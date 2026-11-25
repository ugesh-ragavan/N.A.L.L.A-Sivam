"""app/web/server.py

FastAPI localhost dashboard web server.

Exposes REST APIs, WebSockets, and a localhost web interface for:
  • Memory management (search, category filter, inline edit, delete with confirm, add form, JSON export)
  • Reminders management (view, add, mark completed)
  • Phase 4 Voice Control (live state, push-to-talk, mic mute, test mic/speaker, WebSockets)
  • Settings toggle for memory and voice parameters

Safety rules (CONTEXT.md):
  • Binds strictly to 127.0.0.1 (never 0.0.0.0)
  • Secrets are never displayed in full
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.config import (
    BASE_DIR,
    WEB_HOST,
    WEB_PORT,
    get_voice_profiles,
    save_voice_profiles,
    get_audio_feel,
    save_audio_feel,
    get_stt_settings,
    save_stt_settings,
    get_talk_mode_settings,
    save_talk_mode_settings,
)
from app.brain.persona import (
    persona,
    get_few_shot_examples,
    save_few_shot_examples,
    revert_few_shot_examples,
)
from app.memory.store import store
from app.voice.audio_io import list_input_devices, list_output_devices
from app.voice.pipeline import default_pipeline
from app.voice.tts import get_available_voices_sync

logger = logging.getLogger("nalla.web")


# ---------------------------------------------------------------------------
# Pending Approvals Manager (AMBER / RED Tool Gate)
# ---------------------------------------------------------------------------

class PendingApproval:
    """Represents a tool execution awaiting human confirmation in the web UI."""

    def __init__(self, approval_id: str, description: str, risk: str) -> None:
        self.id = approval_id
        self.description = description
        self.risk = risk
        self.created_at = time.time()
        self.event = threading.Event()
        self.approved: bool = False


class PendingApprovalManager:
    """Thread-safe coordinator for AMBER/RED approvals awaiting web user input."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def create(self, description: str, risk: str) -> PendingApproval:
        import uuid
        approval_id = f"appr-{uuid.uuid4().hex[:8]}"
        item = PendingApproval(approval_id, description, risk)
        with self._lock:
            self._pending[approval_id] = item
        return item

    def resolve(self, approval_id: str, approved: bool) -> bool:
        with self._lock:
            item = self._pending.get(approval_id)
        if item is None:
            return False
        item.approved = approved
        item.event.set()
        return True

    def remove(self, approval_id: str) -> None:
        with self._lock:
            self._pending.pop(approval_id, None)

    def list_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": item.id,
                    "description": item.description,
                    "risk": item.risk,
                    "created_at": item.created_at,
                }
                for item in self._pending.values()
            ]


pending_approvals = PendingApprovalManager()


def web_approval_callback(description: str, risk: Any) -> bool:
    """Approval callback that broadcasts to the web UI and blocks until user approves/denies."""
    risk_val = risk.value if hasattr(risk, "value") else str(risk)
    item = pending_approvals.create(description, risk_val)
    logger.info("Created pending web approval %s [%s]: %s", item.id, risk_val.upper(), description)

    # Broadcast approval_request to all connected WebSocket clients
    ws_manager.broadcast_sync({
        "type": "approval_request",
        "id": item.id,
        "description": description,
        "risk": risk_val,
    })

    # Wait up to 60s for user action in the modal
    done = item.event.wait(timeout=60.0)
    pending_approvals.remove(item.id)
    if not done:
        logger.warning("Web approval %s timed out after 60s.", item.id)
        return False
    return item.approved


def _attach_web_approval_callback() -> None:
    if default_pipeline.agent is not None and hasattr(default_pipeline.agent, "_approvals"):
        default_pipeline.agent._approvals.set_callback(web_approval_callback)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler to initialize agent on startup."""
    if default_pipeline.agent is None:
        try:
            from app.main import _build_agent
            default_pipeline.agent = _build_agent()
            logger.info("Attached Agent to VoicePipeline on startup.")
        except Exception as exc:
            logger.debug("Deferred agent attachment on startup: %s", exc)
    _attach_web_approval_callback()
    yield


app = FastAPI(
    title="NALLA SIVAM Dashboard",
    description="Localhost-only JARVIS control panel, memory management, and voice pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)

_STATIC_PATH = Path(__file__).resolve().parent / "static"
_STATIC_PATH.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_PATH)), name="static")


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class MemoryCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000, description="Memory content")
    category: str = Field("fact", max_length=64, description="Category: preference|fact|project|person|task")
    importance: int = Field(3, ge=1, le=5, description="Importance from 1 to 5")


class MemoryUpdate(BaseModel):
    text: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=64)
    importance: int | None = Field(None, ge=1, le=5)


class ReminderCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000, description="Reminder description")
    due_at: str = Field(..., max_length=64, description="ISO 8601 timestamp (e.g. 2026-11-20T17:00:00)")


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500, description="Task title")
    priority: int = Field(2, ge=1, le=3, description="1 (high), 2 (medium), 3 (low)")
    due_at: str | None = Field(None, max_length=64, description="Optional ISO timestamp")
    project: str | None = Field(None, max_length=128, description="Optional project name")


class TaskReprioritise(BaseModel):
    priority: int = Field(..., ge=1, le=3)


class SettingsUpdate(BaseModel):
    memory_enabled: bool = True
    expressive_tags: bool | None = None
    temperature: float | None = None
    tamil_text_mode: str | None = None
    kongu_touch: int | None = None
    stt_engine: str | None = None
    force_tts_engine: str | None = None
    gemini_tts_model: str | None = None
    gemini_tts_style: str | None = None
    voice_fx: str | None = None
    talk_look: str | None = None
    short_term_exchanges: int | None = None
    short_term_enabled: bool | None = None
    user_timezone: str | None = None
    late_night_start: str | None = None
    late_night_end: str | None = None
    care_nudges: bool | None = None
    nudge_cooldown_minutes: int | None = None
    verified_helpline_numbers: str | None = None
    scenario_engine_enabled: bool | None = None
    scenario_strictness: str | None = None
    scenario_chattiness: str | None = None
    miss_log_enabled: bool | None = None
    thinking_style: str | None = None
    human_speech_polish: bool | None = None


class PersonalityUpdate(BaseModel):
    mode: str = "Friend"
    sarcasm: int = Field(7, ge=0, le=10)
    warmth: int = Field(8, ge=0, le=10)
    tamil_mix: int = Field(40, ge=0, le=100)
    verbosity: str = "very short"


class MuteRequest(BaseModel):
    muted: bool


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    session_id: str = Field("web", max_length=64)


class ApprovalResponse(BaseModel):
    approved: bool


class AmberTestRequest(BaseModel):
    description: str = Field("Toggle Living Room Relay (AMBER risk)", max_length=256)


class TestPersonalityRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    sarcasm: int = Field(7, ge=0, le=10)
    warmth: int = Field(8, ge=0, le=10)
    tamil_mix: int = Field(40, ge=0, le=100)
    verbosity: str = "very short"
    mode: str = "Friend"
    model: str | None = None


class ComparePersonalityRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    sarcasm: int = Field(7, ge=0, le=10)
    warmth: int = Field(8, ge=0, le=10)
    tamil_mix: int = Field(40, ge=0, le=100)
    verbosity: str = "very short"
    mode: str = "Friend"
    model_a: str | None = None
    model_b: str = Field("gemini-2.5-flash", max_length=128)


class FewShotItem(BaseModel):
    user: str
    model: str


class FewShotUpdateRequest(BaseModel):
    examples: list[FewShotItem]


class PersonaRawUpdate(BaseModel):
    text: str


class SnoozeRequest(BaseModel):
    minutes: int = 15


class ClearMemoryRequest(BaseModel):
    confirmation: str


class ModelSettingsUpdate(BaseModel):
    chat_model: str | None = None
    command_model: str | None = None


class ScreenCaptureRequest(BaseModel):
    target: str = "full"
    bbox: list[int] | None = None


class ScreenAnalyzeRequest(BaseModel):
    capture_id: str
    question: str = "What's this error?"
    blur_rectangles: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str = "web"
    remember_session: bool = False


class ScreenCancelRequest(BaseModel):
    capture_id: str


class ScreenSaveRequest(BaseModel):
    capture_id: str
    filename: str | None = None


class PlanCreateRequest(BaseModel):
    query: str
    steps: list[dict[str, Any]]
    spoken_summary: str | None = None
    dry_run: bool | None = None


class PlanApproveRequest(BaseModel):
    action: str = "go"
    modifier: str | None = None


class PlanResolveRedRequest(BaseModel):
    approved: bool


# ---------------------------------------------------------------------------
# Memory 2.0 Models (Prompt 4)
# ---------------------------------------------------------------------------

class EntityCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    type: str = Field("topic", max_length=32)
    aliases: list[str] = Field(default_factory=list)
    summary: str = Field("", max_length=1000)


class EntityUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    aliases: list[str] | None = None
    summary: str | None = None


class FactCreate(BaseModel):
    entity_name: str = Field(..., min_length=1, max_length=128)
    text: str = Field(..., min_length=1, max_length=2000)
    entity_type: str = Field("topic", max_length=32)
    importance: int = Field(2, ge=1, le=5)


class FactUpdate(BaseModel):
    text: str | None = None
    importance: int | None = None
    status: str | None = None


class CandidateProposeRequest(BaseModel):
    entity_name: str = Field(..., min_length=1, max_length=128)
    text: str = Field(..., min_length=1, max_length=2000)
    entity_type: str = Field("topic", max_length=32)


class JournalGenerateRequest(BaseModel):
    date: str | None = None


class JournalUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class ForgetEntityRequest(BaseModel):
    entity_name: str = Field(..., min_length=1, max_length=128)


# ---------------------------------------------------------------------------
# Tamil Voice Lab & Tamil Pipeline Models (Prompts D & E)
# ---------------------------------------------------------------------------

class LabGenerateRequest(BaseModel):
    sentence_id: str
    variant: str = "a_tamil"
    voice_id: str = "ta-IN-PallaviNeural"
    engine: str = "edge-tts"


class LabRateRequest(BaseModel):
    sentence_id: str
    voice_id: str
    variant: str
    engine: str = "edge-tts"
    rating: str  # Good | OK | Bad
    issue_tags: list[str] = []
    flagged_word: str = ""
    note: str = ""


class LabRoundTripRequest(BaseModel):
    clip_filename: str
    expected_text: str


class LabSetVoiceRequest(BaseModel):
    slot: str  # "en" | "ta"
    voice_id: str
    engine: str | None = None


class ABSynthesizeRequest(BaseModel):
    line_id: int
    setting: str = "B"  # "A" | "B"
    fx_mode: str = "jarvis"  # "off" | "clean" | "jarvis"


class ABRateRequest(BaseModel):
    line_id: int
    setting: str  # "A" | "B"
    rating: str  # "good" | "ok" | "bad"
    feedback: str = ""


class TanglishDictUpdate(BaseModel):
    mapping: dict[str, str]


class KonguPhrasesUpdate(BaseModel):
    phrases: list[dict[str, Any]]



class VoiceProfilesUpdate(BaseModel):
    active: str = "Nalla Default"
    profiles: dict[str, Any] = {}


class ActiveVoiceProfileRequest(BaseModel):
    name: str = Field(..., min_length=1)


class AudioFeelUpdate(BaseModel):
    speed_preset: str = "Normal"
    listening_chime: bool = True
    done_chime: bool = True
    chime_volume: int = 80


class STTSettingsUpdate(BaseModel):
    whisper_model: str = "base"
    language: str = "auto"
    listen_languages: list[str] | None = None
    input_device: int | None = None
    sensitivity: float = 400.0


class TalkModeUpdate(BaseModel):
    talk_mode: str = "walkie_talkie"
    keep_mic_warm: bool = False
    global_hotkey_enabled: bool = False
    global_hotkey_key: str = "F9"
    streaming_stt: bool = False
    soft_click_sound: bool = True


class VoicePreviewRequest(BaseModel):
    text: str | None = None
    slot: str | None = "en"
    engine: str | None = None
    voice: str | None = None
    model: str | None = None
    style: str | None = None
    rate: Any = None
    pitch: Any = None
    volume: Any = None


class QuotaCapRequest(BaseModel):
    cap: int = 50


class PronunciationRequest(BaseModel):
    mapping: dict[str, str] = {}
    map: dict[str, str] = {}

    def get_map(self) -> dict[str, str]:
        return self.map or self.mapping or {}


class WakeWordSettingsUpdate(BaseModel):
    engine: str = "openwakeword"
    keyword: str = "hey jarvis"
    sensitivity: float = 0.5
    follow_up_enabled: bool = True
    follow_up_timeout: float = 30.0


# ---------------------------------------------------------------------------
# WebSocket Manager for Voice Events
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages active browser WebSocket connections for real-time voice updates."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.debug("WebSocket client connected. Total: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.debug("WebSocket client disconnected. Total: %d", len(self.active_connections))

    def broadcast_sync(self, message: dict[str, Any]) -> None:
        """Thread-safe event broadcast to all connected WebSockets."""
        if not self.active_connections:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            asyncio.create_task(self._send_all(message))
        else:
            # Called from a background thread
            try:
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(lambda: asyncio.create_task(self._send_all(message)))
            except Exception:
                pass

    async def _send_all(self, message: dict[str, Any]) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


ws_manager = ConnectionManager()
# Subscribe ws_manager to pipeline events
default_pipeline.subscribe(ws_manager.broadcast_sync)


def _on_tts_fallback_broadcast(reason: str) -> None:
    ws_manager.broadcast_sync({"type": "tts_fallback", "reason": reason})


default_pipeline.tts.add_fallback_listener(_on_tts_fallback_broadcast)


# ---------------------------------------------------------------------------
# HTML Dashboard Page
# ---------------------------------------------------------------------------

_HTML_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "index.html"


@app.get("/", response_class=HTMLResponse)
def get_dashboard() -> str:
    """Serve the single-page localhost dashboard."""
    if _HTML_TEMPLATE_PATH.exists():
        return _HTML_TEMPLATE_PATH.read_text(encoding="utf-8")
    return "<h1>NALLA SIVAM Dashboard</h1><p>Template not found.</p>"


@app.websocket("/ws/voice")
async def websocket_voice_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint streaming live voice pipeline events."""
    await ws_manager.connect(websocket)
    try:
        # Send initial state
        await websocket.send_json({
            "type": "state_change",
            "state": default_pipeline.state.value,
            "muted": default_pipeline.is_muted,
        })
        while True:
            # Keep connection alive; process browser client messages
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            else:
                try:
                    payload = json.loads(data)
                    msg_type = payload.get("type")
                    if msg_type == "ptt_down":
                        res = default_pipeline.ptt_down()
                        await websocket.send_json({"type": "ptt_ack", "action": "down", "res": res})
                    elif msg_type == "ptt_up":
                        res = default_pipeline.ptt_up()
                        await websocket.send_json({"type": "ptt_ack", "action": "up", "res": res})
                    elif msg_type == "ptt_cancel":
                        res = default_pipeline.ptt_cancel()
                        await websocket.send_json({"type": "ptt_ack", "action": "cancel", "res": res})
                except Exception as exc:
                    logger.debug("Error processing websocket voice message: %s", exc)
    except WebSocketDisconnect:
        default_pipeline.ptt_cancel()
        ws_manager.disconnect(websocket)
    except Exception as exc:
        default_pipeline.ptt_cancel()
        logger.debug("WebSocket error: %s", exc)
        ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Phase 4: Voice REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/audio/devices")
