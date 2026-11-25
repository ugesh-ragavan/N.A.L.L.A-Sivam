"""tests/test_gemini_tts.py

Unit and integration tests for Google Gemini TTS in Nalla Sivam Voice Studio:
  1. Unary decode (verify base64 audio decoding).
  2. Streaming chunk assembly (verify PCM assembly to WAV).
  3. Fallback on 429 / QuotaExceededError (Gemini -> EdgeTTS -> Piper).
  4. Fallback on timeout (> 4.0s).
  5. Cache hit (cached audio returned without API call).
  6. Pronunciation map (Tanglish replaced with Tamil script).
  7. Quota guard (daily cap and QuotaExceededError).
  8. Voice Studio endpoints (/api/voice/status, /api/voice/quota/cap, /api/voice/pronunciation, /api/voice/extended-library).
"""

from __future__ import annotations

import base64
import io
import time
import wave
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.voice.tts import TTSManager, EdgeTTS, PiperTTS
from app.voice.tts_gemini import (
    GeminiTTS,
    pcm_to_wav,
    PREBUILT_VOICES,
    SAMPLE_RATE,
)
from app.voice.quota_guard import QuotaGuard, QuotaExceededError
from app.voice.tts_cache import TTSAudioCache
from app.voice.pronunciation import apply_pronunciation_map, load_pronunciation_map
from app.web.server import app


@pytest.fixture
def test_client():
    return TestClient(app)


def make_dummy_wav(duration_s: float = 0.1, sample_rate: int = 24000) -> bytes:
    """Generate minimal valid PCM 16-bit mono WAV bytes."""
    buf = io.BytesIO()
    num_samples = int(duration_s * sample_rate)
    pcm = b"\x00\x00" * num_samples
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. PCM to WAV and Unary Decoding Tests
# ---------------------------------------------------------------------------

class TestGeminiTTSCore:
    def test_pcm_to_wav_format(self):
        pcm = b"\x00\x00" * 480  # 480 samples = 20ms at 24kHz
        wav_bytes = pcm_to_wav(pcm, sample_rate=24000)
        assert wav_bytes.startswith(b"RIFF")
        assert b"WAVE" in wav_bytes[:16]

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 24000
            assert wf.getnframes() == 480

    def test_unary_decode_mock(self, tmp_path):
        dummy_wav = make_dummy_wav()
        b64_wav = base64.b64encode(dummy_wav).decode("utf-8")

        mock_interaction = MagicMock()
        mock_output = MagicMock()
        mock_output.type = "audio"
        mock_output.data = b64_wav
        mock_interaction.output = [mock_output]

        mock_client = MagicMock()
        mock_client.interactions.create.return_value = mock_interaction

        cache = TTSAudioCache(cache_dir=str(tmp_path / "cache"))
        guard = QuotaGuard(cap=50, state_file=str(tmp_path / "quota.json"))
        tts = GeminiTTS(client=mock_client, quota_guard=guard, audio_cache=cache)

        audio = tts.synthesize_unary("Hello boss, testing unary", voice="achird")
        assert audio == dummy_wav
        assert mock_client.interactions.create.called

    def test_streaming_chunk_assembly_mock(self, tmp_path):
        # 3 chunks of PCM data (24kHz, 16-bit mono = 2 bytes per sample)
        chunk1 = b"\x10\x00" * 240
        chunk2 = b"\x20\x00" * 240
        chunk3 = b"\x30\x00" * 240

        def create_mock_event(data_bytes):
            event = MagicMock()
            step = MagicMock()
            step.delta = MagicMock()
            step.delta.type = "audio"
            step.delta.data = base64.b64encode(data_bytes).decode("utf-8")
            event.step = step
            return event

        events = [create_mock_event(chunk1), create_mock_event(chunk2), create_mock_event(chunk3)]

        mock_client = MagicMock()
        mock_client.interactions.create.return_value = iter(events)

        cache = TTSAudioCache(cache_dir=str(tmp_path / "cache"))
        guard = QuotaGuard(cap=50, state_file=str(tmp_path / "quota.json"))
        tts = GeminiTTS(client=mock_client, quota_guard=guard, audio_cache=cache)

        stream_chunks = list(tts.synthesize_stream("Hello streaming"))
        assert len(stream_chunks) == 3
        assert stream_chunks[0] == chunk1
        assert stream_chunks[1] == chunk2
        assert stream_chunks[2] == chunk3

        # Test synthesize() which wraps streamed PCM into WAV
        mock_client.interactions.create.return_value = iter(events)
        full_wav = tts.synthesize("Hello streaming", voice="achird")
        assert full_wav.startswith(b"RIFF")
        with wave.open(io.BytesIO(full_wav), "rb") as wf:
            assert wf.getnframes() == 720  # 240 * 3


