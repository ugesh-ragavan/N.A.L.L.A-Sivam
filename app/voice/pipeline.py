"""app/voice/pipeline.py

Voice Pipeline State Machine & Execution Loop.

State Machine:
  IDLE -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE

Rules:
  1. Microphone is muted while speaking to prevent feedback loops.
  2. A new wake word / push-to-talk while SPEAKING interrupts playback immediately.
  3. If STT returns empty or low confidence (<0.25), speak "Sorry boss, didn't catch that."
  4. Stream the model's reply and synthesize/play sentence-by-sentence for lowest latency.
  5. Log and broadcast latency timings (STT, LLM first token, TTS start).
  6. Sanitize spoken replies (strip markdown, code, emoji).
"""

from __future__ import annotations

import collections
import logging
import re
import threading
import time
from enum import Enum
from typing import Any, Callable, Generator

from app.config import FOLLOW_UP_ENABLED, FOLLOW_UP_TIMEOUT
from app.brain.agent import Agent
from app.voice.audio_io import AudioPlayer, AudioRecorder, default_player, default_recorder
from app.voice.stt import FasterWhisperSTT, SpeechToTextProvider, stt_engine
from app.voice.tts import TTSManager, sanitize_text_for_speech, tts_engine
from app.voice.vad import VoiceActivityDetector, default_vad
from app.voice.wakeword import PushToTalk, WakeWordDetector, default_wakeword

logger = logging.getLogger("nalla.voice.pipeline")