def get_audio_devices() -> dict[str, Any]:
    """List available microphone and speaker hardware devices."""
    return {
        "input_devices": list_input_devices(),
        "output_devices": list_output_devices(),
    }


@app.post("/api/voice/trigger")
def trigger_voice_turn() -> dict[str, Any]:
    """Trigger push-to-talk voice interaction."""
    default_pipeline.trigger_wakeword()
    return {"ok": True, "state": default_pipeline.state.value}


@app.post("/api/voice/mute")
def toggle_voice_mute(req: MuteRequest) -> dict[str, Any]:
    """Mute or unmute the microphone."""
    default_pipeline.set_muted(req.muted)
    return {"ok": True, "muted": default_pipeline.is_muted}


@app.post("/api/voice/interrupt")
def interrupt_voice() -> dict[str, Any]:
    """Interrupt ongoing speech playback."""
    default_pipeline.interrupt()
    return {"ok": True, "state": default_pipeline.state.value}


@app.get("/api/voice/talk-mode")
def get_talk_mode_endpoint() -> dict[str, Any]:
    """Return current talk mode and walkie-talkie configuration."""
    return {"ok": True, "talk_mode": get_talk_mode_settings()}


@app.post("/api/voice/talk-mode")
def update_talk_mode_endpoint(req: TalkModeUpdate) -> dict[str, Any]:
    """Update talk mode, keep_mic_warm, global hotkey, and streaming STT settings live."""
    dump = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    save_talk_mode_settings(dump)
    # Apply warm stream setting live if toggled
    if req.keep_mic_warm and hasattr(default_pipeline.recorder, "start_warm_stream"):
        default_pipeline.recorder.start_warm_stream()
    elif not req.keep_mic_warm and hasattr(default_pipeline.recorder, "stop_warm_stream"):
        default_pipeline.recorder.stop_warm_stream()
    # Apply hotkey update
    from app.voice.global_hotkey import hotkey_manager
    hotkey_manager.configure(req.global_hotkey_enabled, req.global_hotkey_key)
    return {"ok": True, "talk_mode": get_talk_mode_settings()}


@app.post("/api/voice/ptt/down")
def ptt_down_endpoint() -> dict[str, Any]:
    """REST trigger for PTT down (hold to talk)."""
    return default_pipeline.ptt_down()


@app.post("/api/voice/ptt/up")
def ptt_up_endpoint() -> dict[str, Any]:
    """REST trigger for PTT up (release to send)."""
    return default_pipeline.ptt_up()


@app.post("/api/voice/ptt/cancel")
def ptt_cancel_endpoint() -> dict[str, Any]:
    """REST trigger for PTT cancel."""
    return default_pipeline.ptt_cancel()


@app.post("/api/voice/test-speaker")
def test_speaker() -> dict[str, Any]:
    """Play a test sentence through the active speaker."""
    phrase = "Speaker test: Nalla Sivam voice online."
    import threading
    threading.Thread(target=default_pipeline._speak_phrase, args=(phrase,), daemon=True).start()
    return {"ok": True, "message": "Speaker test initiated."}


@app.post("/api/voice/test-mic")
def test_microphone() -> dict[str, Any]:
    """Record 1.5 seconds of audio to verify microphone input levels."""
    import numpy as np
    audio = default_pipeline.recorder.record_fixed(1.5)
    if not audio:
        return {"ok": False, "message": "No audio captured from microphone."}

    data = np.frombuffer(audio, dtype=np.int16)
    peak = int(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
    return {
        "ok": True,
        "peak": peak,
        "rms": round(rms, 2),
        "message": f"Microphone active (Peak: {peak}, RMS: {rms:.1f})",
    }


# ---------------------------------------------------------------------------
# Voice Studio Management Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/voice/voices")
def get_voice_list() -> dict[str, Any]:
    """Return dynamic voice list categorized by language and engine."""
    return {"ok": True, "voices": get_available_voices_sync()}


@app.get("/api/voice/profiles")
def get_voice_profiles_endpoint() -> dict[str, Any]:
    """Return current voice profiles, active profile, audio feel, and STT config."""
    profiles_cfg = get_voice_profiles()
    return {
        "ok": True,
        "active": profiles_cfg.get("active", "Nalla Default"),
        "profiles": profiles_cfg.get("profiles", {}),
        "audio_feel": get_audio_feel(),
        "stt": get_stt_settings(),
    }


@app.post("/api/voice/profiles")
def update_voice_profiles_endpoint(req: VoiceProfilesUpdate) -> dict[str, Any]:
    """Update voice profiles (up to 5 named profiles) and set active profile."""
    save_voice_profiles(req.dict())
    active_prof = get_voice_profiles().get("active")
    return {"ok": True, "active": active_prof, "profiles": get_voice_profiles().get("profiles", {})}


@app.post("/api/voice/profiles/active")
def set_active_voice_profile_endpoint(req: ActiveVoiceProfileRequest) -> dict[str, Any]:
    """Switch active voice profile live without restart (used by Command Palette & Studio)."""
    profiles_cfg = get_voice_profiles()
    profiles = profiles_cfg.get("profiles", {})
    if req.name not in profiles:
        raise HTTPException(status_code=404, detail=f"Voice profile '{req.name}' not found.")
    profiles_cfg["active"] = req.name
    save_voice_profiles(profiles_cfg)
    logger.info("Switched active voice profile to '%s'", req.name)
    return {
        "ok": True,
        "active": req.name,
        "profile": profiles[req.name],
        "message": f"Active voice profile switched to '{req.name}'.",
    }


# ---------------------------------------------------------------------------
# Tamil Voice Lab Endpoints (Prompt D)
# ---------------------------------------------------------------------------

@app.get("/api/voice/lab/tests")
def get_voice_lab_tests_endpoint(category: str | None = None) -> dict[str, Any]:
    """Return test sentences with their 3 text variants (Tamil script, Tanglish, Dict converted)."""
    from app.voice.voice_lab import get_test_sentences, get_sentence_variants
    sentences = get_test_sentences(category=category)
    enriched = []
    for s in sentences:
        variants = get_sentence_variants(s)
        enriched.append({
            **s,
            "variants": variants,
        })
    return {"ok": True, "sentences": enriched, "count": len(enriched)}


@app.post("/api/voice/lab/generate")
def generate_voice_lab_clip_endpoint(req: LabGenerateRequest) -> dict[str, Any]:
    """Generate or retrieve a cached audio clip for (sentence x variant x voice x engine)."""
    from app.voice.voice_lab import generate_or_get_lab_clip
    result = generate_or_get_lab_clip(
        sentence_id=req.sentence_id,
        variant=req.variant,
        voice_id=req.voice_id,
        engine=req.engine,
    )
    if result.get("error") and not result.get("audio_url"):
        raise HTTPException(status_code=500, detail=result["error"])
    return {"ok": True, **result}


@app.get("/api/voice/lab/audio/{filename}")
def get_voice_lab_audio_file(filename: str):
    """Serve cached audio file from data/voice_lab/."""
    from app.voice.voice_lab import VOICE_LAB_DIR
    file_path = VOICE_LAB_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found in lab cache.")
    media_type = "audio/wav" if filename.endswith(".wav") else "audio/mpeg"
    with open(file_path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/voice/lab/rate")
def rate_voice_lab_cell_endpoint(req: LabRateRequest) -> dict[str, Any]:
    """Record rating, issue tags, and notes for a comparison cell."""
    from app.voice.voice_lab import save_lab_rating
    record = save_lab_rating(
        sentence_id=req.sentence_id,
        voice_id=req.voice_id,
        variant=req.variant,
        engine=req.engine,
        rating=req.rating,
        issue_tags=req.issue_tags,
        flagged_word=req.flagged_word,
        note=req.note,
    )
    return {"ok": True, "record": record}


@app.get("/api/voice/lab/summary")
def get_voice_lab_summary_endpoint() -> dict[str, Any]:
    """Return summary view: category averages, top issue tags, flagged words, and voice recommendations."""
    from app.voice.voice_lab import compute_lab_summary
    summary = compute_lab_summary()
    return {"ok": True, **summary}


@app.get("/api/voice/lab/export")
def export_voice_lab_csv_endpoint():
    """Export comparison grid ratings as a downloadable CSV."""
    from app.voice.voice_lab import export_lab_results_csv
    csv_data = export_lab_results_csv()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=nalla_voice_lab_results.csv",
        },
    )


@app.post("/api/voice/lab/round-trip")
def round_trip_check_endpoint(req: LabRoundTripRequest) -> dict[str, Any]:
    """Transcribe generated clip back with STT and compute Character Error Rate (CER)."""
    from app.voice.voice_lab import run_round_trip_check
    res = run_round_trip_check(
        clip_filename=req.clip_filename,
        expected_text=req.expected_text,
    )
    if res.get("error"):
        raise HTTPException(status_code=500, detail=res["error"])
    return {"ok": True, **res}


