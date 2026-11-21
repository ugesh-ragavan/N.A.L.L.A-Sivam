"""tests/test_voice_studio.py

Comprehensive tests for Nalla Sivam Voice Studio:
  1. Dynamic voice discovery (EdgeTTS and Piper scanning, language groupings).
  2. Voice profiles persistence in data/settings.json (up to 5 named profiles).
  3. EdgeTTS rate, pitch, and volume formatting.
  4. Bilingual sentence-by-sentence routing (Tamil voice vs English voice).
  5. Voice preview endpoint (POST /api/voice/preview).
  6. Audio feel and STT settings endpoints.
  7. Offline fallback from EdgeTTS to Piper with notice broadcasting.
"""

from __future__ import annotations

import io
import json
import pytest
from fastapi.testclient import TestClient

from app.config import (
    SETTINGS_FILE,
    get_voice_profiles,
    save_voice_profiles,
    get_audio_feel,
    save_audio_feel,
    get_stt_settings,
    save_stt_settings,
    get_active_voice_profile,
)
from app.voice.tts import (
    EdgeTTS,
    PiperTTS,
    TTSManager,
    discover_voices,
    get_available_voices_sync,
    format_edge_rate,
    format_edge_pitch,
    format_edge_volume,
)
from app.voice.pipeline import VoicePipeline, VoiceState
from app.web.server import app


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. EdgeTTS Parameter Formatting Tests
# ---------------------------------------------------------------------------

class TestEdgeTTSFormatting:
    def test_format_edge_rate(self):
        assert format_edge_rate(1.0) == "+0%"
        assert format_edge_rate(1.15) == "+15%"
        assert format_edge_rate(0.85) == "-15%"
        assert format_edge_rate("+10%") == "+10%"
        assert format_edge_rate("-10%") == "-10%"
        assert format_edge_rate(None) == "+0%"

    def test_format_edge_pitch(self):
        assert format_edge_pitch(0) == "+0Hz"
        assert format_edge_pitch(5) == "+5Hz"
        assert format_edge_pitch(-3) == "-3Hz"
        assert format_edge_pitch("+4Hz") == "+4Hz"
        assert format_edge_pitch("-5Hz") == "-5Hz"
        assert format_edge_pitch(None) == "+0Hz"

    def test_format_edge_volume(self):
        assert format_edge_volume(100) == "+0%"
        assert format_edge_volume(80) == "-20%"
        assert format_edge_volume(120) == "+20%"
        assert format_edge_volume("+0%") == "+0%"
        assert format_edge_volume("-15%") == "-15%"
        assert format_edge_volume(None) == "+0%"


# ---------------------------------------------------------------------------
# 2. Dynamic Voice Discovery Tests
# ---------------------------------------------------------------------------

class TestVoiceDiscovery:
    def test_discover_voices_groups(self):
        voices = get_available_voices_sync()
        assert isinstance(voices, dict)
        assert "English (India)" in voices
        assert "English (US/UK)" in voices
        assert "Tamil" in voices
        assert "Piper (Offline)" in voices

        # Verify voice item structure
        in_voices = voices["English (India)"]
        assert len(in_voices) > 0
        first = in_voices[0]
        assert "id" in first
        assert "name" in first
        assert "engine" in first

        # Tamil voice discovery
        ta_voices = voices["Tamil"]
        assert len(ta_voices) > 0
        assert any("Pallavi" in v["id"] or "ta-IN" in v["locale"] for v in ta_voices)


# ---------------------------------------------------------------------------
# 3. Bilingual Sentence-by-Sentence Routing Tests
# ---------------------------------------------------------------------------

class TestBilingualSentenceRouting:
    def test_auto_language_switching(self):
        # Dummy TTS provider that records voice used
        class MockVoiceEdge(EdgeTTS):
            def __init__(self):
                super().__init__(voice_en="en-IN-NeerjaNeural", voice_ta="ta-IN-PallaviNeural")
                self.calls = []

            def synthesize(self, text, voice=None, language=None, rate=None, pitch=None, volume=None):
                v = self._select_voice(text, requested_voice=voice, language=language)
                self.calls.append((text, v))
                return b"RIFFmockaudio"

        mock_edge = MockVoiceEdge()
        manager = TTSManager(edge_provider=mock_edge)

        # English sentence
        manager.synthesize("Yes boss, checking your weather now.")
        assert mock_edge.calls[-1][1] == "en-IN-NeerjaNeural"

        # Tamil sentence
        manager.synthesize("இன்னைக்கு மழை பெய்ய வாய்ப்பு இருக்கு.")
        assert mock_edge.calls[-1][1] == "ta-IN-PallaviNeural"

        # Another English sentence within the same turn
        manager.synthesize("Keep an umbrella handy.")
        assert mock_edge.calls[-1][1] == "en-IN-NeerjaNeural"


# ---------------------------------------------------------------------------
# 4. Fallback from EdgeTTS to Piper Tests
# ---------------------------------------------------------------------------