# ---------------------------------------------------------------------------
# 2. Pronunciation Map Tests
# ---------------------------------------------------------------------------

class TestPronunciationMap:
    def test_apply_pronunciation_map(self):
        text = "Yes boss, sollunga! vaanga paathukalam"
        mapping = {
            "sollunga": "சொல்லுங்க",
            "vaanga": "வாங்க",
            "paathukalam": "பாத்துக்கலாம்",
        }
        converted = apply_pronunciation_map(text, mapping)
        assert "சொல்லுங்க" in converted
        assert "வாங்க" in converted
        assert "பாத்துக்கலாம்" in converted
        assert "sollunga" not in converted

    def test_word_boundary_isolation(self):
        # Should not replace substring inside another word
        mapping = {"va": "வா"}
        text = "canvas evaluation va"
        converted = apply_pronunciation_map(text, mapping)
        assert converted.startswith("canvas evaluation வா")

    def test_pronunciation_applied_before_gemini_call(self, tmp_path):
        dummy_wav = make_dummy_wav()
        b64_wav = base64.b64encode(dummy_wav).decode("utf-8")

        mock_interaction = MagicMock()
        mock_output = MagicMock()
        mock_output.type = "audio"
        mock_output.data = b64_wav
        mock_interaction.output = [mock_output]

        mock_client = MagicMock()
        mock_client.interactions.create.return_value = mock_interaction

        cache = TTSAudioCache(cache_dir=str(tmp_path / "cache"))
        guard = QuotaGuard(cap=50, state_file=str(tmp_path / "quota.json"))
        tts = GeminiTTS(client=mock_client, quota_guard=guard, audio_cache=cache)

        tts.synthesize_unary("Yes boss, sollunga!", voice="achird")
        call_args = mock_client.interactions.create.call_args[1]
        input_text = call_args["input"][0]["content"][0]["text"]
        # Check that sollunga was replaced with Tamil script
        assert "சொல்லுங்க" in input_text


# ---------------------------------------------------------------------------
# 3. Audio Cache Tests
# ---------------------------------------------------------------------------

class TestTTSCache:
    def test_cache_hit_avoids_api_call(self, tmp_path):
        dummy_wav = make_dummy_wav()
        b64_wav = base64.b64encode(dummy_wav).decode("utf-8")

        mock_interaction = MagicMock()
        mock_output = MagicMock()
        mock_output.type = "audio"
        mock_output.data = b64_wav
        mock_interaction.output = [mock_output]

        mock_client = MagicMock()
        mock_client.interactions.create.return_value = mock_interaction

        cache = TTSAudioCache(cache_dir=str(tmp_path / "cache"))
        guard = QuotaGuard(cap=50, state_file=str(tmp_path / "quota.json"))
        tts = GeminiTTS(client=mock_client, quota_guard=guard, audio_cache=cache)

        # 1st call -> Cache miss -> calls API
        audio1 = tts.synthesize_unary("Prewarm canonical phrase", voice="achird")
        assert mock_client.interactions.create.call_count == 1
        assert audio1 == dummy_wav

        # 2nd call -> Cache hit -> no API call
        audio2 = tts.synthesize_unary("Prewarm canonical phrase", voice="achird")
        assert mock_client.interactions.create.call_count == 1  # Unchanged!
        assert audio2 == dummy_wav

        stats = cache.get_stats()
        assert stats["hits"] >= 1