@app.post("/api/voice/lab/set-voice")
def set_lab_recommended_voice_endpoint(req: LabSetVoiceRequest) -> dict[str, Any]:
    """Set the chosen voice from the Voice Lab onto the active profile without restarting."""
    profiles_cfg = get_voice_profiles()
    active_name = profiles_cfg.get("active", "Nalla Default")
    profiles = profiles_cfg.get("profiles", {})
    if active_name not in profiles:
        active_name = "Nalla Default"
        if active_name not in profiles:
            profiles[active_name] = {}

    prof = profiles[active_name]
    slot_key = "voice_slot_ta" if req.slot.lower() == "ta" else "voice_slot_en"
    if slot_key not in prof:
        prof[slot_key] = {}

    prof[slot_key]["voice"] = req.voice_id
    if req.engine:
        prof[slot_key]["engine"] = req.engine
    elif "-" in req.voice_id:
        prof[slot_key]["engine"] = "edge-tts"
    else:
        prof[slot_key]["engine"] = "gemini"

    # Also sync top-level legacy fields
    if req.slot.lower() == "ta":
        prof["voice_ta"] = req.voice_id
    else:
        prof["voice"] = req.voice_id
        if req.engine:
            prof["engine"] = req.engine

    save_voice_profiles(profiles_cfg)
    logger.info("Voice Lab set %s voice to '%s' (%s) live on active profile '%s'",
                req.slot, req.voice_id, prof[slot_key]["engine"], active_name)
    return {"ok": True, "active": active_name, "slot": req.slot, "updated_profile": prof}


# ---------------------------------------------------------------------------
# Voice Quality A/B Benchmark Endpoints (Step 8)
# ---------------------------------------------------------------------------

@app.get("/api/voice/ab/lines")
def get_ab_lines_endpoint() -> dict[str, Any]:
    """Return the 10 standardized A/B benchmark evaluation lines."""
    from app.voice.ab_benchmark import get_benchmark_lines
    return {"ok": True, "lines": get_benchmark_lines()}


@app.post("/api/voice/ab/synthesize")
def synthesize_ab_endpoint(req: ABSynthesizeRequest) -> dict[str, Any]:
    """Synthesize Setting A (Old) or Setting B (New) for a benchmark line."""
    from app.voice.ab_benchmark import synthesize_ab_sample
    try:
        _, filename = synthesize_ab_sample(
            line_id=req.line_id,
            setting=req.setting,
            fx_mode=req.fx_mode,
        )
        return {
            "ok": True,
            "filename": filename,
            "audio_url": f"/api/voice/ab/audio/{filename}",
            "setting": req.setting,
            "line_id": req.line_id,
        }
    except Exception as exc:
        logger.error("A/B sample synthesis error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/voice/ab/audio/{filename}")
def get_ab_audio_endpoint(filename: str):
    """Serve cached A/B benchmark WAV audio file."""
    from app.voice.ab_benchmark import SAMPLES_DIR
    file_path = SAMPLES_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Benchmark audio sample not found.")
    with open(file_path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="audio/wav", headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/voice/ab/rate")
def rate_ab_endpoint(req: ABRateRequest) -> dict[str, Any]:
    """Save Good / OK / Bad rating for Setting A or Setting B."""
    from app.voice.ab_benchmark import record_ab_rating
    try:
        result = record_ab_rating(
            line_id=req.line_id,
            setting=req.setting,
            rating=req.rating,
            feedback=req.feedback,
        )
        return {"ok": True, **result}
    except Exception as exc:
        logger.error("Error saving A/B rating: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/voice/ab/results")
def get_ab_results_endpoint() -> dict[str, Any]:
    """Return all persisted A/B ratings and summary stats."""
    from app.voice.ab_benchmark import get_ab_results
    return {"ok": True, **get_ab_results()}


# ---------------------------------------------------------------------------
# Tanglish Dictionary & Kongu Phrases Endpoints (Prompt E)
# ---------------------------------------------------------------------------

@app.get("/api/voice/tanglish-dict")
def get_tanglish_dict_endpoint() -> dict[str, Any]:
    """Return Tanglish to Tamil script pronunciation dictionary."""
    from app.voice.voice_lab import get_tanglish_dict
    return {"ok": True, "mapping": get_tanglish_dict()}


@app.post("/api/voice/tanglish-dict")
def save_tanglish_dict_endpoint(req: TanglishDictUpdate) -> dict[str, Any]:
    """Save updated Tanglish pronunciation dictionary live."""
    from app.voice.voice_lab import save_tanglish_dict, clear_lab_cache
    ok = save_tanglish_dict(req.mapping)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to persist Tanglish dictionary.")
    # Invalidate cached variant 'c' clips so re-renders use updated dictionary
    clear_lab_cache()
    return {"ok": True, "mapping": req.mapping, "message": "Dictionary saved live."}


@app.get("/api/voice/kongu-phrases")
def get_kongu_phrases_endpoint() -> dict[str, Any]:
    """Return curated Kongu dialect expressions list."""
    from app.voice.voice_lab import get_kongu_phrases
    return {"ok": True, "phrases": get_kongu_phrases()}


@app.post("/api/voice/kongu-phrases")
def save_kongu_phrases_endpoint(req: KonguPhrasesUpdate) -> dict[str, Any]:
    """Save curated Kongu expressions list live."""
    from app.voice.voice_lab import save_kongu_phrases
    ok = save_kongu_phrases(req.phrases)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to persist Kongu phrases.")
    return {"ok": True, "phrases": req.phrases, "message": "Kongu phrases saved live."}


@app.post("/api/voice/audio-feel")
def update_audio_feel_endpoint(req: AudioFeelUpdate) -> dict[str, Any]:
    """Update audio feel settings (speed preset and chimes)."""
    save_audio_feel(req.dict())
    return {"ok": True, "audio_feel": get_audio_feel()}


@app.post("/api/voice/stt-settings")
def update_stt_settings_endpoint(req: STTSettingsUpdate) -> dict[str, Any]:
    """Update STT model, language, input device, and sensitivity live."""
    dump = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    save_stt_settings(dump)
    if hasattr(default_pipeline.vad, "energy_threshold"):
        default_pipeline.vad.energy_threshold = req.sensitivity
    # Switch STT model live if requested
    if hasattr(default_pipeline.stt, "model_size") and req.whisper_model != default_pipeline.stt.model_size:
        default_pipeline.stt.model_size = req.whisper_model
        if hasattr(default_pipeline.stt, "_load_model"):
            default_pipeline.stt._load_model()
    return {"ok": True, "stt": get_stt_settings()}


@app.get("/api/voice/wakeword")
def get_wakeword_endpoint() -> dict[str, Any]:
    """Get wake word and follow-up configuration."""
    from app.config import get_wakeword_settings
    return {"ok": True, "wakeword": get_wakeword_settings()}


@app.post("/api/voice/wakeword")
def update_wakeword_endpoint(req: WakeWordSettingsUpdate) -> dict[str, Any]:
    """Update wake word engine, keyword, sensitivity, and follow-up settings."""
    from app.voice.wakeword import update_wakeword_settings
    from app.config import get_wakeword_settings

    update_wakeword_settings(
        engine=req.engine,
        keyword=req.keyword,
        sensitivity=req.sensitivity,
    )
    default_pipeline.follow_up_enabled = req.follow_up_enabled
    default_pipeline.follow_up_timeout = req.follow_up_timeout
    default_pipeline.reload_wakeword_detector(req.engine)

    return {"ok": True, "wakeword": get_wakeword_settings()}


@app.post("/api/voice/preview")
def preview_voice_endpoint(req: VoicePreviewRequest):
    """Generate an instant speech preview sample in the slot's language and return playable audio."""
    slot = (req.slot or "en").lower()
    default_text = "வணக்கம், நான் நல்ல சிவம். உங்களுக்கு எப்படி உதவ வேண்டும்?" if slot == "ta" else "Yes boss, sollunga!"
    phrase = req.text or default_text
    try:
        audio_bytes, mime_type = default_pipeline.tts.preview(
            slot=slot,
            text=phrase,
            engine=req.engine,
            voice=req.voice,
            style=req.style,
            rate=req.rate,
            pitch=req.pitch,
            volume=req.volume,
        )
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="Voice preview generation produced empty audio.")
        return Response(
            content=audio_bytes,
            media_type=mime_type,
            headers={
                "Content-Disposition": "inline; filename=preview.audio",
                "Cache-Control": "no-cache",
            },
        )
    except Exception as exc:
        logger.error("Preview endpoint failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Voice preview failed: {exc}") from exc


@app.get("/api/voice/last-engines")
def get_last_turn_engines_endpoint() -> dict[str, Any]:
    """Return telemetry of which engine and voice actually spoke each sentence in the last turn."""
    engines = []
    if hasattr(default_pipeline.tts, "get_last_turn_engines"):
        engines = default_pipeline.tts.get_last_turn_engines()
    return {"ok": True, "engines": engines}


@app.get("/api/voice/status")
def get_voice_status() -> dict[str, Any]:
    """Return runtime voice status, active engine, fallback reason, and Gemini quota metrics."""
    from app.voice.quota_guard import quota_guard
    from app.voice.tts_gemini import PREBUILT_VOICES, gemini_tts
    from app.config import get_active_voice_profile
    active = get_active_voice_profile()
    is_gemini_avail = gemini_tts.is_available() if gemini_tts else False
    return {
        "ok": True,
        "status": "online" if is_gemini_avail else "fallback",
        "primary_engine": active.get("engine", config.TTS_ENGINE),
        "active_profile": config.VOICE_PROFILES.get("active", "Nalla Default"),
        "fallback_reason": getattr(default_pipeline.tts, "last_fallback_reason", ""),
        "quota": quota_guard.get_status(),
        "prebuilt_voices": PREBUILT_VOICES,
    }


@app.post("/api/voice/quota/cap")
def update_quota_cap(req: QuotaCapRequest) -> dict[str, Any]:
    """Set Gemini TTS daily request limit (0 for unlimited)."""
    from app.voice.quota_guard import quota_guard
    quota_guard.set_cap(req.cap)
    return {"ok": True, "cap": req.cap, "quota": quota_guard.get_status()}


@app.post("/api/voice/quota/reset")
def reset_quota_counter() -> dict[str, Any]:
    """Reset today's Gemini TTS request counter for testing."""
    from app.voice.quota_guard import quota_guard
    quota_guard.reset()
    return {"ok": True, "quota": quota_guard.get_status()}


@app.get("/api/voice/pronunciation")
def get_pronunciation_map_endpoint() -> dict[str, Any]:
    """Return current Tamil phonetic pronunciation mapping."""
    from app.voice.pronunciation import load_pronunciation_map
    m = load_pronunciation_map()
    return {"ok": True, "mapping": m, "map": m}


@app.post("/api/voice/pronunciation")
def update_pronunciation_map_endpoint(req: PronunciationRequest) -> dict[str, Any]:
    """Save updated Tamil phonetic pronunciation mapping."""
    from app.voice.pronunciation import save_pronunciation_map
    m = req.get_map()
    save_pronunciation_map(m)
    return {"ok": True, "mapping": m, "map": m}


@app.get("/api/voice/extended-library")
def get_extended_library_endpoint(
    language_code: str = "",
    gender: str = "",
    pitch: str = "",
    contexts: str = "",
    search: str = "",
) -> dict[str, Any]:
    """Browse Google Extended Voice Library using client.voices.list."""
    lang_list = [l.strip() for l in language_code.split(",") if l.strip()] or None
    gender_list = [g.strip() for g in gender.split(",") if g.strip()] or None
    pitch_list = [p.strip() for p in pitch.split(",") if p.strip()] or None
    context_list = [c.strip() for c in contexts.split(",") if c.strip()] or None
    try:
        gemini = default_pipeline.tts.gemini
        if not gemini:
            from app.voice.tts_gemini import gemini_tts
            gemini = gemini_tts
        voices = gemini.list_extended_voices(
            language_code=lang_list,
            gender=gender_list,
            pitch=pitch_list,
            contexts=context_list,
            search=search.strip() or None,
        )
        return {"ok": True, "voices": voices}
    except Exception as exc:
        logger.warning("Extended voice library error: %s", exc)
        return {"ok": False, "error": str(exc), "voices": []}


