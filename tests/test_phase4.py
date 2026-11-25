"""tests/test_phase4.py

Phase 4 unit and integration tests — audio I/O, VAD, STT, TTS, WakeWord, Voice Pipeline, and Voice Web UI.

All tests are offline and run without requiring external network services.
"""

from __future__ import annotations

import io
import math
import wave
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.brain.llm_provider import LLMProvider, LLMResponse
from app.brain.persona import Persona
from app.brain.tools import ToolRegistry
from app.core.approvals import ApprovalManager
from app.brain.agent import Agent
from app.voice.audio_io import (
    AudioPlayer,
    AudioRecorder,
    decode_audio_to_array,
    list_input_devices,
    list_output_devices,
)
from app.voice.vad import VoiceActivityDetector, BYTES_PER_FRAME, FRAME_DURATION_MS
from app.voice.stt import STTResult, clean_transcription_text, FasterWhisperSTT
from app.voice.tts import (
    EdgeTTS,
    PiperTTS,
    TTSManager,
    _generate_offline_beep_wav,
    sanitize_text_for_speech,
)
from app.voice.wakeword import PushToTalk, get_wakeword_detector
from app.voice.pipeline import VoicePipeline, VoiceState
from app.web.server import app


# ---------------------------------------------------------------------------
# 1. Audio I/O Tests
# ---------------------------------------------------------------------------

class TestAudioIO:
    def test_list_audio_devices(self):
        in_devs = list_input_devices()
        out_devs = list_output_devices()
        assert isinstance(in_devs, list)
        assert isinstance(out_devs, list)

    def test_player_interrupt_flag(self):
        player = AudioPlayer()
        assert not player.is_playing
        player.stop()
        assert player._stop_requested.is_set()

    def test_decode_wav_audio_to_array(self):
        # Generate a small 100ms sine wave WAV in memory
        wav_bytes = _generate_offline_beep_wav()
        array, rate = decode_audio_to_array(wav_bytes)
        assert rate == 16000
        assert len(array) > 0


# ---------------------------------------------------------------------------
# 2. VAD Tests
# ---------------------------------------------------------------------------

class TestVAD:
    def test_frame_size_constants(self):
        assert FRAME_DURATION_MS == 20
        assert BYTES_PER_FRAME == 640  # 320 samples * 2 bytes

    def test_silence_detection(self):
        vad = VoiceActivityDetector(aggressiveness=3)
        silence_frame = b"\x00" * BYTES_PER_FRAME
        is_speech = vad.is_speech(silence_frame)
        assert is_speech is False


# ---------------------------------------------------------------------------
# 3. Speech-to-Text & Hallucination Filter Tests
# ---------------------------------------------------------------------------

class TestSTT:
    def test_clean_hallucinations(self):
        raw = "Hello boss [music] thank you for watching, please subscribe to the channel."
        cleaned = clean_transcription_text(raw)
        assert "music" not in cleaned
        assert "thank you for watching" not in cleaned
        assert "subscribe" not in cleaned
        assert cleaned == "Hello boss ," or cleaned.startswith("Hello boss")

    def test_stt_result_is_empty(self):
        r1 = STTResult(text="   ", confidence=0.0, language="en")
        assert r1.is_empty is True
        r2 = STTResult(text="hello", confidence=0.9, language="en")
        assert r2.is_empty is False


# ---------------------------------------------------------------------------
# 4. Text-to-Speech & Sanitizer Tests
# ---------------------------------------------------------------------------

class TestTTS:
    def test_sanitize_text_strips_markdown_and_emoji(self):
        text = "Hello **boss**! Check this `int x = 5;` and visit [GitHub](https://github.com). 😊🚀\n```python\nprint(1)\n```"
        cleaned = sanitize_text_for_speech(text)
        assert "**" not in cleaned
        assert "`" not in cleaned
        assert "😊" not in cleaned
        assert "🚀" not in cleaned
        assert "https://" not in cleaned
        assert "boss" in cleaned
        assert "GitHub" in cleaned

    def test_tamil_voice_selection(self):
        edge = EdgeTTS(voice_en="en-IN-NeerjaNeural", voice_ta="ta-IN-PallaviNeural")
        # English sentence
        v1 = edge._select_voice("Hello boss, how are you?")
        assert v1 == "en-IN-NeerjaNeural"

        # Tamil sentence
        v2 = edge._select_voice("வணக்கம் பாஸ், எப்படி இருக்கீங்க?")
        assert v2 == "ta-IN-PallaviNeural"

    def test_tts_offline_fallback(self):
        class FailingEdgeTTS(EdgeTTS):
            def synthesize(self, text, voice=None):
                raise RuntimeError("Internet disconnected")

        failing_edge = FailingEdgeTTS()
        piper = PiperTTS()
        manager = TTSManager(primary_engine="edge-tts", edge_provider=failing_edge, piper_provider=piper)

        # Should transparently catch failure and use offline fallback without crashing
        audio = manager.synthesize("Test fallback speech")
        assert len(audio) > 0