# ---------------------------------------------------------------------------
# 4. Quota Guard & Cap Tests
# ---------------------------------------------------------------------------

class TestQuotaGuard:
    def test_quota_cap_enforcement(self, tmp_path):
        state_file = str(tmp_path / "quota.json")
        guard = QuotaGuard(cap=1, state_file=state_file)

        assert guard.record_request() is True
        assert guard.get_status()["count"] == 1

        # Second request must raise QuotaExceededError
        with pytest.raises(QuotaExceededError):
            guard.record_request()

    def test_quota_reset(self, tmp_path):
        state_file = str(tmp_path / "quota.json")
        guard = QuotaGuard(cap=2, state_file=state_file)
        guard.record_request()
        assert guard.get_status()["count"] == 1

        guard.reset()
        assert guard.get_status()["count"] == 0

    def test_gemini_tts_raises_on_quota_exceeded(self, tmp_path):
        mock_client = MagicMock()
        cache = TTSAudioCache(cache_dir=str(tmp_path / "cache"))
        guard = QuotaGuard(cap=1, state_file=str(tmp_path / "quota.json"))
        guard.record_request()  # Count is now 1 (cap reached)

        tts = GeminiTTS(client=mock_client, quota_guard=guard, audio_cache=cache)
        with pytest.raises(QuotaExceededError):
            tts.synthesize_unary("Another turn", voice="achird")


# ---------------------------------------------------------------------------
# 5. Engine Fallback Chain Tests (Gemini -> EdgeTTS -> Piper)
# ---------------------------------------------------------------------------

class TestTTSManagerFallback:
    def test_fallback_on_gemini_429(self, tmp_path):
        mock_gemini = MagicMock(spec=GeminiTTS)
        mock_gemini.synthesize.side_effect = QuotaExceededError("Daily limit reached")
        mock_gemini.is_available.return_value = True

        mock_edge = MagicMock(spec=EdgeTTS)
        mock_edge.synthesize.return_value = b"EDGE_AUDIO"
        mock_edge.is_available.return_value = True

        mock_piper = MagicMock(spec=PiperTTS)
        mock_piper.is_available.return_value = True

        manager = TTSManager(edge_tts=mock_edge, piper_tts=mock_piper, gemini_tts=mock_gemini)

        fallback_events = []
        manager.add_fallback_listener(lambda p, f, r: fallback_events.append((p, f, r)))

        result = manager.synthesize("Hello fallback", voice="achird")
        assert result == b"EDGE_AUDIO"
        assert len(fallback_events) == 1
        assert fallback_events[0][0] == "gemini"
        assert fallback_events[0][1] == "edge-tts"
        assert "quota" in fallback_events[0][2].lower() or "limit" in fallback_events[0][2].lower()

    def test_fallback_on_gemini_timeout(self):
        mock_gemini = MagicMock(spec=GeminiTTS)
        mock_gemini.synthesize.side_effect = TimeoutError("Gemini TTS first-audio timeout > 4.0s")
        mock_gemini.is_available.return_value = True

        mock_edge = MagicMock(spec=EdgeTTS)
        mock_edge.synthesize.return_value = b"EDGE_AUDIO"
        mock_edge.is_available.return_value = True

        mock_piper = MagicMock(spec=PiperTTS)

        manager = TTSManager(edge_tts=mock_edge, piper_tts=mock_piper, gemini_tts=mock_gemini)
        fallback_events = []
        manager.add_fallback_listener(lambda p, f, r: fallback_events.append((p, f, r)))

        result = manager.synthesize("Hello timeout", voice="achird")
        assert result == b"EDGE_AUDIO"
        assert len(fallback_events) == 1
        assert "timeout" in fallback_events[0][2].lower()

    def test_three_tier_fallback_gemini_to_piper(self):
        mock_gemini = MagicMock(spec=GeminiTTS)
        mock_gemini.synthesize.side_effect = RuntimeError("Gemini network error")
        mock_gemini.is_available.return_value = True

        mock_edge = MagicMock(spec=EdgeTTS)
        mock_edge.synthesize.side_effect = RuntimeError("EdgeTTS websocket offline")
        mock_edge.is_available.return_value = True

        mock_piper = MagicMock(spec=PiperTTS)
        mock_piper.synthesize.return_value = b"PIPER_OFFLINE_AUDIO"
        mock_piper.is_available.return_value = True

        manager = TTSManager(edge_tts=mock_edge, piper_tts=mock_piper, gemini_tts=mock_gemini)
        fallback_events = []
        manager.add_fallback_listener(lambda p, f, r: fallback_events.append((p, f, r)))

        result = manager.synthesize("Hello all offline")
        assert result == b"PIPER_OFFLINE_AUDIO"
        # Should record gemini -> edge-tts and edge-tts -> piper
        assert len(fallback_events) == 2
        assert fallback_events[0][0] == "gemini"
        assert fallback_events[1][0] == "edge-tts"
        assert fallback_events[1][1] == "piper"