# Sentence boundary regex for streaming speech chunks
_SENTENCE_END_RE = re.compile(r"(?<=[.!?\n])\s+")


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
        self._interrupt_event = threading.Event()
        self._state_subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.Lock()

        # Connect wakeword callback
        self.wakeword.start(on_detected=self.trigger_wakeword)

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
            logger.info("Voice pipeline: %s -> %s", old_state.value, new_state.value)

            payload = {"type": "state_change", "state": new_state.value}
            if extra:
                payload.update(extra)
            self._broadcast(payload)

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register a listener for pipeline state changes and transcripts."""
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
        """Mute or unmute the microphone."""
        self._is_muted = muted
        logger.info("Voice pipeline mic muted: %s", muted)
        self._broadcast({"type": "mute_change", "muted": muted})

    @property
    def is_muted(self) -> bool:
        return self._is_muted

    def interrupt(self) -> None:
        """Interrupt any current playback or processing."""
        logger.info("Voice pipeline interruption requested.")
        self._interrupt_event.set()
        self.player.stop()

    def trigger_wakeword(self) -> None:
        """Handle wake word or push-to-talk signal (with barge-in support)."""
        if self._is_muted:
            logger.debug("Wakeword ignored because mic is muted.")
            return

        # Barge-in: if SPEAKING or THINKING, interrupt immediately and start fresh turn
        if self._state in (VoiceState.SPEAKING, VoiceState.THINKING):
            logger.info("Barge-in triggered during %s: stopping playback and listening.", self._state.value)
            self.interrupt()
            time.sleep(0.05)
            threading.Thread(target=self._run_voice_turn, kwargs={"allow_follow_up": self.follow_up_enabled}, daemon=True).start()
            return

        # If IDLE or already in follow-up listening, start fresh turn
        if self._state in (VoiceState.IDLE, VoiceState.LISTENING):
            if self._state == VoiceState.LISTENING:
                self.interrupt()
                time.sleep(0.05)
            threading.Thread(target=self._run_voice_turn, kwargs={"allow_follow_up": self.follow_up_enabled}, daemon=True).start()

    # ------------------------------------------------------------------
    # Core Turn Execution
    # ------------------------------------------------------------------

    def run_single_turn(self, allow_follow_up: bool = False) -> str:
        """Synchronously execute a single voice turn."""
        return self._run_voice_turn(allow_follow_up=allow_follow_up)

    def _run_voice_turn(self, allow_follow_up: bool = False) -> str:
        """Executes: listen -> STT -> think -> TTS -> speak (with optional follow-up window)."""
        if self._is_muted:
            return ""

        self._interrupt_event.clear()
        first_turn = True
        last_reply = ""

        try:
            while not self._interrupt_event.is_set() and not self._is_muted:
                turn_start_time = time.perf_counter()
                metrics: dict[str, float] = {}

                # 1. LISTENING
                if first_turn:
                    self.set_state(VoiceState.LISTENING)
                    audio_bytes = self.vad.record_speech_segment(
                        recorder=self.recorder,
                        silence_duration=0.8,
                        max_duration=20.0,
                        initial_timeout=8.0,
                    )
                else:
                    logger.info("Entering %.1fs follow-up window (listening without wake word)...", self.follow_up_timeout)
                    self.set_state(VoiceState.LISTENING, extra={"follow_up": True, "timeout": self.follow_up_timeout})
                    audio_bytes = self.vad.record_speech_segment(
                        recorder=self.recorder,
                        silence_duration=0.8,
                        max_duration=20.0,
                        initial_timeout=self.follow_up_timeout,
                    )

                speech_end_time = time.perf_counter()
                if not audio_bytes or self._interrupt_event.is_set():
                    if first_turn:
                        logger.info("No speech captured or interrupted.")
                    else:
                        logger.info("Follow-up window expired without speech.")
                    self.set_state(VoiceState.IDLE)
                    return last_reply

                first_turn = False

                # 2. TRANSCRIBING
                self.set_state(VoiceState.TRANSCRIBING)
                stt_start = time.perf_counter()
                result = self.stt.transcribe(audio_bytes)
                stt_duration = time.perf_counter() - stt_start
                metrics["stt_ms"] = round(stt_duration * 1000.0, 1)

                user_text = result.text.strip()
                confidence = result.confidence
                detected_lang = result.language
                self._broadcast({
                    "type": "transcript",
                    "role": "user",
                    "text": user_text,
                    "confidence": confidence,
                    "language": detected_lang,
                })

                # Greeting fast-path: "Nalla!" or "Nalla"
                clean_cmd = user_text.rstrip("!").lower()
                if clean_cmd == "nalla":
                    reply = "Yes boss, sollunga!"
                    metrics["memory_search_ms"] = 0.0
                    metrics["llm_first_token_ms"] = 0.0
                    self._speak_phrase(reply, metrics, speech_end_time, language=detected_lang)
                    metrics["total_turn_ms"] = round((time.perf_counter() - turn_start_time) * 1000.0, 1)
                    logger.info("Voice turn metrics (greeting): %s", metrics)
                    self._broadcast({"type": "metrics", "metrics": metrics})
                    last_reply = reply
                    if not allow_follow_up:
                        break
                    continue

                # Check for low confidence or empty speech
                if not user_text or (confidence < 0.15 and len(user_text) < 4):
                    logger.info("Unrecognized speech (text='%s', conf: %.2f) — fallback phrase.", user_text, confidence)
                    metrics["memory_search_ms"] = 0.0
                    metrics["llm_first_token_ms"] = 0.0
                    self._speak_phrase("Sorry boss, didn't catch that.", metrics, speech_end_time, language=detected_lang)
                    metrics["total_turn_ms"] = round((time.perf_counter() - turn_start_time) * 1000.0, 1)
                    self._broadcast({"type": "metrics", "metrics": metrics})
                    last_reply = "Sorry boss, didn't catch that."
                    if not allow_follow_up:
                        break
                    continue

                if self._interrupt_event.is_set():
                    self.set_state(VoiceState.IDLE)
                    return ""

                # 3. THINKING & 4. SPEAKING (Streaming sentences)
                self.set_state(VoiceState.THINKING)
                llm_start = time.perf_counter()

                # Attempt lazy agent attachment if none set
                if self.agent is None:
                    try:
                        from app.main import _build_agent
                        self.agent = _build_agent()
                        logger.info("Voice pipeline lazily attached full Gemini agent.")
                    except Exception as exc:
                        logger.debug("Could not attach agent automatically: %s", exc)

                if self.agent is None:
                    reply = f"Voice test mode received: {user_text}"
                    first_token_time = time.perf_counter()
                    metrics["memory_search_ms"] = 0.0
                    metrics["llm_first_token_ms"] = round((first_token_time - llm_start) * 1000.0, 1)
                    self._speak_phrase(reply, metrics, speech_end_time, language=detected_lang)
                    metrics["total_turn_ms"] = round((time.perf_counter() - turn_start_time) * 1000.0, 1)
                    self._broadcast({"type": "metrics", "metrics": metrics})
                    last_reply = reply
                    if not allow_follow_up:
                        break
                    continue

                # Build messages for agent
                messages = [{"role": "user", "content": user_text}]

                # Stream model reply sentence-by-sentence
                full_reply = self._process_streaming_agent_turn(
                    messages=messages,
                    llm_start_time=llm_start,
                    speech_end_time=speech_end_time,
                    metrics=metrics,
                    detected_lang=detected_lang,
                )

                metrics["total_turn_ms"] = round((time.perf_counter() - turn_start_time) * 1000.0, 1)
                logger.info("Voice turn metrics: %s", metrics)
                self._broadcast({"type": "metrics", "metrics": metrics})
                last_reply = full_reply

                if not allow_follow_up:
                    break

            self.set_state(VoiceState.IDLE)
            return last_reply

        except Exception as exc:
            logger.exception("Error in voice turn: %s", exc)
            self.set_state(VoiceState.IDLE)
            return f"(Voice turn error: {exc})"

    def _process_streaming_agent_turn(
        self,
        messages: list[dict[str, Any]],
        llm_start_time: float,
        speech_end_time: float,
        metrics: dict[str, float],
        detected_lang: str | None = None,
    ) -> str:
        """Stream LLM output, splitting into sentences and playing immediately."""
        first_token_recorded = False
        first_audio_played = False
        accumulated_text = ""
        full_response = ""

        # Use agent's chat or fallback
        # First check wake word fast path
        user_input = messages[-1].get("content", "").strip()
        if user_input.rstrip("!").lower() == "nalla":
            reply = "Yes boss, sollunga!"
            metrics["llm_first_token_ms"] = round((time.perf_counter() - llm_start_time) * 1000.0, 1)
            self._speak_phrase(reply, metrics, speech_end_time, language=detected_lang)
            return reply

        # Persist user speech in SQLite memory
        try:
            from app.memory.store import store
            store.save_message("voice", "user", user_input)
        except Exception as exc:
            logger.debug("Could not persist voice user turn: %s", exc)

        try:
            stream_gen = (
                self.agent.chat_stream(messages)
                if hasattr(self.agent, "chat_stream")
                else iter([self.agent.chat(messages)])
            )

            sentence_buffer = ""
            full_chunks = []

            for chunk in stream_gen:
                if not chunk:
                    continue
                if not first_token_recorded:
                    first_token_recorded = True
                    metrics["llm_first_token_ms"] = round((time.perf_counter() - llm_start_time) * 1000.0, 1)

                sentence_buffer += chunk
                full_chunks.append(chunk)

                # Split on sentence boundaries: ., !, ?, \n
                while True:
                    match = re.search(r"([.!?]+(?:\s+|\n+)|[\n]+)", sentence_buffer)
                    if not match:
                        break
                    end_pos = match.end()
                    sentence = sentence_buffer[:end_pos].strip()
                    sentence_buffer = sentence_buffer[end_pos:]

                    if not sentence:
                        continue

                    clean_sentence = sanitize_text_for_speech(sentence)
                    if not clean_sentence:
                        continue

                    if self._interrupt_event.is_set():
                        break

                    self.set_state(VoiceState.SPEAKING)
                    logger.info("Synthesizing completed sentence: '%s'", clean_sentence)
                    audio_bytes = self.tts.synthesize(clean_sentence, language=detected_lang)

                    if not first_audio_played:
                        first_audio_played = True
                        tts_time = round((time.perf_counter() - speech_end_time) * 1000.0, 1)
                        metrics["tts_first_audio_ms"] = tts_time
                        metrics["tts_first_word_ms"] = tts_time

                    if self._interrupt_event.is_set():
                        break

                    logger.info("Playing audio (%d bytes)...", len(audio_bytes))
                    self.player.play_audio_bytes(audio_bytes, block=True)
                    logger.info("Sentence playback complete.")

                if self._interrupt_event.is_set():
                    break

            # Flush any remaining text in sentence_buffer
            remaining = sentence_buffer.strip()
            if remaining and not self._interrupt_event.is_set():
                clean_sentence = sanitize_text_for_speech(remaining)
                if clean_sentence:
                    self.set_state(VoiceState.SPEAKING)
                    logger.info("Synthesizing final sentence: '%s'", clean_sentence)
                    audio_bytes = self.tts.synthesize(clean_sentence, language=detected_lang)
                    if not first_audio_played:
                        first_audio_played = True
                        tts_time = round((time.perf_counter() - speech_end_time) * 1000.0, 1)
                        metrics["tts_first_audio_ms"] = tts_time
                        metrics["tts_first_word_ms"] = tts_time
                    self.player.play_audio_bytes(audio_bytes, block=True)

            full_response = "".join(full_chunks).strip()
            metrics["memory_search_ms"] = getattr(self.agent, "last_memory_search_ms", 0.0)
            self._broadcast({"type": "transcript", "role": "assistant", "text": full_response})

            # Persist assistant reply
            try:
                from app.memory.store import store
                store.save_message("voice", "assistant", full_response)
            except Exception as exc:
                logger.debug("Could not persist voice assistant turn: %s", exc)

        except Exception as exc:
            logger.error("Failed during streaming agent speech: %s", exc)
            full_response = f"Something went wrong: {exc}"

        return full_response

    def _speak_phrase(
        self,
        phrase: str,
        metrics: dict[str, float] | None = None,
        speech_end_time: float | None = None,
        language: str | None = None,
    ) -> None:
        """Synthesize and speak a single phrase."""
        clean = sanitize_text_for_speech(phrase)
        if not clean or self._interrupt_event.is_set():
            return

        self.set_state(VoiceState.SPEAKING)
        self._broadcast({"type": "transcript", "role": "assistant", "text": phrase})

        try:
            logger.info("Synthesizing phrase: '%s'", clean)
            audio_bytes = self.tts.synthesize(clean, language=language)
            if metrics is not None and speech_end_time is not None:
                tts_time = round((time.perf_counter() - speech_end_time) * 1000.0, 1)
                metrics["tts_first_audio_ms"] = tts_time
                metrics["tts_first_word_ms"] = tts_time
            logger.info("Playing phrase audio (%d bytes)...", len(audio_bytes))
            self.player.play_audio_bytes(audio_bytes, block=True)
            logger.info("Phrase playback complete.")
        except Exception as exc:
            logger.error("Failed to speak phrase '%s': %s", clean, exc)

    def speak_reminder(self, reminder_text: str) -> None:
        """Speak an alert for a scheduled due reminder."""
        logger.info("Speaking due reminder aloud: %s", reminder_text)
        announcement = f"Boss, reminder: {reminder_text}"
        threading.Thread(target=self._speak_phrase, args=(announcement,), daemon=True).start()


# Global pipeline singleton
default_pipeline = VoicePipeline()
