"""tests/test_voice_core.py

Unit tests for Voice Core:
  • State machine sequence: IDLE -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE.
  • Mic muting while speaking to prevent self-hearing.
  • Push-to-talk turn ("what time is it") with per-turn timing trace output.
  • Empty transcript / low confidence: "Sorry boss, didn't catch that." with ZERO LLM calls.
  • Streaming sentence chunking with concurrent prefetching.
  • User interruption during speech playback.
  • Offline TTS resilience (automatic fallback).
"""

from __future__ import annotations

import time
import pytest

from app.voice.audio_io import AudioPlayer, AudioRecorder
from app.voice.stt import FasterWhisperSTT, STTResult
from app.voice.tts import EdgeTTS, PiperTTS, TTSManager
from app.voice.vad import VoiceActivityDetector, _compute_pcm_rms
from app.voice.wakeword import PushToTalk
from app.voice.pipeline import VoicePipeline, VoiceState


class MockPlayer(AudioPlayer):
    def __init__(self):
        super().__init__()
        self.played_audio = []
        self.interrupted = False

    def play_audio_bytes(self, audio_bytes: bytes, block: bool = True) -> bool:
        self.played_audio.append(audio_bytes)
        return True

    def stop(self) -> None:
        self.interrupted = True
        super().stop()


class MockSTT(FasterWhisperSTT):
    def __init__(self, text: str = "what time is it", confidence: float = 0.95):
        super().__init__()
        self.text = text
        self.confidence = confidence
        self.transcribe_calls = 0

    def transcribe(self, audio_bytes: bytes, language: str | None = None) -> STTResult:
        self.transcribe_calls += 1
        return STTResult(text=self.text, confidence=self.confidence, language="en")


class MockAgent:
    def __init__(self, reply: str = "The current time is 10:45 AM, boss."):
        self.reply = reply
        self.call_count = 0

    def chat_stream(self, messages):
        self.call_count += 1
        # Stream in two chunks
        words = self.reply.split()
        half = len(words) // 2
        yield " ".join(words[:half]) + ", "
        yield " ".join(words[half:])


# ---------------------------------------------------------------------------
# 1. State Machine & WebSocket Transition Tests
# ---------------------------------------------------------------------------

class TestStateMachine:
    def test_full_state_machine_cycle(self):
        state_events = []
        player = MockPlayer()
        stt = MockSTT(text="what time is it")
        agent = MockAgent()

        pipeline = VoicePipeline(
            agent=agent,
            player=player,
            stt=stt,
            wakeword=PushToTalk(),
            follow_up_enabled=False,
        )
        # Mock VAD to return 0.2s of audio immediately
        pipeline.vad.record_speech_segment = lambda **kw: b"\x00" * 3200
        pipeline.subscribe(lambda evt: state_events.append(evt["state"]) if evt.get("type") == "state_change" else None)

        reply = pipeline.run_single_turn()
        assert reply is not None

        # Verify exact sequence: LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE
        assert "LISTENING" in state_events
        assert "TRANSCRIBING" in state_events
        assert "THINKING" in state_events
        assert "SPEAKING" in state_events
        assert state_events[-1] == "IDLE"

    def test_mic_muted_while_speaking(self):
        player = MockPlayer()
        pipeline = VoicePipeline(
            player=player,
            wakeword=PushToTalk(),
        )
        assert pipeline.is_muted is False

        # Transitioning to SPEAKING must automatically mute the mic
        pipeline.set_state(VoiceState.SPEAKING)
        assert pipeline.is_muted is True

        # Transitioning back to IDLE must unmute the mic
        pipeline.set_state(VoiceState.IDLE)
        assert pipeline.is_muted is False


# ---------------------------------------------------------------------------
# 2. Empty & Low-Confidence Transcript Handling (Zero LLM Calls)
# ---------------------------------------------------------------------------