# ---------------------------------------------------------------------------
# 6. REST API Endpoints Tests
# ---------------------------------------------------------------------------

class TestVoiceStudioEndpoints:
    def test_voice_status_endpoint(self, test_client):
        res = test_client.get("/api/voice/status")
        assert res.status_code == 200
        data = res.json()
        assert "status" in data
        assert "quota" in data
        assert "prebuilt_voices" in data
        assert len(data["prebuilt_voices"]) == 30
        assert data["prebuilt_voices"][0]["id"] == "achird"

    def test_quota_cap_endpoint(self, test_client):
        res = test_client.post("/api/voice/quota/cap", json={"cap": 75})
        assert res.status_code == 200
        data = res.json()
        assert data["cap"] == 75

        # Verify status endpoint reflects updated cap
        status_res = test_client.get("/api/voice/status")
        assert status_res.json()["quota"]["cap"] == 75

    def test_pronunciation_endpoints(self, test_client):
        # GET map
        get_res = test_client.get("/api/voice/pronunciation")
        assert get_res.status_code == 200
        cur_map = get_res.json().get("map", {})
        assert "sollunga" in cur_map

        # POST updated map
        cur_map["vanakkam"] = "வணக்கம்"
        post_res = test_client.post("/api/voice/pronunciation", json={"map": cur_map})
        assert post_res.status_code == 200

        # Verify persisted
        verify_res = test_client.get("/api/voice/pronunciation")
        assert verify_res.json()["map"]["vanakkam"] == "வணக்கம்"

    def test_extended_library_endpoint(self, test_client):
        res = test_client.get("/api/voice/extended-library?query=achird&gender=male")
        assert res.status_code == 200
        data = res.json()
        assert "voices" in data
        # At minimum, prebuilts serve as fallback
        assert len(data["voices"]) > 0

    def test_gemini_voice_preview_endpoint(self, test_client):
        dummy_wav = make_dummy_wav()
        with patch("app.voice.tts_gemini.GeminiTTS.synthesize", return_value=dummy_wav):
            res = test_client.post(
                "/api/voice/preview",
                json={
                    "text": "Yes boss, sollunga!",
                    "engine": "gemini",
                    "voice": "achird",
                    "model": "gemini-3.8-flash-lite-tts",
                },
            )
            assert res.status_code == 200
            assert res.headers["content-type"] in ["audio/wav", "audio/x-wav"]
            assert len(res.content) > 0