# ---------------------------------------------------------------------------
# 5. Wake Word Tests
# ---------------------------------------------------------------------------

class TestWakeWord:
    def test_pushtotalk_trigger(self):
        ptt = PushToTalk()
        triggered = []
        ptt.start(on_detected=lambda: triggered.append(True))
        ptt.trigger()
        assert len(triggered) == 1
        assert ptt.wait_for_detection(timeout=0.1) is True

    def test_get_wakeword_detector(self):
        d = get_wakeword_detector("pushtotalk")
        assert isinstance(d, PushToTalk)


# ---------------------------------------------------------------------------
# 6. Pipeline State Machine Tests
# ---------------------------------------------------------------------------

class DummyPlayer(AudioPlayer):
    def __init__(self):
        super().__init__()
        self.stopped = False
        self.played_audio = []

    def play_audio_bytes(self, audio_bytes, block=True):
        self.played_audio.append(audio_bytes)
        return True

    def stop(self):
        self.stopped = True
        super().stop()


class DummySTT(FasterWhisperSTT):
    def __init__(self, text="hello", conf=0.9):
        super().__init__()
        self._text = text
        self._conf = conf

    def transcribe(self, audio_bytes, language=None):
        return STTResult(text=self._text, confidence=self._conf, language="en")


class TestPipeline:
    def test_pipeline_initial_state(self):
        pipeline = VoicePipeline(
            recorder=AudioRecorder(),
            player=DummyPlayer(),
            stt=DummySTT(),
            wakeword=PushToTalk(),
        )
        assert pipeline.state == VoiceState.IDLE
        assert pipeline.is_muted is False

    def test_pipeline_mute_unmute(self):
        pipeline = VoicePipeline(
            recorder=AudioRecorder(),
            player=DummyPlayer(),
            stt=DummySTT(),
            wakeword=PushToTalk(),
        )
        pipeline.set_muted(True)
        assert pipeline.is_muted is True
        pipeline.set_muted(False)
        assert pipeline.is_muted is False

    def test_pipeline_interruption_stops_player(self):
        dummy_player = DummyPlayer()
        pipeline = VoicePipeline(
            recorder=AudioRecorder(),
            player=dummy_player,
            stt=DummySTT(),
            wakeword=PushToTalk(),
        )
        pipeline.set_state(VoiceState.SPEAKING)
        pipeline.interrupt()
        assert dummy_player.stopped is True

    def test_greeting_fast_path(self):
        dummy_player = DummyPlayer()
        pipeline = VoicePipeline(
            recorder=AudioRecorder(),
            player=dummy_player,
            stt=DummySTT(text="Nalla!"),
            wakeword=PushToTalk(),
        )
        # Mock vad to return audio
        pipeline.vad.record_speech_segment = lambda **kwargs: b"\x00" * 3200
        reply = pipeline.run_single_turn()
        assert reply == "Yes boss, sollunga!"
        assert len(dummy_player.played_audio) > 0

    def test_low_confidence_fallback(self):
        dummy_player = DummyPlayer()
        events_collected = []
        pipeline = VoicePipeline(
            recorder=AudioRecorder(),
            player=dummy_player,
            stt=DummySTT(text="", conf=0.0),
            wakeword=PushToTalk(),
        )
        pipeline.vad.record_speech_segment = lambda **kwargs: b"\x00" * 3200
        # Collect transcript events to verify fallback phrase was spoken
        pipeline.subscribe(lambda evt: events_collected.append(evt))
        pipeline.run_single_turn()
        transcript_texts = [
            e.get("text", "") for e in events_collected if e.get("type") == "transcript"
        ]
        assert any("catch that" in t for t in transcript_texts)

    def test_streaming_sentence_execution_and_timings(self):
        dummy_player = DummyPlayer()
        metrics_collected = []

        class StreamingAgent:
            last_memory_search_ms = 4.2
            last_prompt_build_ms = 1.0
            last_tool_calls_ms = 0.0
            def chat_stream(self, messages):
                yield "The answer is forty-two. "
                yield "Everything is running smoothly."

        pipeline = VoicePipeline(
            agent=StreamingAgent(),
            recorder=AudioRecorder(),
            player=dummy_player,
            # Use a phrase that bypasses fast-path (not time/date/greeting)
            stt=DummySTT(text="tell me something interesting", conf=0.95),
            wakeword=PushToTalk(),
        )
        pipeline.vad.record_speech_segment = lambda **kwargs: b"\x00" * 3200
        pipeline.subscribe(lambda evt: metrics_collected.append(evt) if evt.get("type") == "metrics" else None)

        reply = pipeline.run_single_turn()
        assert "forty-two" in reply
        assert len(dummy_player.played_audio) >= 1
        assert len(metrics_collected) == 1
        m = metrics_collected[0]["metrics"]
        assert "stt_ms" in m
        assert "memory_search_ms" in m
        assert "llm_first_token_ms" in m
        assert "tts_first_audio_ms" in m
        assert "total_turn_ms" in m

    def test_barge_in_during_speaking(self):
        """Barge-in: a trigger during SPEAKING immediately stops player and transitions to listening."""
        dummy_player = DummyPlayer()
        pipeline = VoicePipeline(
            recorder=AudioRecorder(),
            player=dummy_player,
            stt=DummySTT(text="stop", conf=0.9),
            wakeword=PushToTalk(),
        )
        pipeline.set_state(VoiceState.SPEAKING)
        assert dummy_player.stopped is False

        # Barge-in trigger
        pipeline.trigger_wakeword()
        assert dummy_player.stopped is True

    def test_follow_up_window_conversation(self):
        """Follow-up: speech within the follow-up window executes without wake word."""
        dummy_player = DummyPlayer()
        # Mock VAD to return audio on first call and follow-up call, then silence
        call_count = [0]
        def mock_record(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return b"\x00" * 3200
            return b""

        pipeline = VoicePipeline(
            recorder=AudioRecorder(),
            player=dummy_player,
            stt=DummySTT(text="Nalla!"),
            wakeword=PushToTalk(),
            follow_up_enabled=True,
            follow_up_timeout=0.2,
        )
        pipeline.vad.record_speech_segment = mock_record

        reply = pipeline._run_voice_turn(allow_follow_up=True)
        assert reply == "Yes boss, sollunga!"
        assert call_count[0] >= 2  # Ran initial turn and follow-up turn!
        assert pipeline.state == VoiceState.IDLE

    def test_tamil_voice_selection_bilingual(self):
        """Auto-detect selects Tamil voice for Tamil script or language='ta'."""
        edge = EdgeTTS(voice_en="en-IN-NeerjaNeural", voice_ta="ta-IN-PallaviNeural")
        # Tamil Unicode
        assert edge._select_voice("வணக்கம் பாஸ்") == "ta-IN-PallaviNeural"
        # Romanized Tamil with language tag
        assert edge._select_voice("Vanakkam boss", language="ta") == "ta-IN-PallaviNeural"
        # English
        assert edge._select_voice("Good morning boss", language="en") == "en-IN-NeerjaNeural"


# ---------------------------------------------------------------------------
# 7. Voice Web Dashboard API Tests
# ---------------------------------------------------------------------------

class TestVoiceWebAPI:
    def test_audio_devices_endpoint(self):
        client = TestClient(app)
        res = client.get("/api/audio/devices")
        assert res.status_code == 200
        data = res.json()
        assert "input_devices" in data
        assert "output_devices" in data

    def test_voice_mute_endpoint(self):
        client = TestClient(app)
        res = client.post("/api/voice/mute", json={"muted": True})
        assert res.status_code == 200
        assert res.json()["muted"] is True

        res2 = client.post("/api/voice/mute", json={"muted": False})
        assert res2.status_code == 200
        assert res2.json()["muted"] is False

    def test_voice_interrupt_endpoint(self):
        client = TestClient(app)
        res = client.post("/api/voice/interrupt")
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_dashboard_contains_voice_panel(self):
        client = TestClient(app)
        res = client.get("/")
        assert res.status_code == 200
        assert "NALLA SIVAM" in res.text
        assert "HOLD TO TALK" in res.text