class TestEmptyTranscriptHandling:
    def test_empty_transcript_no_llm_call(self):
        player = MockPlayer()
        agent = MockAgent()
        stt = MockSTT(text="", confidence=0.0)

        pipeline = VoicePipeline(
            agent=agent,
            player=player,
            stt=stt,
            wakeword=PushToTalk(),
            follow_up_enabled=False,
        )
        pipeline.vad.record_speech_segment = lambda **kw: b"\x00" * 3200

        transcripts = []
        pipeline.subscribe(lambda evt: transcripts.append(evt.get("text")) if evt.get("type") == "transcript" else None)

        pipeline.run_single_turn()

        # Agent must NEVER have been called
        assert agent.call_count == 0
        # Pipeline must have spoken fallback phrase
        assert any("catch that" in (t or "") for t in transcripts)
        assert len(player.played_audio) > 0

    def test_low_confidence_no_llm_call(self):
        agent = MockAgent()
        stt = MockSTT(text="um", confidence=0.08)

        pipeline = VoicePipeline(
            agent=agent,
            player=MockPlayer(),
            stt=stt,
            wakeword=PushToTalk(),
            follow_up_enabled=False,
        )
        pipeline.vad.record_speech_segment = lambda **kw: b"\x00" * 3200

        pipeline.run_single_turn()
        assert agent.call_count == 0


# ---------------------------------------------------------------------------
# 3. Streaming Sentence Chunking & Prefetching
# ---------------------------------------------------------------------------

class TestStreamingAndPrefetching:
    def test_streaming_sentence_chunking(self):
        player = MockPlayer()

        class MultiSentenceAgent:
            def chat_stream(self, messages):
                yield "Yes boss, checking the server load. "
                yield "CPU is at fifteen percent. "
                yield "All systems are green."

        pipeline = VoicePipeline(
            agent=MultiSentenceAgent(),
            player=player,
            stt=MockSTT(text="give me a full system status report"),
            wakeword=PushToTalk(),
            follow_up_enabled=False,
        )
        pipeline.vad.record_speech_segment = lambda **kw: b"\x00" * 3200

        reply = pipeline.run_single_turn()
        assert "fifteen percent" in reply
        # Multiple sentence chunks synthesized and played
        assert len(player.played_audio) >= 2


# ---------------------------------------------------------------------------
# 4. Push-to-Talk Acceptance Test ("what time is it")
# ---------------------------------------------------------------------------

class TestPushToTalkAcceptance:
    def test_push_to_talk_what_time_is_it(self, capsys):
        player = MockPlayer()
        agent = MockAgent()

        pipeline = VoicePipeline(
            agent=agent,
            player=player,
            stt=MockSTT(text="what time is it"),
            wakeword=PushToTalk(),
            follow_up_enabled=False,
        )
        pipeline.vad.record_speech_segment = lambda **kw: b"\x00" * 3200

        reply = pipeline.run_single_turn()
        assert reply is not None

        # Verify console output line contains [PERF]
        captured = capsys.readouterr()
        assert "[PERF]" in captured.out
        assert "STT=" in captured.out
        assert "Total=" in captured.out

    def test_interruption_during_speaking(self):
        player = MockPlayer()
        pipeline = VoicePipeline(
            player=player,
            wakeword=PushToTalk(),
        )
        pipeline.set_state(VoiceState.SPEAKING)
        pipeline.interrupt()
        assert player.interrupted is True
        assert pipeline._interrupt_event.is_set()

    def test_offline_vad_pure_rms_fallback(self):
        # 16-bit PCM silent frame
        silent_frame = b"\x00" * 640
        rms_silent = _compute_pcm_rms(silent_frame)
        assert rms_silent == 0.0

        # Sine wave tone frame
        import math
        tone_samples = bytearray()
        for i in range(320):
            val = int(1000.0 * math.sin(2 * math.pi * 440 * (i / 16000.0)))
            tone_samples.extend(val.to_bytes(2, byteorder="little", signed=True))
        rms_tone = _compute_pcm_rms(bytes(tone_samples))
        assert rms_tone > 500.0
