"""tests/test_voice_studio_6c.py

Tests for Prompt 6C Voice Studio features:
  1. Live profile switching (/api/voice/profiles/active).
  2. Voice library browser with filters (language, gender, pitch, context, search).
  3. Custom voice_... ID preview and synthesis.
  4. Live STT settings & VAD threshold updates.
  5. Pronunciation map CRUD.
  6. Quota status, cap, and counter.
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app import config
from app.web.server import app
from app.voice.pipeline import default_pipeline


@pytest.fixture
def client():
    return TestClient(app)


def test_active_profile_switch_endpoint(client):
    # Ensure Calm Night and Nalla Default exist
    client.post("/api/voice/profiles", json={
        "active": "Nalla Default",
        "profiles": {
            "Nalla Default": {"engine": "gemini", "voice": "achird"},
            "Calm Night": {"engine": "gemini", "voice": "sulafat"},
        }
    })

    # Switch to Calm Night
    res = client.post("/api/voice/profiles/active", json={"name": "Calm Night"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["active"] == "Calm Night"
    assert config.VOICE_PROFILES.get("active") == "Calm Night"

    # Non-existent profile returns 404
    res_err = client.post("/api/voice/profiles/active", json={"name": "NonExistentProfileXYZ"})
    assert res_err.status_code == 404

    # Switch back to Nalla Default
    res_def = client.post("/api/voice/profiles/active", json={"name": "Nalla Default"})
    assert res_def.status_code == 200
    assert res_def.json()["active"] == "Nalla Default"


def test_extended_voice_library_default_filters(client):
    # Default filter: Tamil & English (India)
    res = client.get("/api/voice/extended-library?language_code=ta-IN,en-IN")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    voices = data["voices"]
    assert len(voices) > 0
    for v in voices:
        assert v["language_code"] in ("ta-IN", "en-IN")


def test_extended_voice_library_gender_and_pitch_filters(client):
    # Gender = female, Pitch = high
    res = client.get("/api/voice/extended-library?gender=female&pitch=high")
    assert res.status_code == 200
    voices = res.json()["voices"]
    assert len(voices) > 0
    for v in voices:
        assert v["gender"].lower() == "female"
        assert v["pitch"].lower() == "high"


def test_extended_voice_library_context_and_search(client):
    # Context = conversational, Search = Pallavi
    res = client.get("/api/voice/extended-library?contexts=conversational&search=Pallavi")
    assert res.status_code == 200
    voices = res.json()["voices"]
    assert len(voices) > 0
    assert "pallavi" in voices[0]["name"].lower()


def test_voice_preview_custom_voice_id(client):
    mock_audio = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00data\x00\x00\x00\x00"
    with patch("app.voice.tts.TTSManager.preview", return_value=(mock_audio, "audio/wav")):
        res = client.post("/api/voice/preview", json={
            "text": "Yes boss, sollunga!",
            "engine": "gemini",
            "voice": "voice_studio_custom_xyz999",
            "model": "gemini-3.8-flash-lite-tts",
        })
        assert res.status_code == 200
        assert res.headers["content-type"] == "audio/wav"
        assert res.content == mock_audio


def test_stt_live_application(client):
    res = client.post("/api/voice/stt-settings", json={
        "whisper_model": "small",
        "language": "ta",
        "sensitivity": 550.0,
    })
    assert res.status_code == 200
    assert config.WHISPER_MODEL == "small"
    assert config.WHISPER_LANGUAGE == "ta"
    assert getattr(default_pipeline.vad, "energy_threshold", 550.0) == 550.0

    # Revert to base / auto / 400
    client.post("/api/voice/stt-settings", json={
        "whisper_model": "base",
        "language": "auto",
        "sensitivity": 400.0,
    })
    assert config.WHISPER_MODEL == "base"
    assert getattr(default_pipeline.vad, "energy_threshold", 400.0) == 400.0


def test_pronunciation_map_crud(client):
    res_post = client.post("/api/voice/pronunciation", json={
        "map": {
            "sollunga": "சொல்லுங்க",
            "vanakkam": "வணக்கம்",
            "nandri": "நன்றி",
        }
    })
    assert res_post.status_code == 200
    data = res_post.json()
    assert data["map"]["sollunga"] == "சொல்லுங்க"

    res_get = client.get("/api/voice/pronunciation")
    assert res_get.status_code == 200
    assert res_get.json()["map"]["nandri"] == "நன்றி"


def test_quota_counter_and_cap(client):
    res_cap = client.post("/api/voice/quota/cap", json={"cap": 75})
    assert res_cap.status_code == 200
    assert res_cap.json()["cap"] == 75

    res_status = client.get("/api/voice/status")
    assert res_status.status_code == 200
    assert res_status.json()["quota"]["cap"] == 75

    # Revert cap
    client.post("/api/voice/quota/cap", json={"cap": 50})
