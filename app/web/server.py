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
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.config import BASE_DIR, WEB_HOST, WEB_PORT
from app.brain.persona import persona
from app.memory.store import store
from app.voice.audio_io import list_input_devices, list_output_devices
from app.voice.pipeline import default_pipeline

logger = logging.getLogger("nalla.web")


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
    text: str = Field(..., min_length=1, description="Memory content")
    category: str = Field("fact", description="Category: preference|fact|project|person|task")
    importance: int = Field(3, ge=1, le=5, description="Importance from 1 to 5")


class MemoryUpdate(BaseModel):
    text: str | None = None
    category: str | None = None
    importance: int | None = Field(None, ge=1, le=5)


class ReminderCreate(BaseModel):
    text: str = Field(..., min_length=1, description="Reminder description")
    due_at: str = Field(..., description="ISO 8601 timestamp (e.g. 2026-11-20T17:00:00)")


class SettingsUpdate(BaseModel):
    memory_enabled: bool


class PersonalityUpdate(BaseModel):
    mode: str = "Friend"
    sarcasm: int = Field(7, ge=0, le=10)
    warmth: int = Field(8, ge=0, le=10)
    tamil_mix: int = Field(40, ge=0, le=100)
    verbosity: str = "very short"


class MuteRequest(BaseModel):
    muted: bool


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = "web"


class TestPersonalityRequest(BaseModel):
    message: str = Field(..., min_length=1)
    sarcasm: int = Field(7, ge=0, le=10)
    warmth: int = Field(8, ge=0, le=10)
    tamil_mix: int = Field(40, ge=0, le=100)
    verbosity: str = "very short"
    mode: str = "Friend"


class PersonaRawUpdate(BaseModel):
    text: str


class SnoozeRequest(BaseModel):
    minutes: int = 15


class ClearMemoryRequest(BaseModel):
    confirmation: str


class ModelSettingsUpdate(BaseModel):
    chat_model: str | None = None
    command_model: str | None = None


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
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as exc:
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
# Memory API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/memories")
def list_or_search_memories(
    q: str = "",
    category: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List or search memories with optional category filter."""
    if q.strip():
        results = store.search_memories(q.strip(), limit=limit)
        if category:
            results = [m for m in results if m.get("category") == category]
        return results
    cat = category.strip() if category.strip() else None
    return store.list_memories(category=cat, limit=limit)


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
# Settings & Personality API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    """Return runtime settings."""
    return {
        "memory_enabled": config.MEMORY_ENABLED,
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
    }


@app.post("/api/settings")
def update_settings(item: SettingsUpdate) -> dict[str, Any]:
    """Toggle memory on or off."""
    config.MEMORY_ENABLED = item.memory_enabled
    config.save_persisted_settings()
    logger.info("Setting memory_enabled updated to %s", item.memory_enabled)
    return {"ok": True, "memory_enabled": config.MEMORY_ENABLED}


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

@app.post("/api/chat")
def handle_chat_message(req: ChatRequest) -> dict[str, Any]:
    """Execute a conversational chat turn with tool inspection support."""
    if default_pipeline.agent is None:
        from app.main import _build_agent
        default_pipeline.agent = _build_agent()

    store.save_message(req.session_id, "user", req.message)
    history_turns = store.get_messages(req.session_id, limit=16)
    messages = [{"role": m["role"], "content": m["text"]} for m in history_turns]

    try:
        reply = default_pipeline.agent.chat(messages)
    except Exception as exc:
        reply = f"Error: {exc}"

    store.save_message(req.session_id, "assistant", reply)
    return {
        "ok": True,
        "reply": reply,
        "tool_calls": [],
        "metrics": {
            "memory_ms": getattr(default_pipeline.agent, "last_memory_search_ms", 0.0),
            "prompt_ms": getattr(default_pipeline.agent, "last_prompt_build_ms", 0.0),
        },
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
    """Reload the persona file from disk."""
    persona._load()
    return {"ok": True, "text": persona._cached_text}


@app.post("/api/personality/test")
def test_personality_ephemeral(req: TestPersonalityRequest) -> dict[str, Any]:
    """Test reply using unsaved slider settings without mutating saved config."""
    from datetime import datetime
    now = datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")
    ephemeral_line = (
        f"Sarcasm: {req.sarcasm}/10. Warmth: {req.warmth}/10. "
        f"Tamil mix: {req.tamil_mix}%. Replies: {req.verbosity}. Mode: {req.mode}."
    )
    custom_prompt = (
        f"{persona._cached_text}\n\n---\n{ephemeral_line}\n\n"
        f"Current date/time: {now}\nCurrently connected tools: (no tools connected in test mode)\n"
    )

    if default_pipeline.agent is None:
        from app.main import _build_agent
        default_pipeline.agent = _build_agent()

    try:
        resp = default_pipeline.agent._provider.generate(
            messages=[{"role": "user", "content": req.message}],
            tools=None,
            system_prompt=custom_prompt,
        )
        return {"ok": True, "reply": resp.text.strip(), "prompt_line": ephemeral_line}
    except Exception as exc:
        return {"ok": False, "reply": f"Test chat error: {exc}"}


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

    # Enforce localhost-only binding
    host = "127.0.0.1" if WEB_HOST in ("localhost", "127.0.0.1") else WEB_HOST
    logger.info("Starting dashboard at http://%s:%d", host, WEB_PORT)
    uvicorn.run(app, host=host, port=WEB_PORT, log_level=config.LOG_LEVEL.lower())


if __name__ == "__main__":
    run_server()
