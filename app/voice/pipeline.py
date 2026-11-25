"""app/voice/pipeline.py

Voice Pipeline State Machine & Execution Loop.

State Machine:
  IDLE -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE

Performance improvements in this version
-----------------------------------------
• Full per-turn timing via app.perf.TurnRecord (all 8 spans).
• Fast-path router (app.brain.fast_router) handles greetings, time/date,
  mic mute/unmute, reminders, stop — returns in < 100 ms, no LLM call.
• Memory search starts in a background thread right after STT finishes,
  overlapping with the LLM API call setup.
• Sentence-by-sentence streaming: each sentence goes to TTS as soon as
  it ends, without waiting for the full model reply.
• Mic is muted while Nalla speaks to prevent feedback loops.
• Barge-in: wake-word or hotkey during SPEAKING interrupts playback.
• 30-second follow-up window after each reply (no wake word needed).
• Filler phrase ("On it, boss.") if any tool takes > 1.5 s.
• All blocking I/O (STT, TTS, file/DB) runs in worker threads via
  the shared executor so the asyncio event loop is never stalled.

Rules:
  1. Mic muted while Nalla is speaking.
  2. New wake word during SPEAKING interrupts playback.
  3. Empty / low-confidence STT → "Sorry boss, didn't catch that."
  4. Sentence-by-sentence TTS streaming.
  5. Log + broadcast timing metrics.
  6. Strip markdown/emoji/code before TTS.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from enum import Enum
from typing import Any, Callable, Generator

from app.config import FOLLOW_UP_ENABLED, FOLLOW_UP_TIMEOUT
from app.brain.agent import Agent
from app.brain.fast_router import route as fast_route
from app.perf import perf
from app.voice.audio_io import AudioPlayer, AudioRecorder, compute_rms, default_player, default_recorder
from app.voice.stt import FasterWhisperSTT, SpeechToTextProvider, stt_engine
from app.voice.tts import TTSManager, sanitize_text_for_speech, tts_engine
from app.voice.vad import VoiceActivityDetector, default_vad
from app.voice.wakeword import PushToTalk, WakeWordDetector, default_wakeword

logger = logging.getLogger("nalla.voice.pipeline")

# Sentence-boundary splitter
_SENTENCE_END_RE = re.compile(r"([.!?\n][\s\n]+)")

# Min chars before treating a chunk as a sentence to avoid very short clips
_MIN_SENTENCE_LEN = 6


class VoiceState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"


class VoicePipeline:
    """Manages the full listen -> STT -> think -> TTS -> speak cycle."""

    def __init__(
        self,
        agent: Agent | None = None,
        recorder: AudioRecorder | None = None,
        player: AudioPlayer | None = None,
        vad: VoiceActivityDetector | None = None,
        stt: SpeechToTextProvider | None = None,
        tts: TTSManager | None = None,
        wakeword: WakeWordDetector | None = None,
        follow_up_enabled: bool | None = None,
        follow_up_timeout: float | None = None,
    ) -> None:
        self.agent = agent
        self.recorder = recorder or default_recorder
        self.player = player or default_player
        self.vad = vad or default_vad
        self.stt = stt or stt_engine
        self.tts = tts or tts_engine
        self.wakeword = wakeword or default_wakeword
        self.follow_up_enabled = follow_up_enabled if follow_up_enabled is not None else FOLLOW_UP_ENABLED
        self.follow_up_timeout = follow_up_timeout if follow_up_timeout is not None else FOLLOW_UP_TIMEOUT

        self._state = VoiceState.IDLE
        self._is_running = False
        self._is_muted = False
        self._speaking_muted = False
        self._interrupt_event = threading.Event()
        self._current_turn_id = 0
        self._turn_thread: threading.Thread | None = None
        self._state_subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.Lock()

        # Walkie-talkie mode state (Prompt A)
        self._ptt_active = False
        self._ptt_down_time = 0.0
        self._ptt_chunks: list[bytes] = []
        self._ptt_lock = threading.Lock()
        self._ptt_stream = None
        self._ptt_timer: threading.Timer | None = None
        self._ptt_chunk_counter = 0

        # Start warm stream if enabled in configuration
        from app import config as _cfg
        if getattr(_cfg, "KEEP_MIC_WARM", False) and hasattr(self.recorder, "start_warm_stream"):
            self.recorder.start_warm_stream()

        # Connect wakeword callback
        self.wakeword.start(on_detected=self.trigger_wakeword)

        # Connect TTS fallback notification listener
        if hasattr(self.tts, "add_fallback_listener"):
            self.tts.add_fallback_listener(self._handle_tts_fallback)

    def reload_wakeword_detector(self, engine_name: str | None = None) -> None:
        """Switch or reload wake word detector and reconnect callbacks."""
        from app.voice.wakeword import get_wakeword_detector
        if self.wakeword:
            self.wakeword.stop()
        self.wakeword = get_wakeword_detector(engine_name)
        self.wakeword.start(on_detected=self.trigger_wakeword)
        logger.info("Pipeline reloaded wake word detector: %s", type(self.wakeword).__name__)

    def _handle_tts_fallback(self, reason: str) -> None:
        logger.warning("TTS fallback active: %s", reason)
        self._broadcast({"type": "notice", "level": "warning", "message": reason})

    # ------------------------------------------------------------------
    # State & Event Broadcasting
    # ------------------------------------------------------------------

    @property
    def state(self) -> VoiceState:
        return self._state

    def get_state(self) -> VoiceState:
        return self._state

    def set_state(self, new_state: VoiceState, extra: dict[str, Any] | None = None) -> None:
        with self._lock:
            old_state = self._state
            self._state = new_state

            # Mic is automatically muted while speaking to prevent self-hearing feedback loops (echo control)
            if new_state == VoiceState.SPEAKING:
                self._speaking_muted = True
                if hasattr(self.recorder, "set_muted"):
                    self.recorder.set_muted(True)
            elif old_state == VoiceState.SPEAKING and new_state != VoiceState.SPEAKING:
                self._speaking_muted = False
                if hasattr(self.recorder, "set_muted"):
                    self.recorder.set_muted(False)

            logger.info("Voice pipeline: %s -> %s", old_state.value, new_state.value)
            payload = {"type": "state_change", "state": new_state.value}
            if extra:
                payload.update(extra)
            self._broadcast(payload)

            # Trigger optional chimes
            try:
                from app.config import get_audio_feel
                feel = get_audio_feel()
                if new_state == VoiceState.LISTENING and feel.get("listening_chime"):
                    self._broadcast({"type": "chime", "sound": "listening", "volume": feel.get("chime_volume", 80)})
                elif new_state == VoiceState.IDLE and old_state == VoiceState.SPEAKING and feel.get("done_chime"):
                    self._broadcast({"type": "chime", "sound": "done", "volume": feel.get("chime_volume", 80)})
            except Exception:
                pass

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        if callback not in self._state_subscribers:
            self._state_subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        if callback in self._state_subscribers:
            self._state_subscribers.remove(callback)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for cb in list(self._state_subscribers):
            try:
                cb(event)
            except Exception as exc:
                logger.debug("Error in event subscriber: %s", exc)

    # ------------------------------------------------------------------
    # Control Actions
    # ------------------------------------------------------------------

    def set_muted(self, muted: bool) -> None:
        self._is_muted = muted
        logger.info("Voice pipeline mic muted: %s", muted)
        self._broadcast({"type": "mute_change", "muted": muted})

    @property
    def is_muted(self) -> bool:
        return self._is_muted or self._speaking_muted

    def interrupt(self) -> None:
        logger.info("Voice pipeline interruption requested.")
        self._current_turn_id += 1
        self._interrupt_event.set()
        self.player.stop()

    def push_to_talk(self) -> None:
        """Trigger push-to-talk voice turn directly."""
        self.trigger_wakeword()

    def trigger_wakeword(self) -> None:
        """Handle wake word or push-to-talk (with barge-in support < 200ms)."""
        if self._is_muted:
            logger.debug("Wakeword ignored because mic is muted.")
            return

        if self._state in (VoiceState.SPEAKING, VoiceState.THINKING):
            t0 = time.perf_counter()
            self.interrupt()
            if self._turn_thread and self._turn_thread.is_alive():
                self._turn_thread.join(timeout=0.15)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            logger.info("Barge-in: interrupted %s in %.1f ms.", self._state.value, elapsed_ms)
            self._turn_thread = threading.Thread(
                target=self._run_voice_turn,
                kwargs={"allow_follow_up": self.follow_up_enabled},
                daemon=True,
                name="VoiceTurnThread",
            )
            self._turn_thread.start()
            return

        if self._state in (VoiceState.IDLE, VoiceState.LISTENING):
            if self._state == VoiceState.LISTENING:
                self.interrupt()
                time.sleep(0.04)
            self._turn_thread = threading.Thread(
                target=self._run_voice_turn,
                kwargs={"allow_follow_up": self.follow_up_enabled},
                daemon=True,
                name="VoiceTurnThread",
            )
            self._turn_thread.start()

    # ------------------------------------------------------------------
    # Walkie-Talkie Mode (Hold Space to Talk, Release to Send)
    # ------------------------------------------------------------------

    def ptt_down(self) -> dict[str, Any]:
        """User pressed and is holding the talk button / key (Space / on-screen button)."""
        if self._is_muted:
            logger.debug("PTT down ignored: mic is muted.")
            return {"ok": False, "reason": "muted"}

        if self._ptt_active:
            # Ignore repeated keydown events while already holding
            return {"ok": True, "action": "ignored_repeat"}

        # Barge-in: if currently speaking or thinking, cancel immediately within 200ms
        if self._state in (VoiceState.SPEAKING, VoiceState.THINKING) or (self._turn_thread and self._turn_thread.is_alive()):
            t0 = time.perf_counter()
            self.interrupt()
            if self._turn_thread and self._turn_thread.is_alive():
                self._turn_thread.join(timeout=0.15)
            logger.info("PTT Barge-in: cancelled %s in %.1f ms", self._state.value, (time.perf_counter() - t0) * 1000.0)

        self._current_turn_id += 1
        self._interrupt_event.clear()
        self._ptt_active = True
        self._ptt_down_time = time.perf_counter()
        self._ptt_chunk_counter = 0

        with self._ptt_lock:
            self._ptt_chunks = []
            from app import config as _cfg
            if getattr(_cfg, "KEEP_MIC_WARM", False) and hasattr(self.recorder, "get_warm_frames"):
                warm = self.recorder.get_warm_frames()
                if warm:
                    self._ptt_chunks.append(warm)

        # Open microphone stream for PTT capture
        try:
            self._ptt_stream = self.recorder.open_stream(self._on_ptt_audio_chunk, blocksize=320)
        except Exception as exc:
            logger.warning("Could not open PTT audio stream: %s", exc)
            self._ptt_stream = None

        self.set_state(VoiceState.LISTENING, extra={"ptt": True, "hold_to_talk": True})

        # Broadcast click sound event if configured
        from app import config as _cfg
        if getattr(_cfg, "SOFT_CLICK_SOUND", True):
            self._broadcast({"type": "click_sound", "action": "down"})

        # Safety auto-release timer at 60s
        if self._ptt_timer:
            self._ptt_timer.cancel()
        self._ptt_timer = threading.Timer(60.0, self._on_ptt_auto_release)
        self._ptt_timer.daemon = True
        self._ptt_timer.start()

        return {"ok": True, "state": "LISTENING"}

    def _on_ptt_audio_chunk(self, chunk: bytes) -> None:
        """Callback for frames arriving from sounddevice during PTT hold."""
        if not self._ptt_active:
            return
        with self._ptt_lock:
            self._ptt_chunks.append(chunk)

        self._ptt_chunk_counter += 1
        # Broadcast live volume meter every ~80ms (4 chunks @ 20ms)
        if self._ptt_chunk_counter % 4 == 0:
            rms = compute_rms(chunk)
            elapsed = round(time.perf_counter() - self._ptt_down_time, 1)
            self._broadcast({
                "type": "ptt_level",
                "rms": round(rms, 1),
                "elapsed_s": elapsed,
            })

    def _on_ptt_auto_release(self) -> None:
        """Invoked when maximum 60s hold time is reached."""
        logger.warning("PTT hold reached 60s maximum limit. Auto-releasing.")
        self._broadcast({"type": "notice", "level": "warning", "message": "Max hold time (60s) reached."})
        self._broadcast({"type": "chime", "sound": "warning", "volume": 80})
        self.ptt_up()

    def ptt_up(self) -> dict[str, Any]:
        """User released the talk button / key."""
        if not self._ptt_active:
            return {"ok": False, "reason": "not_active"}

        if self._ptt_timer:
            self._ptt_timer.cancel()
            self._ptt_timer = None

        self._ptt_active = False
        hold_duration = time.perf_counter() - self._ptt_down_time

        from app import config as _cfg
        if getattr(_cfg, "SOFT_CLICK_SOUND", True):
            self._broadcast({"type": "click_sound", "action": "up"})

        # Close active capture stream
        if self._ptt_stream is not None:
            try:
                self.recorder.close_stream()
            except Exception as exc:
                logger.debug("Error closing PTT stream: %s", exc)
            self._ptt_stream = None

        # Ignore accidental short tap (< 0.35s)
        if hold_duration < 0.35:
            with self._ptt_lock:
                self._ptt_chunks = []
            logger.info("PTT hold too short (%.2fs < 0.35s) — ignored as accidental tap.", hold_duration)
            self._broadcast({"type": "ptt_hint", "message": "Hold Space to talk"})
            self.set_state(VoiceState.IDLE)
            return {"ok": True, "action": "ignored_short_tap", "duration": hold_duration}

        # Valid hold: gather audio bytes
        with self._ptt_lock:
            audio_bytes = b"".join(self._ptt_chunks)
            self._ptt_chunks = []

        t_release = time.perf_counter()
        active_turn_id = self._current_turn_id
        logger.info("PTT released: captured %.2fs audio (%d bytes). Starting STT immediately.", hold_duration, len(audio_bytes))

        # Launch background turn with pre-recorded audio bytes
        self._turn_thread = threading.Thread(
            target=self._run_walkie_turn,
            args=(audio_bytes, t_release, active_turn_id),
            daemon=True,
            name=f"WalkieTurnThread-{active_turn_id}",
        )
        self._turn_thread.start()
        return {"ok": True, "state": "TRANSCRIBING"}

    def ptt_cancel(self) -> dict[str, Any]:
        """Cancel PTT capture (e.g. Esc pressed, window blur, tab hidden, WS disconnect)."""
        if not self._ptt_active:
            return {"ok": False, "reason": "not_active"}

        if self._ptt_timer:
            self._ptt_timer.cancel()
            self._ptt_timer = None

        self._ptt_active = False
        with self._ptt_lock:
            self._ptt_chunks = []

        if self._ptt_stream is not None:
            try:
                self.recorder.close_stream()
            except Exception as exc:
                logger.debug("Error closing PTT stream: %s", exc)
            self._ptt_stream = None

        logger.info("PTT recording cancelled and discarded.")
        self.set_state(VoiceState.IDLE)
        return {"ok": True, "action": "cancelled"}

    def _run_walkie_turn(self, audio_bytes: bytes, release_time: float, turn_id: int = 0) -> str:
        """Execute turn using already captured audio bytes — NO VAD silence wait."""
        if turn_id and self._current_turn_id != turn_id:
            return ""
        turn_start = release_time
        rec = perf.new_turn()
        rec.release_time = release_time
        try:
            self.tts.clear_turn_engines()
        except Exception:
            pass

        try:
            # ------- 2. TRANSCRIBING -------
            self.set_state(VoiceState.TRANSCRIBING)
            stt_t0 = time.perf_counter()
            result = self.stt.transcribe(audio_bytes)
            rec.stt_ms = round((time.perf_counter() - stt_t0) * 1000.0, 1)

            user_text = result.text.strip()
            detected_lang = result.language
            rec.user_text = user_text[:60]

            if turn_id and self._current_turn_id != turn_id:
                return ""

            self._broadcast({
                "type": "transcript",
                "role": "user",
                "text": user_text,
                "confidence": result.confidence,
                "language": detected_lang,
            })

            # Low-confidence / empty guard
            if not user_text or (result.confidence < 0.15 and len(user_text) < 4):
                logger.info("Low confidence STT (conf=%.2f) — fallback phrase.", result.confidence)
                rec.fast_path = True
                self._speak_phrase("Sorry boss, didn't catch that.", rec, release_time, detected_lang)
                rec.total_ms = round((time.perf_counter() - turn_start) * 1000.0, 1)
                self._emit_metrics(rec)
                self.set_state(VoiceState.IDLE)
                return "Sorry boss, didn't catch that."

            if self._interrupt_event.is_set() or (turn_id and self._current_turn_id != turn_id):
                self.set_state(VoiceState.IDLE)
                return ""

            # ------- 3. FAST-PATH ROUTER -------
            self.set_state(VoiceState.THINKING)
            fast_reply = fast_route(user_text, pipeline=self)
            if fast_reply is not None:
                logger.info("Fast-path handled: '%s' -> '%s'", user_text[:40], fast_reply)
                rec.fast_path = True
                rec.llm_first_ms = 0.0
                try:
                    from app.memory.store import store
                    session_key = getattr(self, "current_session_id", "voice")
                    store.save_message(session_key, "user", user_text)
                    store.save_message(session_key, "assistant", fast_reply)
                except Exception as exc:
                    logger.debug("Failed to persist fast-path voice turn: %s", exc)
                if not (turn_id and self._current_turn_id != turn_id):
                    self._speak_phrase(fast_reply, rec, release_time, detected_lang)
                rec.total_ms = round((time.perf_counter() - turn_start) * 1000.0, 1)
                self._emit_metrics(rec)
                self.set_state(VoiceState.IDLE)
                return fast_reply

            # ------- 4. LAZY AGENT ATTACH -------
            if self.agent is None:
                try:
                    from app.main import _build_agent
                    self.agent = _build_agent()
                    logger.info("Voice pipeline lazily attached Gemini agent.")
                except Exception as exc:
                    logger.debug("Could not attach agent: %s", exc)

            if self.agent is None:
                fallback = f"Voice test mode received: {user_text}"
                if not (turn_id and self._current_turn_id != turn_id):
                    self._speak_phrase(fallback, rec, release_time, detected_lang)
                rec.total_ms = round((time.perf_counter() - turn_start) * 1000.0, 1)
                self._emit_metrics(rec)
                self.set_state(VoiceState.IDLE)
                return fallback

            # Attach filler speaker to agent
            if hasattr(self.agent, '_filler_speaker'):
                self.agent._filler_speaker = lambda phrase: self._speak_phrase_nonblocking(phrase)

            try:
                from app.memory.store import store
                session_key = getattr(self, "current_session_id", "voice")
                store.save_message(session_key, "user", user_text)
                from app import config
                n_exchanges = getattr(config, "SHORT_TERM_EXCHANGES", 5)
                history_turns = store.get_recent_exchanges(session_key, n_exchanges=n_exchanges)
                messages = [{"role": m["role"], "content": m["text"]} for m in history_turns]
            except Exception:
                messages = [{"role": "user", "content": user_text}]

            # ------- 5. STREAM AGENT REPLY -------
            llm_t0 = time.perf_counter()

            full_reply = self._stream_agent_turn(
                messages=messages,
                llm_start=llm_t0,
                speech_end=release_time,
                rec=rec,
                detected_lang=detected_lang,
                turn_id=turn_id,
            )

            if self.agent:
                rec.memory_ms = getattr(self.agent, "last_memory_search_ms", 0.0)
                rec.prompt_build_ms = getattr(self.agent, "last_prompt_build_ms", 0.0)
                rec.tool_calls_ms = getattr(self.agent, "last_tool_calls_ms", 0.0)

            rec.total_ms = round((time.perf_counter() - turn_start) * 1000.0, 1)
            self._emit_metrics(rec)
            self.set_state(VoiceState.IDLE)
            return full_reply

        except Exception as exc:
            logger.exception("Error in walkie turn: %s", exc)
            self.set_state(VoiceState.IDLE)
            return f"(Walkie turn error: {exc})"

    # ------------------------------------------------------------------
    # Core Turn Execution
    # ------------------------------------------------------------------

    def run_single_turn(self, allow_follow_up: bool = False) -> str:
        return self._run_voice_turn(allow_follow_up=allow_follow_up)

    def _run_voice_turn(self, allow_follow_up: bool = False) -> str:
        """Executes: listen -> STT -> think -> TTS -> speak."""
        if self._is_muted:
            return ""

        self._interrupt_event.clear()
        first_turn = True
        last_reply = ""

        try:
            while not self._interrupt_event.is_set() and not self._is_muted:
                turn_start = time.perf_counter()

                # Create a perf record for this turn
                rec = perf.new_turn()
                try:
                    self.tts.clear_turn_engines()
                except Exception:
                    pass

                # ------- 1. LISTENING -------
                if first_turn:
                    self.set_state(VoiceState.LISTENING)
                    audio_bytes = self.vad.record_speech_segment(
                        recorder=self.recorder,
                        silence_duration=0.8,
                        max_duration=20.0,
                        initial_timeout=8.0,
                    )
                else:
                    self.set_state(VoiceState.LISTENING, extra={
                        "follow_up": True, "timeout": self.follow_up_timeout
                    })
                    audio_bytes = self.vad.record_speech_segment(
                        recorder=self.recorder,
                        silence_duration=0.8,
                        max_duration=20.0,
                        initial_timeout=self.follow_up_timeout,
                    )

                speech_end = time.perf_counter()

                if not audio_bytes or self._interrupt_event.is_set():
                    self.set_state(VoiceState.IDLE)
                    return last_reply

                first_turn = False

                # ------- 2. TRANSCRIBING -------
                self.set_state(VoiceState.TRANSCRIBING)
                stt_t0 = time.perf_counter()
                result = self.stt.transcribe(audio_bytes)
                rec.stt_ms = round((time.perf_counter() - stt_t0) * 1000.0, 1)

                user_text = result.text.strip()
                detected_lang = result.language
                rec.user_text = user_text[:60]

                self._broadcast({
                    "type": "transcript",
                    "role": "user",
                    "text": user_text,
                    "confidence": result.confidence,
                    "language": detected_lang,
                })

                # Low-confidence / empty guard
                if not user_text or (result.confidence < 0.15 and len(user_text) < 4):
                    logger.info("Low confidence STT (conf=%.2f) — fallback phrase.", result.confidence)
                    rec.fast_path = True
                    self._speak_phrase("Sorry boss, didn't catch that.", rec, speech_end, detected_lang)
                    rec.total_ms = round((time.perf_counter() - turn_start) * 1000.0, 1)
                    self._emit_metrics(rec)
                    if not allow_follow_up:
                        break
                    continue

                if self._interrupt_event.is_set():
                    self.set_state(VoiceState.IDLE)
                    return ""

                # ------- 3. FAST-PATH ROUTER -------
                self.set_state(VoiceState.THINKING)
                fast_reply = fast_route(user_text, pipeline=self)
                if fast_reply is not None:
                    logger.info("Fast-path handled: '%s' -> '%s'", user_text[:40], fast_reply)
                    rec.fast_path = True
                    rec.llm_first_ms = 0.0
                    try:
                        from app.memory.store import store
                        session_key = getattr(self, "current_session_id", "voice")
                        store.save_message(session_key, "user", user_text)
                        store.save_message(session_key, "assistant", fast_reply)
                    except Exception as exc:
                        logger.debug("Failed to persist fast-path voice turn: %s", exc)
                    self._speak_phrase(fast_reply, rec, speech_end, detected_lang)
                    rec.total_ms = round((time.perf_counter() - turn_start) * 1000.0, 1)
                    self._emit_metrics(rec)
                    last_reply = fast_reply
                    if not allow_follow_up:
                        break
                    continue

                # ------- 4. LAZY AGENT ATTACH -------
                if self.agent is None:
                    try:
                        from app.main import _build_agent
                        self.agent = _build_agent()
                        logger.info("Voice pipeline lazily attached Gemini agent.")
                    except Exception as exc:
                        logger.debug("Could not attach agent: %s", exc)

                if self.agent is None:
                    fallback = f"Voice test mode received: {user_text}"
                    self._speak_phrase(fallback, rec, speech_end, detected_lang)
                    rec.total_ms = round((time.perf_counter() - turn_start) * 1000.0, 1)
                    self._emit_metrics(rec)
                    last_reply = fallback
                    if not allow_follow_up:
                        break
                    continue

                # Attach filler speaker to agent
                if hasattr(self.agent, '_filler_speaker'):
                    self.agent._filler_speaker = lambda phrase: self._speak_phrase_nonblocking(phrase)

                # Persist user speech
                try:
                    from app.memory.store import store
                    session_key = getattr(self, "current_session_id", "voice")
                    store.save_message(session_key, "user", user_text)
                    from app import config
                    n_exchanges = getattr(config, "SHORT_TERM_EXCHANGES", 5)
                    history_turns = store.get_recent_exchanges(session_key, n_exchanges=n_exchanges)
                    messages = [{"role": m["role"], "content": m["text"]} for m in history_turns]
                except Exception:
                    messages = [{"role": "user", "content": user_text}]

                # ------- 5. STREAM AGENT REPLY -------
                llm_t0 = time.perf_counter()

                full_reply = self._stream_agent_turn(
                    messages=messages,
                    llm_start=llm_t0,
                    speech_end=speech_end,
                    rec=rec,
                    detected_lang=detected_lang,
                )

                # Pull timing from agent internals
                if self.agent:
                    rec.memory_ms = getattr(self.agent, "last_memory_search_ms", 0.0)
                    rec.prompt_build_ms = getattr(self.agent, "last_prompt_build_ms", 0.0)
                    rec.tool_calls_ms = getattr(self.agent, "last_tool_calls_ms", 0.0)

                rec.total_ms = round((time.perf_counter() - turn_start) * 1000.0, 1)
                self._emit_metrics(rec)
                last_reply = full_reply

                if not allow_follow_up:
                    break

            self.set_state(VoiceState.IDLE)
            return last_reply

        except Exception as exc:
            logger.exception("Error in voice turn: %s", exc)
            self.set_state(VoiceState.IDLE)
            return f"(Voice turn error: {exc})"

    # ------------------------------------------------------------------
    # Streaming LLM turn
    # ------------------------------------------------------------------

    def _stream_agent_turn(
        self,
        messages: list[dict[str, Any]],
        llm_start: float,
        speech_end: float,
        rec: Any,
        detected_lang: str | None,
        turn_id: int = 0,
    ) -> str:
        """Stream LLM output, splitting into sentences, prefetching TTS, and playing immediately."""
        import queue
        first_token_recorded = False
        first_audio_played = False
        full_chunks: list[str] = []
        sentence_buffer = ""
        first_chunk_done = False

        sentence_q: queue.Queue[str | None] = queue.Queue()
        audio_q: queue.Queue[tuple[str, bytes] | None] = queue.Queue(maxsize=3)

        # Worker thread: prefetch TTS synthesis while audio plays
        def _tts_prefetcher():
            while not self._interrupt_event.is_set() and (not turn_id or self._current_turn_id == turn_id):
                try:
                    sent = sentence_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if sent is None:
                    audio_q.put(None)
                    sentence_q.task_done()
                    break
                try:
                    audio = self.tts.synthesize(sent, language=detected_lang)
                except Exception as exc:
                    logger.warning("Prefetch TTS failed for '%s': %s", sent[:40], exc)
                    audio = b""
                if not self._interrupt_event.is_set() and (not turn_id or self._current_turn_id == turn_id):
                    audio_q.put((sent, audio))
                sentence_q.task_done()

        prefetch_thread = threading.Thread(target=_tts_prefetcher, daemon=True)
        prefetch_thread.start()

        # Producer thread: consumes LLM stream and pushes sentences to sentence_q
        def _llm_producer():
            nonlocal sentence_buffer, first_chunk_done, first_token_recorded
            stopped_for_tts = False
            try:
                stream_gen: Generator[str, None, None] = (
                    self.agent.chat_stream(messages)  # type: ignore[union-attr]
                    if hasattr(self.agent, "chat_stream")
                    else iter([self.agent.chat(messages)])  # type: ignore[union-attr]
                )
                sentence_accumulator: list[str] = []
                for chunk in stream_gen:
                    if not chunk or self._interrupt_event.is_set() or (turn_id and self._current_turn_id != turn_id):
                        continue
                    if not first_token_recorded:
                        first_token_recorded = True
                        rec.llm_first_ms = round((time.perf_counter() - llm_start) * 1000.0, 1)

                    full_chunks.append(chunk)

                    if stopped_for_tts:
                        # Full response continues accumulating in full_chunks for text display,
                        # but no more audio is sent to TTS.
                        continue

                    sentence_buffer += chunk

                    # If delimiter '---' is encountered in streaming, STOP queuing speech for TTS!
                    if "---" in sentence_buffer:
                        parts = sentence_buffer.split("---", 1)
                        spoken_part = parts[0].strip()
                        sentence_buffer = ""
                        if sentence_accumulator:
                            spoken_part = " ".join(sentence_accumulator + ([spoken_part] if spoken_part else "")).strip()
                            sentence_accumulator = []
                        if spoken_part:
                            clean = sanitize_text_for_speech(spoken_part)
                            if clean:
                                sentence_q.put(clean)
                        stopped_for_tts = True
                        sentence_q.put(None)
                        continue

                    # Group text into 1-2 sentence chunks (>= ~40 chars). Never split mid-sentence.
                    while True:
                        m = re.search(r"([.!?]+(?:\s+|\n+)|[\n]{2,})", sentence_buffer)
                        if not m:
                            break
                        end_pos = m.end()
                        sentence_part = sentence_buffer[:end_pos].strip()
                        sentence_buffer = sentence_buffer[end_pos:]
                        clean = sanitize_text_for_speech(sentence_part)
                        if clean:
                            sentence_accumulator.append(clean)

                        # Emit when accumulated text is at least 40 chars or has 2 sentences
                        accum_len = sum(len(s) for s in sentence_accumulator)
                        if accum_len >= 40 or len(sentence_accumulator) >= 2:
                            chunk_text = " ".join(sentence_accumulator).strip()
                            if chunk_text:
                                sentence_q.put(chunk_text)
                            sentence_accumulator = []

                # Flush remaining text if not already stopped for TTS
                if not stopped_for_tts:
                    if sentence_buffer.strip():
                        clean_rem = sanitize_text_for_speech(sentence_buffer.strip())
                        if clean_rem:
                            sentence_accumulator.append(clean_rem)
                    if sentence_accumulator and not self._interrupt_event.is_set() and (not turn_id or self._current_turn_id == turn_id):
                        chunk_text = " ".join(sentence_accumulator).strip()
                        if chunk_text:
                            sentence_q.put(chunk_text)
                        sentence_accumulator = []
            except Exception as e:
                logger.error("LLM streaming error: %s", e)
            finally:
                if not stopped_for_tts:
                    sentence_q.put(None)

        producer_thread = threading.Thread(target=_llm_producer, daemon=True)
        producer_thread.start()

        # Consumer: feeds audio items into continuous gapless stream
        stream_started = False
        try:
            while not self._interrupt_event.is_set() and (not turn_id or self._current_turn_id == turn_id):
                try:
                    item = audio_q.get(timeout=0.1)
                except queue.Empty:
                    if not producer_thread.is_alive() and sentence_q.empty() and audio_q.empty():
                        break
                    continue
                if item is None:
                    break
                sent, audio = item
                if audio and not self._interrupt_event.is_set() and (not turn_id or self._current_turn_id == turn_id):
                    self.set_state(VoiceState.SPEAKING)
                    try:
                        from app.voice.audio_io import compute_rms
                        audio_rms = compute_rms(audio)
                        self._broadcast({"type": "speech_level", "rms": round(audio_rms, 1)})
                    except Exception:
                        pass
                    if not first_audio_played:
                        first_audio_played = True
                        rec.tts_first_ms = round((time.perf_counter() - speech_end) * 1000.0, 1)
                        if getattr(rec, "release_time", None) is not None:
                            rec.release_to_first_audio_ms = round((time.perf_counter() - rec.release_time) * 1000.0, 1)
                    if hasattr(self.player, "start_gapless_stream") and hasattr(self.player, "push_gapless_chunk"):
                        if not stream_started:
                            self.player.start_gapless_stream(sample_rate=24000)
                            stream_started = True
                        self.player.push_gapless_chunk(audio, normalize=True, fade=True)
                    else:
                        self.player.play_audio_bytes(audio, block=True)
                audio_q.task_done()
        finally:
            if stream_started and hasattr(self.player, "finish_gapless_stream"):
                self.player.finish_gapless_stream()

        producer_thread.join(timeout=2.0)
        prefetch_thread.join(timeout=2.0)

        full_response = "".join(full_chunks).strip()
        rec.llm_total_ms = round((time.perf_counter() - llm_start) * 1000.0, 1)

        self._broadcast({"type": "transcript", "role": "assistant", "text": full_response})

        # Persist assistant reply
        try:
            from app.memory.store import store
            store.save_message("voice", "assistant", full_response)
        except Exception:
            pass

        return full_response

    # ------------------------------------------------------------------
    # Speaking helpers
    # ------------------------------------------------------------------

    def _speak_phrase(
        self,
        phrase: str,
        rec: Any = None,
        speech_end: float | None = None,
        language: str | None = None,
    ) -> None:
        """Synthesize and speak a single phrase (blocking)."""
        spoken = phrase.split("---", 1)[0].strip() if "---" in phrase else phrase
        clean = sanitize_text_for_speech(spoken)
        if not clean or self._interrupt_event.is_set():
            return

        self.set_state(VoiceState.SPEAKING)
        self._broadcast({"type": "transcript", "role": "assistant", "text": phrase})

        try:
            audio = self.tts.synthesize(clean, language=language)
            if rec is not None:
                if speech_end is not None:
                    rec.tts_first_ms = round((time.perf_counter() - speech_end) * 1000.0, 1)
                if getattr(rec, "release_time", None) is not None and getattr(rec, "release_to_first_audio_ms", 0.0) == 0.0:
                    rec.release_to_first_audio_ms = round((time.perf_counter() - rec.release_time) * 1000.0, 1)
            if audio and not self._interrupt_event.is_set():
                try:
                    from app.voice.audio_io import compute_rms
                    audio_rms = compute_rms(audio)
                    self._broadcast({"type": "speech_level", "rms": round(audio_rms, 1)})
                except Exception:
                    pass
                self.player.play_audio_bytes(audio, block=True)
        except Exception as exc:
            logger.error("Failed to speak phrase '%s': %s", clean, exc)

    def _speak_phrase_nonblocking(self, phrase: str) -> None:
        """Speak a filler phrase in a background thread (non-blocking)."""
        if self._interrupt_event.is_set() or self._state == VoiceState.SPEAKING:
            return
        threading.Thread(
            target=self._speak_phrase,
            args=(phrase,),
            daemon=True,
        ).start()

    def _emit_metrics(self, rec: Any) -> None:
        """Broadcast metrics to the dashboard and log the summary table."""
        try:
            engines = self.tts.get_last_turn_engines()
            rec.tts_engines = engines
        except Exception:
            engines = getattr(rec, "tts_engines", [])

        rel_audio = getattr(rec, "release_to_first_audio_ms", 0.0)
        rel_str = f"Rel->Audio={rel_audio:.0f}ms " if rel_audio > 0 else ""
        eng_str = f"TTS-Engines={len(engines)} " if engines else ""
        perf_line = (
            f"[PERF] Turn #{rec.turn_id}: "
            f"STT={rec.stt_ms:.0f}ms "
            f"Mem={rec.memory_ms:.0f}ms "
            f"Prompt={rec.prompt_build_ms:.0f}ms "
            f"LLM1st={rec.llm_first_ms:.0f}ms "
            f"LLMtot={rec.llm_total_ms:.0f}ms "
            f"Tools={rec.tool_calls_ms:.0f}ms "
            f"TTS1st={rec.tts_first_ms:.0f}ms "
            f"{rel_str}"
            f"{eng_str}"
            f"Total={rec.total_ms:.0f}ms "
            f"FP={'Y' if rec.fast_path else 'N'}"
        )
        logger.info(perf_line)
        print(perf_line)
        self._broadcast({
            "type": "metrics",
            "metrics": {
                "stt_ms": rec.stt_ms,
                "memory_search_ms": rec.memory_ms,
                "prompt_build_ms": rec.prompt_build_ms,
                "llm_first_token_ms": rec.llm_first_ms,
                "llm_total_ms": rec.llm_total_ms,
                "tool_calls_ms": rec.tool_calls_ms,
                "tts_first_audio_ms": rec.tts_first_ms,
                "tts_first_word_ms": rec.tts_first_ms,
                "release_to_first_audio_ms": rel_audio,
                "total_turn_ms": rec.total_ms,
                "fast_path": rec.fast_path,
                "tts_engines": engines,
            },
        })

    def speak_reminder(self, reminder_text: str) -> None:
        """Speak an alert for a scheduled due reminder."""
        logger.info("Speaking due reminder: %s", reminder_text)
        announcement = f"Boss, reminder: {reminder_text}"
        threading.Thread(
            target=self._speak_phrase,
            args=(announcement,),
            daemon=True,
        ).start()


# Global pipeline singleton
default_pipeline = VoicePipeline()