# ---------------------------------------------------------------------------
# Memory API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/memories")
def list_or_search_memories(
    q: str = "",
    category: str = "",
    importance: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List or search memories with optional category and importance filter."""
    if q.strip():
        results = store.search_memories(q.strip(), limit=limit)
    else:
        cat = category.strip() if category.strip() else None
        results = store.list_memories(category=cat, limit=limit)

    if category.strip():
        results = [m for m in results if m.get("category") == category.strip()]
    if importance is not None:
        results = [m for m in results if m.get("importance") == importance]
    return results


@app.get("/api/memories/last-used")
def get_last_used_memories() -> dict[str, Any]:
    """Return memories recalled and injected in the last conversation turn."""
    memories = []
    if default_pipeline.agent is not None:
        memories = getattr(default_pipeline.agent, "last_recalled_memories", []) or []
    return {"ok": True, "count": len(memories), "memories": memories}


@app.post("/api/memories", status_code=201)
def create_memory(item: MemoryCreate) -> dict[str, Any]:
    """Create a new memory."""
    try:
        mid = store.save_memory(
            text=item.text,
            category=item.category,
            importance=item.importance,
            source="user_said",
        )
        return {"ok": True, "id": mid, "message": f"Memory #{mid} saved."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/memories/{memory_id}")
def update_memory_item(memory_id: int, item: MemoryUpdate) -> dict[str, Any]:
    """Inline update of an existing memory."""
    try:
        updated = store.update_memory(
            memory_id=memory_id,
            text=item.text,
            category=item.category,
            importance=item.importance,
        )
        if not updated:
            raise HTTPException(status_code=404, detail=f"Memory #{memory_id} not found.")
        return {"ok": True, "message": f"Memory #{memory_id} updated."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/memories/{memory_id}")
def delete_memory_item(memory_id: int) -> dict[str, Any]:
    """Hard delete a memory row from SQLite and FTS5."""
    deleted = store.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Memory #{memory_id} not found.")
    return {"ok": True, "message": f"Memory #{memory_id} permanently deleted."}


@app.get("/api/memories/export")
def export_memories() -> Response:
    """Export all memories as JSON."""
    json_data = store.export_memories_json()
    return Response(
        content=json_data,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=nalla_memories.json"},
    )


# ---------------------------------------------------------------------------
# Memory 2.0 Endpoints (Prompt 4)
# ---------------------------------------------------------------------------

from app.memory.memory_v2 import memory_v2


@app.get("/api/memory/entities")
def list_entities_endpoint(type: str = "") -> list[dict[str, Any]]:
    """List entities, optionally filtered by type."""
    return memory_v2.list_entities(type=type.strip() or None)


@app.post("/api/memory/entities", status_code=201)
def create_entity_endpoint(item: EntityCreate) -> dict[str, Any]:
    """Create or retrieve entity."""
    return memory_v2.get_or_create_entity(
        name=item.name,
        type=item.type,
        aliases=item.aliases,
        summary=item.summary,
    )


@app.get("/api/memory/entities/{entity_id}")
def get_entity_endpoint(entity_id: int) -> dict[str, Any]:
    """Get entity details."""
    ent = memory_v2.get_entity(entity_id)
    if not ent:
        raise HTTPException(status_code=404, detail="Entity not found.")
    return ent


@app.put("/api/memory/entities/{entity_id}")
def update_entity_endpoint(entity_id: int, item: EntityUpdate) -> dict[str, Any]:
    """Update entity."""
    ok = memory_v2.update_entity(
        entity_id=entity_id,
        name=item.name,
        type=item.type,
        aliases=item.aliases,
        summary=item.summary,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Entity not found or no changes made.")
    return {"ok": True, "message": f"Entity #{entity_id} updated."}


@app.delete("/api/memory/entities/{entity_id}")
def delete_entity_endpoint(entity_id: int) -> dict[str, Any]:
    """Delete entity and its facts."""
    ok = memory_v2.delete_entity(entity_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Entity not found.")
    return {"ok": True, "message": f"Entity #{entity_id} deleted."}


@app.get("/api/memory/facts")
def list_facts_endpoint(entity_id: int | None = None, status: str = "active", limit: int = 50) -> list[dict[str, Any]]:
    """List facts filtered by entity and status."""
    return memory_v2.list_facts(entity_id=entity_id, status=status, limit=limit)


@app.post("/api/memory/facts", status_code=201)
def create_fact_endpoint(item: FactCreate) -> dict[str, Any]:
    """Add a verified fact for an entity."""
    try:
        fact = memory_v2.add_fact(
            entity_name=item.entity_name,
            text=item.text,
            entity_type=item.entity_type,
            importance=item.importance,
        )
        return {"ok": True, "fact": fact}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/memory/facts/search")
def search_facts_endpoint(q: str = "", limit: int = 5) -> dict[str, Any]:
    """Search facts using hybrid FTS5 + importance + recency decay ranking."""
    results = memory_v2.search_facts_hybrid(query=q, limit=limit)
    return {"ok": True, "count": len(results), "facts": results}


@app.put("/api/memory/facts/{fact_id}")
def update_fact_endpoint(fact_id: int, item: FactUpdate) -> dict[str, Any]:
    """Update fact."""
    try:
        ok = memory_v2.update_fact(
            fact_id=fact_id,
            text=item.text,
            importance=item.importance,
            status=item.status,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Fact not found.")
        return {"ok": True, "message": f"Fact #{fact_id} updated."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/memory/facts/{fact_id}")
def delete_fact_endpoint(fact_id: int) -> dict[str, Any]:
    """Delete fact."""
    ok = memory_v2.delete_fact(fact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Fact not found.")
    return {"ok": True, "message": f"Fact #{fact_id} deleted."}


@app.get("/api/memory/candidates")
def list_candidates_endpoint(status: str = "pending") -> list[dict[str, Any]]:
    """List candidate memories in inbox."""
    return memory_v2.list_candidates(status=status)


@app.post("/api/memory/candidates/propose", status_code=201)
def propose_candidate_endpoint(item: CandidateProposeRequest) -> dict[str, Any]:
    """Propose candidate fact to inbox."""
    res = memory_v2.propose_candidate(
        entity_name=item.entity_name,
        text=item.text,
        entity_type=item.entity_type,
    )
    if not res.get("ok", True):
        raise HTTPException(status_code=400, detail=res.get("message", "Fact rejected."))
    return {"ok": True, "candidate": res}


@app.post("/api/memory/candidates/{candidate_id}/approve")
def approve_candidate_endpoint(candidate_id: int) -> dict[str, Any]:
    """Approve candidate memory into active fact."""
    try:
        res = memory_v2.approve_candidate(candidate_id)
        return res
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/memory/candidates/{candidate_id}/reject")
def reject_candidate_endpoint(candidate_id: int) -> dict[str, Any]:
    """Reject candidate memory."""
    ok = memory_v2.reject_candidate(candidate_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return {"ok": True, "message": f"Candidate #{candidate_id} rejected."}


@app.get("/api/memory/review/unconfirmed")
def get_unconfirmed_review_endpoint(days: int = 30) -> list[dict[str, Any]]:
    """Weekly Review: get facts not confirmed for 30+ days."""
    return memory_v2.get_unconfirmed_facts(days=days)


@app.post("/api/memory/facts/{fact_id}/confirm")
def confirm_fact_endpoint(fact_id: int) -> dict[str, Any]:
    """Confirm fact is still true."""
    ok = memory_v2.confirm_fact(fact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Fact not found.")
    return {"ok": True, "message": f"Fact #{fact_id} confirmed as still true."}


@app.get("/api/memory/journals")
def list_journals_endpoint(limit: int = 30) -> list[dict[str, Any]]:
    """List daily journals."""
    return memory_v2.list_journals(limit=limit)


@app.get("/api/memory/journals/{date_str}")
def get_journal_endpoint(date_str: str) -> dict[str, Any]:
    """Get single daily journal."""
    j = memory_v2.get_journal(date_str)
    if not j:
        raise HTTPException(status_code=404, detail="Journal entry not found.")
    return j


@app.post("/api/memory/journals/generate")
def generate_journal_endpoint(req: JournalGenerateRequest | None = None) -> dict[str, Any]:
    """Generate daily journal locally from tasks and notes."""
    dt = req.date if req else None
    res = memory_v2.generate_daily_journal(dt)
    return {"ok": True, "journal": res}


@app.put("/api/memory/journals/{date_str}")
def update_journal_endpoint(date_str: str, req: JournalUpdateRequest) -> dict[str, Any]:
    """Update journal content."""
    ok = memory_v2.update_journal(date_str, req.content)
    if not ok:
        raise HTTPException(status_code=404, detail="Journal entry not found.")
    return {"ok": True, "message": f"Journal for {date_str} updated."}


@app.delete("/api/memory/journals/{date_str}")
def delete_journal_endpoint(date_str: str) -> dict[str, Any]:
    """Delete journal."""
    ok = memory_v2.delete_journal(date_str)
    if not ok:
        raise HTTPException(status_code=404, detail="Journal entry not found.")
    return {"ok": True, "message": f"Journal for {date_str} deleted."}


@app.post("/api/memory/forget/entity")
def forget_entity_api_endpoint(req: ForgetEntityRequest) -> dict[str, Any]:
    """Forget everything about entity."""
    return memory_v2.forget_everything_about(req.entity_name)


@app.post("/api/memory/forget/last")
def forget_last_fact_api_endpoint() -> dict[str, Any]:
    """Forget the last fact added."""
    res = memory_v2.forget_last_fact()
    if not res:
        return {"ok": False, "message": "No recent fact found to forget."}
    return {"ok": True, "forgotten": res, "message": f"Forgot that {res['entity_name']} {res['text']}."}


@app.get("/api/memory/export/v2")
def export_memory_v2_endpoint() -> Response:
    """Full JSON export of Memory 2.0."""
    json_data = memory_v2.export_memory_2_0_json()
    return Response(
        content=json_data,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=nalla_memory_2.0.json"},
    )



# ---------------------------------------------------------------------------
# Reminders API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/reminders")
def list_reminders(include_done: bool = True) -> list[dict[str, Any]]:
    """List reminders."""
    return store.list_reminders(include_done=include_done)


@app.post("/api/reminders", status_code=201)
def create_reminder(item: ReminderCreate) -> dict[str, Any]:
    """Create a new scheduled reminder."""
    try:
        rid = store.save_reminder(text=item.text, due_at=item.due_at)
        return {"ok": True, "id": rid, "message": f"Reminder #{rid} scheduled."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/reminders/{reminder_id}/done")
def mark_reminder_completed(reminder_id: int) -> dict[str, Any]:
    """Mark a reminder as completed."""
    done = store.mark_reminder_done(reminder_id)
    if not done:
        raise HTTPException(status_code=404, detail=f"Reminder #{reminder_id} not found.")
    return {"ok": True, "message": f"Reminder #{reminder_id} marked as done."}


# ---------------------------------------------------------------------------
# Tasks API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/tasks")
def list_tasks_endpoint(status: str = "open", project: str | None = None) -> list[dict[str, Any]]:
    """List tasks ordered by priority then due date."""
    return store.list_tasks(status=status, project=project)


@app.post("/api/tasks", status_code=201)
def create_task_endpoint(item: TaskCreate) -> dict[str, Any]:
    """Create a new task."""
    try:
        tid = store.add_task(title=item.title, priority=item.priority, due_at=item.due_at, project=item.project)
        return {"ok": True, "id": tid, "message": f"Task #{tid} added."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/tasks/next")
def get_next_task_endpoint() -> dict[str, Any]:
    """Get the highest priority open task."""
    task = store.next_task()
    return {"ok": True, "task": task}


@app.patch("/api/tasks/{task_id}/done")
def mark_task_done_endpoint(task_id: int) -> dict[str, Any]:
    """Mark task as done."""
    ok = store.complete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Task #{task_id} not found.")
    return {"ok": True, "message": f"Task #{task_id} completed."}


@app.patch("/api/tasks/{task_id}/priority")
def reprioritise_task_endpoint(task_id: int, item: TaskReprioritise) -> dict[str, Any]:
    """Change priority of task."""
    try:
        ok = store.reprioritise_task(task_id, item.priority)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Task #{task_id} not found.")
        return {"ok": True, "message": f"Task #{task_id} priority set to {item.priority}."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/tasks/{task_id}")
def delete_task_endpoint(task_id: int) -> dict[str, Any]:
    """Delete a task."""
    ok = store.delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Task #{task_id} not found.")
    return {"ok": True, "message": f"Task #{task_id} deleted."}


# ---------------------------------------------------------------------------
# Short-Term Memory & Context Transparency Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/short-term/context")
def get_short_term_context(session_id: str = "web") -> dict[str, Any]:
    """Return the exact short-term context and messages sent to Gemini."""
    from app.brain.context_builder import build_context_block
    ctx = build_context_block(session_id=session_id)
    n_exchanges = getattr(config, "SHORT_TERM_EXCHANGES", 5)
    recent_msgs = store.get_recent_exchanges(session_id, n_exchanges=n_exchanges)
    summary_info = store.get_session_summary(session_id)
    return {
        "ok": True,
        "session_id": session_id,
        "short_term_enabled": getattr(config, "SHORT_TERM_ENABLED", True),
        "short_term_exchanges": n_exchanges,
        "context_block": ctx.get("block_text", ""),
        "time_band": ctx.get("time_band", ""),
        "messages": recent_msgs,
        "message_count": len(recent_msgs),
        "summary": summary_info.get("summary") if summary_info else None,
    }


@app.delete("/api/short-term/last-exchange")
def forget_last_exchange_endpoint(session_id: str = "web") -> dict[str, Any]:
    """Forget the last exchange (assistant + user turn) from history."""
    deleted_count = store.delete_last_exchange(session_id)
    return {"ok": True, "deleted": deleted_count, "message": "Last exchange removed from memory."}


@app.delete("/api/short-term/clear")
def clear_short_term_endpoint(session_id: str = "web") -> dict[str, Any]:
    """Clear short-term memory for session."""
    count = store.clear_short_term_memory(session_id)
    return {"ok": True, "deleted": count, "message": "Short-term memory cleared."}



# ---------------------------------------------------------------------------
# Settings & Personality API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    """Return runtime settings."""
    return {
        "memory_enabled": config.MEMORY_ENABLED,
        "expressive_tags": config.EXPRESSIVE_TAGS,
        "temperature": config.GEMINI_TEMPERATURE,
        "model": config.GEMINI_MODEL,
        "chat_model": config.GEMINI_CHAT_MODEL,
        "command_model": config.GEMINI_COMMAND_MODEL,
        "host": config.WEB_HOST,
        "port": config.WEB_PORT,
        "whisper_model": config.WHISPER_MODEL,
        "whisper_language": config.WHISPER_LANGUAGE,
        "tts_engine": config.TTS_ENGINE,
        "tts_voice": config.TTS_VOICE,
        "tts_voice_ta": config.TTS_VOICE_TA,
        "wake_word_engine": config.WAKE_WORD_ENGINE,
        "personality": {
            "mode": config.PERSONALITY_MODE,
            "sarcasm": config.PERSONALITY_SARCASM,
            "warmth": config.PERSONALITY_WARMTH,
            "tamil_mix": config.PERSONALITY_TAMIL_MIX,
            "verbosity": config.PERSONALITY_VERBOSITY,
            "prompt_line": config.get_personality_prompt_line(),
        },
        "tamil_text_mode": config.TAMIL_TEXT_MODE,
        "kongu_touch": config.KONGU_TOUCH,
        "stt_engine": config.STT_ENGINE,
        "force_tts_engine": config.FORCE_TTS_ENGINE,
        "gemini_tts_model": config.GEMINI_TTS_MODEL,
        "gemini_tts_style": config.GEMINI_TTS_STYLE,
        "voice_fx": config.VOICE_FX,
        "talk_look": config.TALK_LOOK,
        "short_term_exchanges": getattr(config, "SHORT_TERM_EXCHANGES", 5),
        "short_term_enabled": getattr(config, "SHORT_TERM_ENABLED", True),
        "user_timezone": getattr(config, "USER_TIMEZONE", "system"),
        "late_night_start": getattr(config, "LATE_NIGHT_START", "00:30"),
        "late_night_end": getattr(config, "LATE_NIGHT_END", "05:00"),
        "care_nudges": getattr(config, "CARE_NUDGES", True),
        "nudge_cooldown_minutes": getattr(config, "NUDGE_COOLDOWN_MINUTES", 60),
    }


@app.post("/api/settings")
def update_settings(item: SettingsUpdate) -> dict[str, Any]:
    """Update general settings."""
    config.MEMORY_ENABLED = item.memory_enabled
    if item.expressive_tags is not None:
        config.EXPRESSIVE_TAGS = item.expressive_tags
    if item.temperature is not None:
        config.GEMINI_TEMPERATURE = item.temperature
    if item.tamil_text_mode is not None:
        config.TAMIL_TEXT_MODE = item.tamil_text_mode
    if item.kongu_touch is not None:
        config.KONGU_TOUCH = item.kongu_touch
    if item.stt_engine is not None:
        config.STT_ENGINE = item.stt_engine
    if item.force_tts_engine is not None:
        config.FORCE_TTS_ENGINE = item.force_tts_engine
    if item.gemini_tts_model is not None:
        config.GEMINI_TTS_MODEL = item.gemini_tts_model
    if item.gemini_tts_style is not None:
        config.GEMINI_TTS_STYLE = item.gemini_tts_style
    if item.voice_fx is not None:
        config.VOICE_FX = item.voice_fx
    if item.talk_look is not None:
        config.TALK_LOOK = item.talk_look.lower().strip()
    if item.short_term_exchanges is not None:
        config.SHORT_TERM_EXCHANGES = max(1, min(10, item.short_term_exchanges))
    if item.short_term_enabled is not None:
        config.SHORT_TERM_ENABLED = item.short_term_enabled
    if item.user_timezone is not None:
        config.USER_TIMEZONE = item.user_timezone
    if item.late_night_start is not None:
        config.LATE_NIGHT_START = item.late_night_start
    if item.late_night_end is not None:
        config.LATE_NIGHT_END = item.late_night_end
    if item.care_nudges is not None:
        config.CARE_NUDGES = item.care_nudges
    if item.nudge_cooldown_minutes is not None:
        config.NUDGE_COOLDOWN_MINUTES = item.nudge_cooldown_minutes
    if item.verified_helpline_numbers is not None:
        config.VERIFIED_HELPLINE_NUMBERS = item.verified_helpline_numbers.strip()
    if item.scenario_engine_enabled is not None:
        config.SCENARIO_ENGINE_ENABLED = item.scenario_engine_enabled
    if item.scenario_strictness is not None and item.scenario_strictness in ("strict", "balanced", "loose"):
        config.SCENARIO_STRICTNESS = item.scenario_strictness
    if item.scenario_chattiness is not None and item.scenario_chattiness in ("rarely", "sometimes", "often"):
        config.SCENARIO_CHATTINESS = item.scenario_chattiness
    if item.miss_log_enabled is not None:
        config.MISS_LOG_ENABLED = item.miss_log_enabled
    if item.thinking_style is not None and item.thinking_style in ("off", "subtle", "natural", "chatty"):
        config.THINKING_STYLE = item.thinking_style
    if item.human_speech_polish is not None:
        config.HUMAN_SPEECH_POLISH = item.human_speech_polish
    config.save_persisted_settings()
    logger.info("Updated settings: memory_enabled=%s, expressive_tags=%s, force_tts_engine=%s, talk_look=%s",
                config.MEMORY_ENABLED, config.EXPRESSIVE_TAGS, config.FORCE_TTS_ENGINE, config.TALK_LOOK)
    return {
        "ok": True,
        "memory_enabled": config.MEMORY_ENABLED,
        "expressive_tags": config.EXPRESSIVE_TAGS,
        "temperature": config.GEMINI_TEMPERATURE,
        "tamil_text_mode": config.TAMIL_TEXT_MODE,
        "kongu_touch": config.KONGU_TOUCH,
        "stt_engine": config.STT_ENGINE,
        "force_tts_engine": config.FORCE_TTS_ENGINE,
        "gemini_tts_model": config.GEMINI_TTS_MODEL,
        "gemini_tts_style": config.GEMINI_TTS_STYLE,
        "voice_fx": config.VOICE_FX,
        "talk_look": config.TALK_LOOK,
        "voice_fx": config.VOICE_FX,
    }


@app.get("/api/personality")
def get_personality() -> dict[str, Any]:
    """Return current personality configuration and available preset modes."""
    return {
        "mode": config.PERSONALITY_MODE,
        "sarcasm": config.PERSONALITY_SARCASM,
        "warmth": config.PERSONALITY_WARMTH,
        "tamil_mix": config.PERSONALITY_TAMIL_MIX,
        "verbosity": config.PERSONALITY_VERBOSITY,
        "prompt_line": config.get_personality_prompt_line(),
        "modes": config.PERSONALITY_MODES,
        "coach_mode": getattr(config, "COACH_MODE", False),
    }


@app.post("/api/personality")
def update_personality(item: PersonalityUpdate) -> dict[str, Any]:
    """Update personality sliders and mode."""
    config.PERSONALITY_MODE = item.mode
    config.PERSONALITY_SARCASM = item.sarcasm
    config.PERSONALITY_WARMTH = item.warmth
    config.PERSONALITY_TAMIL_MIX = item.tamil_mix
    config.PERSONALITY_VERBOSITY = item.verbosity
    config.save_persisted_settings()
    prompt_line = config.get_personality_prompt_line()
    logger.info("Updated personality: %s", prompt_line)
    return {
        "ok": True,
        "mode": config.PERSONALITY_MODE,
        "sarcasm": config.PERSONALITY_SARCASM,
        "warmth": config.PERSONALITY_WARMTH,
        "tamil_mix": config.PERSONALITY_TAMIL_MIX,
        "verbosity": config.PERSONALITY_VERBOSITY,
        "prompt_line": prompt_line,
    }


# ---------------------------------------------------------------------------
# Performance Telemetry API
# ---------------------------------------------------------------------------

@app.get("/api/perf")
def get_perf_records() -> dict[str, Any]:
    """Return per-turn timing records and an ASCII summary table."""
    from app.perf import perf
    return {
        "records": perf.records(),
        "summary": perf.summary_table(),
    }


@app.delete("/api/perf")
def clear_perf_records() -> dict[str, Any]:
    """Clear the performance ring-buffer (for before/after comparisons)."""
    from app.perf import perf
    perf._records.clear()
    perf._counter = 0
    return {"ok": True, "message": "Performance records cleared."}


# ---------------------------------------------------------------------------
# Phase 4 & Redesign: Chat, Persona, IoT, and Activity APIs
# ---------------------------------------------------------------------------

def _resolve_session_id(session_id: str) -> str:
    """Check if more than 6 hours have passed since the last message; if so, create a new session suffix."""
    from datetime import datetime, timezone
    last_dt = store.get_last_message_timestamp(session_id)
    if last_dt is not None:
        now_utc = datetime.now(timezone.utc)
        hours_gap = (now_utc - last_dt).total_seconds() / 3600.0
        if hours_gap > 6.0:
            logger.info("Session '%s' has > 6.0h gap (%.1fh). Starting new session segment.", session_id, hours_gap)
            return f"{session_id}_{int(now_utc.timestamp())}"
    return session_id


@app.post("/api/chat")
def handle_chat_message(req: ChatRequest) -> dict[str, Any]:
    """Execute a conversational chat turn with tool inspection and fast-path support."""
    t0 = time.perf_counter()
    from app.perf import perf
    from app.brain.fast_router import route as fast_route
    rec = perf.new_turn(user_text=req.message)

    eff_session_id = _resolve_session_id(req.session_id)

    # Fast-path check (< 100 ms)
    from app.core.presence import presence
    presence.record_interaction()
    if req.message.lower().strip() in ("not now", "stop", "stop talking", "snooze", "later"):
        presence.snooze_session()

    fast_reply = fast_route(req.message, pipeline=default_pipeline)
    if fast_reply is not None:
        rec.fast_path = True
        rec.total_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        store.save_message(eff_session_id, "user", req.message)
        store.save_message(eff_session_id, "assistant", fast_reply)
        return {
            "ok": True,
            "reply": fast_reply,
            "fast_path": True,
            "tool_calls": [],
            "metrics": {
                "memory_ms": 0.0,
                "prompt_ms": 0.0,
                "total_ms": rec.total_ms,
            },
        }

    # Memory 2.0 Candidate proposals (non-blocking)
    try:
        from app.memory.candidate_extractor import extract_and_propose_candidates
        cands = extract_and_propose_candidates(req.message)
        if cands:
            ws_manager.broadcast_sync({"type": "candidate_memory_proposed", "candidates": cands})
    except Exception as cand_err:
        logger.debug("Candidate extraction skipped: %s", cand_err)

    if default_pipeline.agent is None:
        from app.main import _build_agent
        default_pipeline.agent = _build_agent()
    _attach_web_approval_callback()

    default_pipeline.agent.current_session_id = eff_session_id
    store.save_message(eff_session_id, "user", req.message)
    n_exchanges = getattr(config, "SHORT_TERM_EXCHANGES", 5)
    history_turns = store.get_recent_exchanges(eff_session_id, n_exchanges=n_exchanges)
    messages = [{"role": m["role"], "content": m["text"]} for m in history_turns]

    t_llm = time.perf_counter()
    try:
        reply = default_pipeline.agent.chat(messages)
    except Exception as exc:
        reply = f"Error: {exc}"
    rec.llm_total_ms = round((time.perf_counter() - t_llm) * 1000.0, 1)

    rec.memory_ms = getattr(default_pipeline.agent, "last_memory_search_ms", 0.0)
    rec.prompt_build_ms = getattr(default_pipeline.agent, "last_prompt_build_ms", 0.0)
    rec.tool_calls_ms = getattr(default_pipeline.agent, "last_tool_calls_ms", 0.0)
    rec.total_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    store.save_message(eff_session_id, "assistant", reply)
    tool_calls = getattr(default_pipeline.agent, "last_tool_calls", [])
    return {
        "ok": True,
        "reply": reply,
        "fast_path": False,
        "tool_calls": tool_calls,
        "metrics": {
            "memory_ms": rec.memory_ms,
            "prompt_ms": rec.prompt_build_ms,
            "llm_total_ms": rec.llm_total_ms,
            "total_ms": rec.total_ms,
        },
    }


@app.post("/api/chat/stream")
def handle_chat_stream(req: ChatRequest):
    """Execute a conversational chat turn with real-time SSE token streaming."""
    from fastapi.responses import StreamingResponse
    from app.perf import perf
    from app.brain.fast_router import route as fast_route
    from app.brain.persona import strip_expressive_tags

    def event_generator():
        t0 = time.perf_counter()
        rec = perf.new_turn(user_text=req.message)

        # Fast-path check (< 100 ms)
    from app.core.presence import presence
    presence.record_interaction()
    if req.message.lower().strip() in ("not now", "stop", "stop talking", "snooze", "later"):
        presence.snooze_session()

        fast_reply = fast_route(req.message, pipeline=default_pipeline)
        if fast_reply is not None:
            rec.fast_path = True
            rec.total_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            store.save_message(req.session_id, "user", req.message)
            store.save_message(req.session_id, "assistant", fast_reply)
            yield f"data: {json.dumps({'type': 'chunk', 'text': fast_reply})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'reply': fast_reply, 'fast_path': True, 'tool_calls': []})}\n\n"
            return

        if default_pipeline.agent is None:
            from app.main import _build_agent
            default_pipeline.agent = _build_agent()
        _attach_web_approval_callback()

        default_pipeline.agent.current_session_id = req.session_id
        store.save_message(req.session_id, "user", req.message)
        n_exchanges = getattr(config, "SHORT_TERM_EXCHANGES", 5)
        history_turns = store.get_recent_exchanges(req.session_id, n_exchanges=n_exchanges)
        messages = [{"role": m["role"], "content": m["text"]} for m in history_turns]

        full_reply_parts = []
        t_llm = time.perf_counter()
        try:
            for chunk in default_pipeline.agent.chat_stream(messages):
                if chunk:
                    clean_chunk = strip_expressive_tags(chunk)
                    full_reply_parts.append(clean_chunk)
                    yield f"data: {json.dumps({'type': 'chunk', 'text': clean_chunk})}\n\n"
        except Exception as exc:
            err_msg = f"Error: {exc}"
            full_reply_parts.append(err_msg)
            yield f"data: {json.dumps({'type': 'chunk', 'text': err_msg})}\n\n"

        rec.llm_total_ms = round((time.perf_counter() - t_llm) * 1000.0, 1)
        rec.memory_ms = getattr(default_pipeline.agent, "last_memory_search_ms", 0.0)
        rec.prompt_build_ms = getattr(default_pipeline.agent, "last_prompt_build_ms", 0.0)
        rec.tool_calls_ms = getattr(default_pipeline.agent, "last_tool_calls_ms", 0.0)
        rec.total_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        full_reply = "".join(full_reply_parts).strip()
        store.save_message(req.session_id, "assistant", full_reply)
        tool_calls = getattr(default_pipeline.agent, "last_tool_calls", [])
        yield f"data: {json.dumps({'type': 'done', 'reply': full_reply, 'fast_path': False, 'tool_calls': tool_calls})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/approvals/{approval_id}/respond")
def respond_approval(approval_id: str, req: ApprovalResponse) -> dict[str, Any]:
    """Resolve an awaiting AMBER/RED approval triggered by the user."""
    resolved = pending_approvals.resolve(approval_id, req.approved)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"Approval request '{approval_id}' not found or already resolved.",
        )
    logger.info("Approval %s decided: %s", approval_id, "APPROVED" if req.approved else "DENIED")
    return {"ok": True, "id": approval_id, "approved": req.approved}


@app.get("/api/approvals/pending")
def list_pending_approvals() -> dict[str, Any]:
    """List currently pending approvals."""
    return {"ok": True, "pending": pending_approvals.list_pending()}


@app.post("/api/test/amber")
def test_amber_action(req: AmberTestRequest | None = None) -> dict[str, Any]:
    """Trigger a test AMBER tool execution that requests confirmation via the modal."""
    desc = req.description if req and req.description else "Toggle Living Room Relay 1 (AMBER risk)"
    if default_pipeline.agent is None:
        from app.main import _build_agent
        default_pipeline.agent = _build_agent()
    _attach_web_approval_callback()

    from app.core.approvals import RiskLevel
    approved = default_pipeline.agent._approvals.request(desc, RiskLevel.AMBER)
    if approved:
        from app.brain.tools import registry
        res = registry.run("fake_relay_toggle", {"relay_id": "relay_1", "state": "on"})
        return {
            "ok": True,
            "approved": True,
            "message": res.message,
            "tool": "fake_relay_toggle",
            "data": res.data,
        }
    else:
        return {
            "ok": False,
            "approved": False,
            "message": "Action denied by user.",
            "tool": "fake_relay_toggle",
        }


@app.get("/api/persona/raw")
def get_persona_raw() -> dict[str, Any]:
    """Return raw markdown text of persona/nalla_master.md."""
    text = persona.path.read_text(encoding="utf-8") if persona.path.exists() else ""
    return {"text": text, "path": str(persona.path)}


@app.put("/api/persona/raw")
def update_persona_raw(req: PersonaRawUpdate) -> dict[str, Any]:
    """Save updated markdown to persona/nalla_master.md."""
    persona.path.write_text(req.text, encoding="utf-8")
    persona._load()
    return {"ok": True, "message": "Persona markdown saved to disk."}


@app.post("/api/persona/revert")
def revert_persona_raw() -> dict[str, Any]:
    """Reload the persona file from disk or restore from default baseline."""
    default_p = persona.path.parent / "nalla_master.default.md"
    if default_p.exists():
        persona.path.write_text(default_p.read_text(encoding="utf-8"), encoding="utf-8")
    persona._load()
    return {"ok": True, "text": persona._cached_text}


@app.get("/api/fewshot")
def get_fewshot_endpoint() -> dict[str, Any]:
    """Return the 8 canonical few-shot examples and raw formatted JSON."""
    examples = get_few_shot_examples()
    return {
        "ok": True,
        "count": len(examples),
        "examples": examples,
        "raw_json": json.dumps(examples, indent=2, ensure_ascii=False),
    }


@app.put("/api/fewshot")
def update_fewshot_endpoint(req: FewShotUpdateRequest) -> dict[str, Any]:
    """Save updated few-shot examples to disk and refresh cache."""
    items = [item.model_dump() for item in req.examples]
    ok = save_few_shot_examples(items)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save few-shot examples to disk.")
    return {"ok": True, "count": len(items), "examples": get_few_shot_examples()}


@app.post("/api/fewshot/revert")
def revert_fewshot_endpoint() -> dict[str, Any]:
    """Revert few-shot examples to canonical default examples."""
    reverted = revert_few_shot_examples()
    return {"ok": True, "count": len(reverted), "examples": reverted}


@app.post("/api/personality/preview")
def preview_personality_prompt(req: TestPersonalityRequest) -> dict[str, Any]:
    """Generate live prompt preview including full system prompt, personality block, and mood override."""
    preview = persona.build_prompt_preview(
        mode=req.mode,
        sarcasm=req.sarcasm,
        warmth=req.warmth,
        tamil_mix=req.tamil_mix,
        verbosity=req.verbosity,
        user_text=req.message,
    )
    return {"ok": True, **preview}


@app.post("/api/personality/test")
def test_personality_ephemeral(req: TestPersonalityRequest) -> dict[str, Any]:
    """Test reply using unsaved slider settings without mutating saved config."""
    ephemeral_line = (
        f"Sarcasm: {req.sarcasm}/10. Warmth: {req.warmth}/10. "
        f"Tamil mix: {req.tamil_mix}%. Replies: {req.verbosity}. Mode: {req.mode}."
    )
    custom_prompt = persona.build_system_prompt(
        tool_summary="(no tools connected in test mode)",
        user_text=req.message,
        mode=req.mode,
        sarcasm=req.sarcasm,
        warmth=req.warmth,
        tamil_mix=req.tamil_mix,
        verbosity=req.verbosity,
    )

    if default_pipeline.agent is None:
        from app.main import _build_agent
        default_pipeline.agent = _build_agent()

    try:
        t0 = time.perf_counter()
        chosen_model = req.model or config.GEMINI_CHAT_MODEL
        resp = default_pipeline.agent._provider.generate(
            messages=[{"role": "user", "content": req.message}],
            tools=None,
            system_prompt=custom_prompt,
            model=chosen_model,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "reply": resp.text.strip(),
            "prompt_line": ephemeral_line,
            "latency_ms": latency_ms,
            "model": chosen_model,
        }
    except Exception as exc:
        return {"ok": False, "reply": f"Test chat error: {exc}", "prompt_line": ephemeral_line}


@app.post("/api/personality/compare")
async def compare_personality_models(req: ComparePersonalityRequest) -> dict[str, Any]:
    """Run the same prompt against two models side-by-side with unsaved sliders."""
    custom_prompt = persona.build_system_prompt(
        tool_summary="(no tools connected in test mode)",
        user_text=req.message,
        mode=req.mode,
        sarcasm=req.sarcasm,
        warmth=req.warmth,
        tamil_mix=req.tamil_mix,
        verbosity=req.verbosity,
    )

    if default_pipeline.agent is None:
        from app.main import _build_agent
        default_pipeline.agent = _build_agent()

    model_a = req.model_a or config.GEMINI_CHAT_MODEL
    model_b = req.model_b or "gemini-2.5-flash"

    def _call_model(model_name: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            resp = default_pipeline.agent._provider.generate(
                messages=[{"role": "user", "content": req.message}],
                tools=None,
                system_prompt=custom_prompt,
                model=model_name,
            )
            lat = int((time.perf_counter() - t0) * 1000)
            return {"name": model_name, "reply": resp.text.strip(), "latency_ms": lat, "ok": True}
        except Exception as exc:
            lat = int((time.perf_counter() - t0) * 1000)
            return {"name": model_name, "reply": f"Model error: {exc}", "latency_ms": lat, "ok": False}

    loop = asyncio.get_running_loop()
    res_a, res_b = await asyncio.gather(
        loop.run_in_executor(None, _call_model, model_a),
        loop.run_in_executor(None, _call_model, model_b),
    )
    return {
        "ok": True,
        "prompt_line": f"Sarcasm: {req.sarcasm}/10. Warmth: {req.warmth}/10. Tamil mix: {req.tamil_mix}%. Replies: {req.verbosity}. Mode: {req.mode}.",
        "model_a": res_a,
        "model_b": res_b,
    }


# ---------------------------------------------------------------------------
# IoT Devices Fleet (Phase 5 ready stub)
# ---------------------------------------------------------------------------

_DEVICES = [
    {
        "id": "esp32-relay-1",
        "name": "Living Room Main Light",
        "type": "relay",
        "state": "off",
        "load_w": 45,
        "online": True,
        "last_seen": "Just now",
    },
    {
        "id": "esp32-dht22-1",
        "name": "Living Room Climate Sensor",
        "type": "sensor",
        "state": "on",
        "temp": 28.4,
        "humidity": 62.5,
        "online": True,
        "last_seen": "2s ago",
    },
    {
        "id": "esp8266-fan-1",
        "name": "Bedroom Ceiling Fan",
        "type": "relay",
        "state": "on",
        "load_w": 65,
        "online": True,
        "last_seen": "1m ago",
    },
    {
        "id": "esp32-plug-1",
        "name": "Workbench Smart Plug",
        "type": "relay",
        "state": "off",
        "load_w": 120,
        "online": False,
        "last_seen": "12m ago",
    },
]

_DEVICE_HISTORY = [
    {"time": "10:14:22", "device": "Living Room Main Light", "command": "RELAY_OFF", "status": "ACK_CONFIRMED", "latency_ms": 42},
    {"time": "09:45:10", "device": "Bedroom Ceiling Fan", "command": "RELAY_ON", "status": "ACK_CONFIRMED", "latency_ms": 38},
    {"time": "08:30:00", "device": "Living Room Climate Sensor", "command": "TELEMETRY_POLL", "status": "ACK_CONFIRMED", "latency_ms": 15},
]


@app.get("/api/devices")
def get_devices() -> dict[str, Any]:
    return {"devices": _DEVICES, "history": _DEVICE_HISTORY}


@app.post("/api/devices/{device_id}/toggle")
def toggle_device(device_id: str) -> dict[str, Any]:
    from datetime import datetime
    for d in _DEVICES:
        if d["id"] == device_id:
            if not d.get("online", False):
                _DEVICE_HISTORY.insert(0, {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "device": d["name"],
                    "command": f"TOGGLE_{d['state'].upper()}",
                    "status": "FAILED_OFFLINE",
                    "latency_ms": 0,
                })
                return {
                    "ok": False,
                    "device_id": device_id,
                    "state": d["state"],
                    "error": f"Device '{d['name']}' is offline. Action failed.",
                }
            d["state"] = "off" if d["state"] == "on" else "on"
            d["last_seen"] = "Just now"
            _DEVICE_HISTORY.insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "device": d["name"],
                "command": f"TOGGLE_{d['state'].upper()}",
                "status": "ACK_CONFIRMED",
                "latency_ms": 28,
            })
            return {"ok": True, "device_id": device_id, "state": d["state"]}
    raise HTTPException(status_code=404, detail=f"Device {device_id} not found.")


# ---------------------------------------------------------------------------
# Activity Log & Additional Utilities
# ---------------------------------------------------------------------------

@app.get("/api/activity")
def get_activity_log() -> dict[str, Any]:
    events = [
        {"category": "VOICE", "time": "Just now", "description": "Voice interaction loop standby", "detail": "Listening on 16kHz audio input"},
        {"category": "MEMORY", "time": "Today", "description": "Durable memory store indexed", "detail": "FTS5 index ready"},
        {"category": "SYSTEM", "time": "Today", "description": "NALLA SIVAM Control Center HUD v2.0 initialized", "detail": "All subsystems nominal"},
    ]
    return {"events": events}


@app.post("/api/reminders/{reminder_id}/snooze")
def snooze_reminder(reminder_id: int, req: SnoozeRequest) -> dict[str, Any]:
    from datetime import datetime, timedelta
    reminders = store.list_reminders()
    target = next((r for r in reminders if r["id"] == reminder_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Reminder not found.")
    try:
        due = datetime.fromisoformat(target["due_at"])
    except Exception:
        due = datetime.now()
    new_due = due + timedelta(minutes=req.minutes)
    new_due_str = new_due.isoformat()

    with store._conn() as conn:
        conn.execute("UPDATE reminders SET due_at = ?, status = 'pending' WHERE id = ?", (new_due_str, reminder_id))
    return {"ok": True, "id": reminder_id, "new_due_at": new_due_str}


@app.post("/api/memories/clear")
def clear_all_memories(req: ClearMemoryRequest) -> dict[str, Any]:
    if req.confirmation != "DELETE":
        raise HTTPException(status_code=400, detail="Invalid confirmation phrase. Type 'DELETE'.")
    with store._conn() as conn:
        conn.execute("DELETE FROM memories")
        try:
            conn.execute("DELETE FROM memories_fts")
        except Exception:
            pass
    return {"ok": True, "message": "All memories permanently wiped."}


@app.post("/api/settings/models")
def update_model_settings(req: ModelSettingsUpdate) -> dict[str, Any]:
    if req.chat_model:
        config.GEMINI_CHAT_MODEL = req.chat_model
    if req.command_model:
        config.GEMINI_COMMAND_MODEL = req.command_model
    return {"ok": True, "chat_model": config.GEMINI_CHAT_MODEL, "command_model": config.GEMINI_COMMAND_MODEL}


# ---------------------------------------------------------------------------
# Presence & Proactive Policy Endpoints (Prompt 1A & 1B)
# ---------------------------------------------------------------------------

class PresenceSettingsUpdate(BaseModel):
    proactive_enabled: bool | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    dnd: bool | None = None
    max_per_hour: int | None = None
    max_per_day: int | None = None
    sound_cue: bool | None = None
    briefing_enabled: bool | None = None
    check_ins_enabled: bool | None = None
    welcome_back_enabled: bool | None = None
    wrap_up_enabled: bool | None = None
    reminders_enabled: bool | None = None
    away_threshold_minutes: int | None = None
    check_in_interval_minutes: int | None = None
    stuck_task_hours: int | None = None
    morning_briefing_time: str | None = None
    evening_wrap_up_time: str | None = None
    weather_latitude: float | None = None
    weather_longitude: float | None = None


class PresenceTestRequest(BaseModel):
    category: str = "briefing"  # briefing | welcome_back | check_in | wrap_up


class PresenceSnoozeRequest(BaseModel):
    category: str | None = None
    minutes: int = 30


@app.get("/api/presence")
def get_presence_endpoint() -> dict[str, Any]:
    """Get presence status, settings, scheduled jobs, and recent policy decisions."""
    from app.core.presence import presence
    from app.core.proactive import proactive_engine
    from app.core.scheduler import scheduler

    scheduler.ensure_default_presence_jobs()
    return {
        "status": presence.get_status(),
        "settings": config.get_presence_settings(),
        "jobs": scheduler.list_jobs(),
        "recent_decisions": proactive_engine.get_recent_decisions(20),
    }


@app.post("/api/presence")
def update_presence_endpoint(req: PresenceSettingsUpdate) -> dict[str, Any]:
    """Update and persist presence & proactive policy configuration."""
    data = req.model_dump(exclude_unset=True) if hasattr(req, "model_dump") else req.dict(exclude_unset=True)
    config.save_presence_settings(data)
    return {"ok": True, "settings": config.get_presence_settings()}


@app.post("/api/presence/test")
def test_presence_content_endpoint(req: PresenceTestRequest) -> dict[str, Any]:
    """Test generating a presence content piece in Nalla's voice."""
    from app.core.briefings import (
        generate_morning_briefing,
        generate_welcome_back,
        generate_smart_check_in,
        generate_evening_wrap_up,
    )
    from app.brain.humanisation import split_spoken_vs_screen

    generators = {
        "briefing": generate_morning_briefing,
        "welcome_back": generate_welcome_back,
        "check_in": generate_smart_check_in,
        "wrap_up": generate_evening_wrap_up,
    }
    gen = generators.get(req.category, generate_morning_briefing)
    full_text = gen()
    spoken, screen = split_spoken_vs_screen(full_text)
    return {
        "ok": True,
        "category": req.category,
        "spoken": spoken,
        "screen": screen,
        "full": full_text,
    }


@app.post("/api/presence/snooze")
def snooze_presence_endpoint(req: PresenceSnoozeRequest) -> dict[str, Any]:
    """Snooze proactive speech for the session or for a specific category."""
    from app.core.presence import presence
    if req.category:
        presence.snooze_category(req.category, minutes=req.minutes)
    else:
        presence.snooze_session()
    return {"ok": True, "status": presence.get_status()}


# ---------------------------------------------------------------------------
# Screen Help & Sensitivity Gate API Endpoints (Prompt 2A)
# ---------------------------------------------------------------------------

@app.post("/api/screen/capture")
def capture_screen_endpoint(req: ScreenCaptureRequest | None = None) -> dict[str, Any]:
    """Capture user screen and return in-memory preview thumbnail for Sensitivity Gate."""
    from app.core.screen import screen_manager
    target = req.target if req else "full"
    bbox = tuple(req.bbox) if req and req.bbox and len(req.bbox) == 4 else None
    res = screen_manager.create_capture(target=target, bbox=bbox)
    return {"ok": True, **res}


@app.post("/api/screen/analyze")
def analyze_screen_endpoint(req: ScreenAnalyzeRequest) -> dict[str, Any]:
    """Analyze Sensitivity Gate approved screen capture with multimodal model, then discard image."""
    from app.core.screen import analyze_screen_capture
    result = analyze_screen_capture(
        capture_id=req.capture_id,
        question=req.question,
        blur_rectangles=req.blur_rectangles,
        session_id=req.session_id,
    )
    if result.get("ok") and result.get("spoken") and default_pipeline is not None:
        try:
            default_pipeline._speak_phrase(result["spoken"])
        except Exception as sp_err:
            logger.debug("Speech playback skipped: %s", sp_err)
    return result


@app.post("/api/screen/cancel")
def cancel_screen_endpoint(req: ScreenCancelRequest) -> dict[str, Any]:
    """Cancel screen inspection. Image is discarded immediately from memory without cloud transmission."""
    from app.core.screen import cancel_screen_capture
    return cancel_screen_capture(req.capture_id)


@app.post("/api/screen/save")
def save_screen_endpoint(req: ScreenSaveRequest) -> dict[str, Any]:
    """Explicitly save an in-memory capture to data/screenshots/ upon user request."""
    from app.core.screen import screen_manager
    p = screen_manager.save_to_disk(req.capture_id, req.filename)
    if not p:
        raise HTTPException(status_code=404, detail="Capture expired or already discarded.")
    return {"ok": True, "path": str(p), "filename": p.name}


# ---------------------------------------------------------------------------
# Multi-Step Planner API Endpoints (Prompt 3C)
# ---------------------------------------------------------------------------

from app.core.planner import planner

planner.subscribe(ws_manager.broadcast_sync)


@app.post("/api/plan/create")
def create_plan_endpoint(req: PlanCreateRequest) -> dict[str, Any]:
    """Create a multi-step plan with numbered steps and risk levels."""
    plan = planner.create_plan_from_steps(
        query=req.query,
        steps_spec=req.steps,
        spoken_summary=req.spoken_summary,
        dry_run=req.dry_run,
    )
    return {"ok": True, "plan": plan.to_dict()}


@app.get("/api/plan/{plan_id}")
def get_plan_endpoint(plan_id: str) -> dict[str, Any]:
    """Get status and step progress of a plan."""
    plan = planner.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found.")
    return {"ok": True, "plan": plan.to_dict()}


@app.post("/api/plan/{plan_id}/approve")
def approve_plan_endpoint(plan_id: str, req: PlanApproveRequest) -> dict[str, Any]:
    """Batch approve AMBER steps ('go'), cancel ('cancel'), or modify a step."""
    res = planner.approve_batched(plan_id=plan_id, action=req.action, modifier=req.modifier)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Approval failed."))
    return res


@app.post("/api/plan/{plan_id}/resolve-red")
def resolve_red_step_endpoint(plan_id: str, req: PlanResolveRedRequest) -> dict[str, Any]:
    """Resolve individual approval gate for a high-risk RED step."""
    ok = planner.resolve_red_step(plan_id=plan_id, approved=req.approved)
    return {"ok": ok, "approved": req.approved}


@app.post("/api/plan/stop")
def stop_plan_endpoint() -> dict[str, Any]:
    """Global Stop / Esc: cancel any active plan execution immediately."""
    stopped = planner.stop_active_plan()
    return {"ok": True, "stopped": stopped}


@app.post("/api/plan/{plan_id}/save-note")
def save_plan_note_endpoint(plan_id: str) -> dict[str, Any]:
    """Save completed plan report as a markdown note."""
    res = planner.save_plan_as_note(plan_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to save note."))
    return res


# ---------------------------------------------------------------------------
# Prompt 5C: Always-On, Watchdog, Quota, Offline, Backup & Diagnostics
# ---------------------------------------------------------------------------

class AutostartRequest(BaseModel):
    enabled: bool
    custom_cmd: str | None = None


class RestoreBackupRequest(BaseModel):
    filename: str


class OfflineToggleRequest(BaseModel):
    force_offline: bool


@app.get("/health")
def health_endpoint() -> dict[str, Any]:
    """System health check and component status."""
    from app.core.watchdog import watchdog
    from app.core.quota import quota_tracker
    from app.brain.offline import is_offline

    report = watchdog.get_health_report()
    report["quota"] = quota_tracker.get_status()
    report["offline"] = is_offline()
    return report


@app.get("/api/watchdog/status")
def watchdog_status_endpoint() -> dict[str, Any]:
    """Detailed watchdog component statuses and recent crash tracking."""
    from app.core.watchdog import watchdog
    return watchdog.get_health_report()


@app.post("/api/watchdog/self-check")
def watchdog_self_check_endpoint() -> dict[str, Any]:
    """Trigger an immediate system self-check (disk, API key, mic, TTS, database)."""
    from app.core.watchdog import watchdog
    return watchdog.run_self_check()


@app.get("/api/quota")
def quota_status_endpoint() -> dict[str, Any]:
    """Current Gemini LLM and TTS daily quota consumption and throttling state."""
    from app.core.quota import quota_tracker
    return quota_tracker.get_status()


@app.post("/api/quota/reset")
def quota_reset_endpoint() -> dict[str, Any]:
    """Reset today's quota counters (admin/testing)."""
    from app.core.quota import quota_tracker
    quota_tracker.reset()
    return {"ok": True, "quota": quota_tracker.get_status()}


@app.get("/api/backups")
def list_backups_endpoint() -> dict[str, Any]:
    """List all available backup ZIP archives."""
    from app.core.backup import list_backups
    backups = list_backups()
    return {"ok": True, "count": len(backups), "backups": backups}


@app.post("/api/backup")
def create_backup_endpoint() -> dict[str, Any]:
    """Create a new one-click backup of nalla.db, notes, and configurations."""
    from app.core.backup import create_backup
    try:
        res = create_backup()
        return res
    except Exception as exc:
        logger.error("Failed to create backup: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/restore")
def restore_backup_endpoint(req: RestoreBackupRequest) -> dict[str, Any]:
    """Safely restore database and configurations from a backup zip archive."""
    from app.config import BASE_DIR
    from app.core.backup import restore_backup

    backup_path = BASE_DIR / "data" / "backups" / req.filename
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail=f"Backup file '{req.filename}' not found.")

    try:
        res = restore_backup(zip_path=backup_path)
        return res
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        logger.error("Failed to restore backup: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/backups/download/{filename}")
def download_backup_endpoint(filename: str) -> FileResponse:
    """Download a backup or diagnostics zip archive."""
    from app.config import BASE_DIR
    file_path = (BASE_DIR / "data" / "backups" / filename).resolve()
    base_backups = (BASE_DIR / "data" / "backups").resolve()

    if not file_path.is_relative_to(base_backups) or not file_path.exists():
        raise HTTPException(status_code=404, detail="Archive not found.")

    return FileResponse(path=str(file_path), filename=filename, media_type="application/zip")


@app.post("/api/diagnostics/export")
def export_diagnostics_endpoint() -> dict[str, Any]:
    """Create a sanitized diagnostics bundle with all secrets redacted."""
    from app.core.backup import create_diagnostics_bundle
    from app.core.watchdog import watchdog
    try:
        sys_info = watchdog.get_health_report()
        res = create_diagnostics_bundle(system_status=sys_info)
        return res
    except Exception as exc:
        logger.error("Failed to create diagnostics bundle: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/autostart")
def get_autostart_endpoint() -> dict[str, Any]:
    """Get Windows login auto-start status."""
    from app.core.autostart import get_autostart_status
    return get_autostart_status()


@app.post("/api/autostart")
def set_autostart_endpoint(req: AutostartRequest) -> dict[str, Any]:
    """Toggle auto-start on user login."""
    from app.core.autostart import set_autostart, get_autostart_status
    ok = set_autostart(enable=req.enabled, custom_cmd=req.custom_cmd)
    status = get_autostart_status()
    status["updated"] = ok
    return status


@app.get("/api/offline/status")
def offline_status_endpoint() -> dict[str, Any]:
    """Get offline degraded mode and network reachability status."""
    from app.brain.offline import is_offline, is_force_offline
    return {
        "is_offline": is_offline(),
        "force_offline": is_force_offline(),
    }


@app.post("/api/offline/toggle")
def offline_toggle_endpoint(req: OfflineToggleRequest) -> dict[str, Any]:
    """Manually force or unforce offline degraded mode."""
    from app.brain.offline import set_force_offline, is_offline, is_force_offline
    set_force_offline(req.force_offline)
    return {
        "is_offline": is_offline(),
        "force_offline": is_force_offline(),
    }





# ---------------------------------------------------------------------------
# Scenario Lab & Human Mode Endpoints (Prompt H2 & H4)
# ---------------------------------------------------------------------------

class ScenarioTestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    strictness: str | None = None


class ScenarioDraftAction(BaseModel):
    scenario_id: str
    filename: str | None = None


class ScenarioEditRequest(BaseModel):
    scenario_id: str
    replies: list[dict[str, Any]]


@app.post("/api/scenarios/test")
def test_scenario_endpoint(req: ScenarioTestRequest) -> dict[str, Any]:
    """Test an utterance against Crisis Guard + Scenario Engine (dry run)."""
    from app.brain.scenarios import scenario_engine, check_crisis
    crisis = check_crisis(req.text)
    if crisis.is_crisis:
        return {
            "ok": True,
            "crisis": True,
            "category": crisis.category,
            "matched": False,
        }
    m = scenario_engine.match(req.text, strictness=req.strictness, dry_run=True)
    if m is None:
        return {"ok": True, "crisis": False, "matched": False}
    return {
        "ok": True,
        "crisis": False,
        "matched": True,
        "scenario_id": m.scenario_id,
        "category": m.category,
        "tier": m.tier,
        "confidence": m.confidence,
        "latency_ms": m.latency_ms,
        "reply": m.reply,
        "matched_trigger": m.matched_trigger,
    }


@app.get("/api/scenarios/list")
def list_scenarios_endpoint() -> dict[str, Any]:
    """List active scenarios, drafts, conflicts, and local miss log."""
    import yaml
    from app.brain.scenarios import (
        scenario_engine, SCENARIOS_DRAFTS_DIR, get_miss_log,
    )
    drafts: list[dict[str, Any]] = []
    if SCENARIOS_DRAFTS_DIR.exists():
        for p in sorted(SCENARIOS_DRAFTS_DIR.glob("*.yaml")):
            items = yaml.safe_load(p.read_text(encoding="utf-8")) or []
            for item in items:
                if isinstance(item, dict):
                    drafts.append({**item, "_file": p.name})
    active = [
        {
            "id": s["id"],
            "category": s.get("category", "general"),
            "triggers": s.get("triggers", {}),
            "replies": s.get("replies", []),
            "_source": s.get("_source", ""),
        }
        for s in scenario_engine.scenarios
    ]
    return {
        "ok": True,
        "active": active,
        "drafts": drafts,
        "conflicts": scenario_engine.conflicts,
        "miss_log": get_miss_log()[-100:],
        "coverage": {
            "active_count": len(active),
            "draft_count": len(drafts),
            "conflict_count": len(scenario_engine.conflicts),
        },
    }


@app.post("/api/scenarios/approve")
def approve_scenario_draft(req: ScenarioDraftAction) -> dict[str, Any]:
    """Move a draft scenario from drafts/ to active/ and reload engine."""
    import yaml
    from app.brain.scenarios import scenario_engine, SCENARIOS_DRAFTS_DIR, SCENARIOS_ACTIVE_DIR
    if not SCENARIOS_DRAFTS_DIR.exists():
        raise HTTPException(status_code=404, detail="No drafts directory.")

    target_item = None
    target_file = None
    for p in sorted(SCENARIOS_DRAFTS_DIR.glob("*.yaml")):
        items = yaml.safe_load(p.read_text(encoding="utf-8")) or []
        remaining = []
        for item in items:
            if isinstance(item, dict) and item.get("id") == req.scenario_id and target_item is None:
                target_item = item
                target_file = p
            else:
                remaining.append(item)
        if target_item is not None and target_file == p:
            if remaining:
                p.write_text(yaml.safe_dump(remaining, sort_keys=False, allow_unicode=True), encoding="utf-8")
            else:
                p.unlink()
            break

    if target_item is None:
        raise HTTPException(status_code=404, detail="Draft scenario not found.")

    SCENARIOS_ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    cat = target_item.get("category", "custom")
    active_file = SCENARIOS_ACTIVE_DIR / f"{cat}.yaml"
    existing = yaml.safe_load(active_file.read_text(encoding="utf-8")) if active_file.exists() else []
    if not isinstance(existing, list):
        existing = []
    existing = [x for x in existing if x.get("id") != req.scenario_id]
    existing.append(target_item)
    active_file.write_text(yaml.safe_dump(existing, sort_keys=False, allow_unicode=True), encoding="utf-8")
    count = scenario_engine.reload()
    return {"ok": True, "approved": req.scenario_id, "active_count": count}


@app.post("/api/scenarios/reject")
def reject_scenario_draft(req: ScenarioDraftAction) -> dict[str, Any]:
    """Delete a draft scenario from drafts/."""
    import yaml
    from app.brain.scenarios import SCENARIOS_DRAFTS_DIR
    if not SCENARIOS_DRAFTS_DIR.exists():
        raise HTTPException(status_code=404, detail="No drafts directory.")
    for p in sorted(SCENARIOS_DRAFTS_DIR.glob("*.yaml")):
        items = yaml.safe_load(p.read_text(encoding="utf-8")) or []
        remaining = [x for x in items if not (isinstance(x, dict) and x.get("id") == req.scenario_id)]
        if len(remaining) != len(items):
            if remaining:
                p.write_text(yaml.safe_dump(remaining, sort_keys=False, allow_unicode=True), encoding="utf-8")
            else:
                p.unlink()
            return {"ok": True, "rejected": req.scenario_id}
    raise HTTPException(status_code=404, detail="Draft scenario not found.")


@app.post("/api/scenarios/miss-log/clear")
def clear_miss_log_endpoint() -> dict[str, Any]:
    from app.brain.scenarios import clear_miss_log
    removed = clear_miss_log()
    return {"ok": True, "cleared": removed}


@app.get("/api/fillers/list")
def list_fillers_endpoint() -> dict[str, Any]:
    from app.voice.fillers import filler_manager
    return {"ok": True, **filler_manager.list_library()}


@app.post("/api/fillers/generate")
def generate_fillers_endpoint() -> dict[str, Any]:
    from app.voice.fillers import filler_manager, DEFAULT_FILLER_PHRASES
    generated = 0
    for cat, phrases in DEFAULT_FILLER_PHRASES.items():
        for p in phrases[:4]:
            audio = filler_manager.ensure_filler_audio(default_pipeline.tts, cat, p)
            if audio:
                generated += 1
    return {"ok": True, "generated": generated, "voice_hash": filler_manager.list_library()["voice_hash"]}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_server() -> None:
    """Launch the uvicorn web server bound strictly to localhost."""
    import uvicorn
    # Connect agent to pipeline if available
    try:
        from app.main import _build_agent
        default_pipeline.agent = _build_agent()
    except Exception as exc:
        logger.debug("Deferred agent attachment: %s", exc)

    # Enforce strictly localhost-only binding (127.0.0.1) as required by CONTEXT.md
    host = "127.0.0.1"
    logger.info("Starting dashboard at http://%s:%d", host, WEB_PORT)
    uvicorn.run(app, host=host, port=WEB_PORT, log_level=config.LOG_LEVEL.lower())


if __name__ == "__main__":
    run_server()