class TestFallbackToPiper:
    def test_edge_failure_falls_back_to_piper(self):
        notices = []

        class FailingEdge(EdgeTTS):
            def synthesize(self, *args, **kwargs):
                raise RuntimeError("DNS lookup failed: edge-tts.microsoft.com offline")

        class WorkingPiper(PiperTTS):
            def synthesize(self, text, *args, **kwargs):
                return b"PIPER_OFFLINE_AUDIO"

        manager = TTSManager(edge_provider=FailingEdge(), piper_provider=WorkingPiper())
        manager.add_fallback_listener(lambda reason: notices.append(reason))

        audio = manager.synthesize("Hello boss, testing offline fallback.")
        assert audio == b"PIPER_OFFLINE_AUDIO"
        assert len(notices) == 1
        assert "EdgeTTS connection failed" in notices[0]


# ---------------------------------------------------------------------------
# 5. Voice Profiles & Settings Persistence Tests
# ---------------------------------------------------------------------------

class TestVoiceProfiles:
    def test_profiles_crud_and_persistence(self):
        initial = get_voice_profiles()
        assert "profiles" in initial
        assert "active" in initial

        # Save up to 5 profiles
        test_profiles = {
            "active": "Test Custom",
            "profiles": {
                "P1": {"engine": "edge-tts", "voice": "en-IN-NeerjaNeural", "voice_ta": "ta-IN-PallaviNeural", "rate": "+0%", "pitch": "+0Hz", "volume": "+0%"},
                "P2": {"engine": "edge-tts", "voice": "en-IN-PrabhatNeural", "voice_ta": "ta-IN-ValluvarNeural", "rate": "+10%", "pitch": "+5Hz", "volume": "+0%"},
                "P3": {"engine": "edge-tts", "voice": "en-IN-NeerjaNeural", "voice_ta": "ta-IN-PallaviNeural", "rate": "-5%", "pitch": "-3Hz", "volume": "-10%"},
                "P4": {"engine": "piper", "voice": "en_US-lessac-medium", "voice_ta": "ta-IN-PallaviNeural", "rate": "+0%", "pitch": "+0Hz", "volume": "+0%"},
                "Test Custom": {"engine": "edge-tts", "voice": "en-IN-NeerjaNeural", "voice_ta": "ta-IN-PallaviNeural", "rate": "+15%", "pitch": "+2Hz", "volume": "+0%"},
                "P6_Overflow": {"engine": "edge-tts", "voice": "en-IN-NeerjaNeural", "voice_ta": "ta-IN-PallaviNeural", "rate": "+0%", "pitch": "+0Hz", "volume": "+0%"},
            },
        }

        save_voice_profiles(test_profiles)
        active = get_active_voice_profile()
        assert active["rate"] == "+15%"

        # Enforce max 5 profiles limit
        saved = get_voice_profiles()
        assert len(saved["profiles"]) == 5
        assert "P6_Overflow" not in saved["profiles"]


# ---------------------------------------------------------------------------
# 6. REST API Endpoints Tests
# ---------------------------------------------------------------------------

class TestVoiceStudioAPI:
    def test_get_voices_api(self, client):
        res = client.get("/api/voice/voices")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "voices" in data
        assert "English (India)" in data["voices"]

    def test_get_and_post_profiles_api(self, client):
        # GET
        res = client.get("/api/voice/profiles")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "active" in data
        assert "profiles" in data
        assert "audio_feel" in data
        assert "stt" in data

        # POST profile update
        new_prof_data = {
            "active": "Calm Night",
            "profiles": data["profiles"],
        }
        post_res = client.post("/api/voice/profiles", json=new_prof_data)
        assert post_res.status_code == 200
        assert post_res.json()["active"] == "Calm Night"

    def test_audio_feel_api(self, client):
        res = client.post("/api/voice/audio-feel", json={
            "speed_preset": "Quick",
            "listening_chime": True,
            "done_chime": False,
            "chime_volume": 65,
        })
        assert res.status_code == 200
        feel = res.json()["audio_feel"]
        assert feel["speed_preset"] == "Quick"
        assert feel["done_chime"] is False
        assert feel["chime_volume"] == 65

    def test_stt_settings_api(self, client):
        res = client.post("/api/voice/stt-settings", json={
            "whisper_model": "small",
            "language": "auto",
            "input_device": None,
            "sensitivity": 350.0,
        })
        assert res.status_code == 200
        stt = res.json()["stt"]
        assert stt["whisper_model"] == "small"
        assert stt["sensitivity"] == 350.0

    def test_preview_voice_api(self, client):
        res = client.post("/api/voice/preview", json={
            "text": "Yes boss, sollunga!",
            "engine": "piper",  # Use offline piper for instant non-network test
            "voice": "en_US-lessac-medium",
            "rate": 1.0,
            "pitch": 0,
            "volume": 100,
        })
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("audio/")
        assert len(res.content) > 0
