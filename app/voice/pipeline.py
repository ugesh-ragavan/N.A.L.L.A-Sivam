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
from app.voice.audio_io import AudioPlayer, AudioRecorder, default_player, default_recorder
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
        self._state_subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.Lock()

        # Connect wakeword callback
        self.wakeword.start(on_detected=self.trigger_wakeword)

        # Connect TTS fallback notification listener
        if hasattr(self.tts, "add_fallback_listener"):
            self.tts.add_fallback_listener(self._handle_tts_fallback)

    def _handle_tts_fallback(self, reason: str) -> None:
        logger.warning("TTS fallback active: %s", reason)
        self._broadcast({"type": "notice", "level": "warning", "message": reason})

    # ------------------------------------------------------------------
    # State & Event Broadcasting
    # ------------------------------------------------------------------

    @property
    def state(self) -> VoiceState:
        return self._state

    def set_state(self, new_state: VoiceState, extra: dict[str, Any] | None = None) -> None:
        with self._lock:
            old_state = self._state
            self._state = new_state

            # Mic is automatically muted while speaking to prevent self-hearing feedback loops
            if new_state == VoiceState.SPEAKING:
                self._speaking_muted = True
            elif old_state == VoiceState.SPEAKING and new_state != VoiceState.SPEAKING:
                self._speaking_muted = False

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
        self._interrupt_event.set()
        self.player.stop()

    def push_to_talk(self) -> None:
        """Trigger push-to-talk voice turn directly."""
        self.trigger_wakeword()

    def trigger_wakeword(self) -> None:
        """Handle wake word or push-to-talk (with barge-in support)."""
        if self._is_muted:
            logger.debug("Wakeword ignored because mic is muted.")
            return

        if self._state in (VoiceState.SPEAKING, VoiceState.THINKING):
            logger.info("Barge-in triggered during %s.", self._state.value)
            self.interrupt()
            time.sleep(0.05)
            threading.Thread(
                target=self._run_voice_turn,
                kwargs={"allow_follow_up": self.follow_up_enabled},
                daemon=True,
            ).start()
            return

        if self._state in (VoiceState.IDLE, VoiceState.LISTENING):
            if self._state == VoiceState.LISTENING:
                self.interrupt()
                time.sleep(0.05)
            threading.Thread(
                target=self._run_voice_turn,
                kwargs={"allow_follow_up": self.follow_up_enabled},
                daemon=True,
            ).start()

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
                    store.save_message("voice", "user", user_text)
                except Exception:
                    pass

                # ------- 5. STREAM AGENT REPLY -------
                llm_t0 = time.perf_counter()
                messages = [{"role": "user", "content": user_text}]

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
            while not self._interrupt_event.is_set():
                try:
                    sent = sentence_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if sent is None:
                    audio_q.put(None)
                    sentence_q.task_done()
                    break
                try:
                    audio = self.tts.synthesize(sent)
                except Exception as exc:
                    logger.warning("Prefetch TTS failed for '%s': %s", sent[:40], exc)
                    audio = b""
                audio_q.put((sent, audio))
                sentence_q.task_done()

        prefetch_thread = threading.Thread(target=_tts_prefetcher, daemon=True)
        prefetch_thread.start()

        # Producer thread: consumes LLM stream and pushes sentences to sentence_q
        def _llm_producer():
            nonlocal sentence_buffer, first_chunk_done, first_token_recorded
            try:
                stream_gen: Generator[str, None, None] = (
                    self.agent.chat_stream(messages)  # type: ignore[union-attr]
                    if hasattr(self.agent, "chat_stream")
                    else iter([self.agent.chat(messages)])  # type: ignore[union-attr]
                )
                for chunk in stream_gen:
                    if not chunk or self._interrupt_event.is_set():
                        continue
                    if not first_token_recorded:
                        first_token_recorded = True
                        rec.llm_first_ms = round((time.perf_counter() - llm_start) * 1000.0, 1)

                    full_chunks.append(chunk)
                    sentence_buffer += chunk

                    # Check for completed sentences or early clause (adapted from javis-os voice-chunker.js)
                    while True:
                        if not first_chunk_done:
                            words = sentence_buffer.split()
                            if len(words) >= 6:
                                m = re.search(r"([,;:]\s+|\s+(?:and|but|then|because|so|while)\s+)", sentence_buffer, re.IGNORECASE)
                                if m and m.start() >= 15:
                                    cut = m.end()
                                    chunk_text = sentence_buffer[:cut].strip()
                                    sentence_buffer = sentence_buffer[cut:]
                                    clean = sanitize_text_for_speech(chunk_text)
                                    if clean:
                                        sentence_q.put(clean)
                                    first_chunk_done = True
                                    continue
                        # Standard sentence split
                        m = re.search(r"([.!?]+(?:\s+|\n+)|[\n]+)", sentence_buffer)
                        if not m:
                            break
                        end_pos = m.end()
                        chunk_text = sentence_buffer[:end_pos].strip()
                        sentence_buffer = sentence_buffer[end_pos:]
                        if len(chunk_text) >= _MIN_SENTENCE_LEN:
                            clean = sanitize_text_for_speech(chunk_text)
                            if clean:
                                sentence_q.put(clean)
                            first_chunk_done = True

                # Flush remaining text
                rem = sentence_buffer.strip()
                if rem and not self._interrupt_event.is_set():
                    clean = sanitize_text_for_speech(rem)
                    if clean:
                        sentence_q.put(clean)
            except Exception as e:
                logger.error("LLM streaming error: %s", e)
            finally:
                sentence_q.put(None)

        producer_thread = threading.Thread(target=_llm_producer, daemon=True)
        producer_thread.start()

        # Consumer: plays audio items as they become available
        while not self._interrupt_event.is_set():
            try:
                item = audio_q.get(timeout=0.1)
            except queue.Empty:
                if not producer_thread.is_alive() and sentence_q.empty() and audio_q.empty():
                    break
                continue
            if item is None:
                break
            sent, audio = item
            if audio and not self._interrupt_event.is_set():
                self.set_state(VoiceState.SPEAKING)
                if not first_audio_played:
                    first_audio_played = True
                    rec.tts_first_ms = round((time.perf_counter() - speech_end) * 1000.0, 1)
                self.player.play_audio_bytes(audio, block=True)
            audio_q.task_done()

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
        clean = sanitize_text_for_speech(phrase)
        if not clean or self._interrupt_event.is_set():
            return

        self.set_state(VoiceState.SPEAKING)
        self._broadcast({"type": "transcript", "role": "assistant", "text": phrase})

        try:
            audio = self.tts.synthesize(clean, language=language)
            if rec is not None and speech_end is not None:
                rec.tts_first_ms = round((time.perf_counter() - speech_end) * 1000.0, 1)
            if audio and not self._interrupt_event.is_set():
                self.player.play_audio_bytes(audio, block=True)
        except Exception as exc:
            logger.error("Failed to speak phrase '%s': %s", clean, exc)

    def _speak_phrase_nonblocking(self, phrase: str) -> None:
        """Speak a filler phrase in a background thread (non-blocking)."""
        threading.Thread(
            target=self._speak_phrase,
            args=(phrase,),
            daemon=True,
        ).start()

    def _emit_metrics(self, rec: Any) -> None:
        """Broadcast metrics to the dashboard and log the summary table."""
        perf_line = (
            f"[PERF] Turn #{rec.turn_id}: "
            f"STT={rec.stt_ms:.0f}ms "
            f"Mem={rec.memory_ms:.0f}ms "
            f"Prompt={rec.prompt_build_ms:.0f}ms "
            f"LLM1st={rec.llm_first_ms:.0f}ms "
            f"LLMtot={rec.llm_total_ms:.0f}ms "
            f"Tools={rec.tool_calls_ms:.0f}ms "
            f"TTS1st={rec.tts_first_ms:.0f}ms "
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
                "total_turn_ms": rec.total_ms,
                "fast_path": rec.fast_path,
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
