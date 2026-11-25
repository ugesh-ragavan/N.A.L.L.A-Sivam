"""tests/test_two_voice_slots.py

Comprehensive tests for Prompt C:
1. Two voice slots configuration (English & Tamil slots, normalization, persistence).
2. Dynamic voice discovery with locale priority (ta-IN first, dynamic Piper).
3. Bilingual sentence splitting and language routing.
4. Hard Rule 5: Never read Tamil with an English voice; text-only notice on failure.
5. Telemetry: Per-sentence engine recording and retrieval.
6. API Endpoints: Preview for English and Tamil slots, engine telemetry endpoint.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch
import pytest

from app.config import (
    DEFAULT_VOICE_PROFILES,
    normalize_voice_profile,
    get_voice_profiles,
    get_active_voice_profile,
)
from app.voice.tts import (
    TTSManager,
    discover_voices,
    split_bilingual_sentences,
    _tamil_locale_priority,
)
from app.voice.pipeline import default_pipeline


# -----------------------------------------------------------------------------
# 1. Configuration & Profile Normalization Tests
# -----------------------------------------------------------------------------

def test_default_voice_profiles_have_two_slots():
    assert "profiles" in DEFAULT_VOICE_PROFILES
    assert "Nalla Default" in DEFAULT_VOICE_PROFILES["profiles"]
    default_prof = DEFAULT_VOICE_PROFILES["profiles"]["Nalla Default"]
    assert "voice_slot_en" in default_prof
    assert "voice_slot_ta" in default_prof
    assert default_prof.get("auto_switch_language") is True

    slot_en = default_prof["voice_slot_en"]
    slot_ta = default_prof["voice_slot_ta"]
    assert slot_en["engine"] in ("gemini", "edge-tts", "piper")
    assert slot_ta["engine"] in ("edge-tts", "gemini")
    assert "voice" in slot_en
    assert "voice" in slot_ta
    assert "rate" in slot_en
    assert "volume" in slot_en


def test_normalize_legacy_voice_profile():
    # Legacy profile with flat voice and engine
    legacy = {
        "engine": "piper",
        "voice": "en_US-lessac-medium",
        "voice_ta": "ta-IN-PallaviNeural",
        "rate": "+10%",
        "volume": "+0%",
    }
    normalized = normalize_voice_profile(legacy)
    assert "voice_slot_en" in normalized
    assert "voice_slot_ta" in normalized
    assert normalized["voice_slot_en"]["engine"] == "piper"
    assert normalized["voice_slot_en"]["voice"] == "en_US-lessac-medium"
    assert normalized["voice_slot_ta"]["engine"] == "edge-tts"
    assert normalized["voice_slot_ta"]["voice"] == "ta-IN-PallaviNeural"
    assert normalized["auto_switch_language"] is True


# -----------------------------------------------------------------------------
# 2. Dynamic Voice Discovery & Priority Sorting Tests
# -----------------------------------------------------------------------------

def test_tamil_locale_priority_sorting():
    voices = [
        {"locale": "ta-MY", "name": "Kani"},
        {"locale": "ta-IN", "name": "Valluvar"},
        {"locale": "ta-LK", "name": "Saranya"},
        {"locale": "ta-SG", "name": "Venba"},
        {"locale": "ta-IN", "name": "Pallavi"},
    ]
    sorted_voices = sorted(voices, key=_tamil_locale_priority)
    locales = [v["locale"] for v in sorted_voices]
    # ta-IN must come first, followed by ta-LK, ta-SG, ta-MY
    assert locales[0] == "ta-IN"
    assert locales[1] == "ta-IN"
    assert locales[2] == "ta-LK"
    assert locales[3] == "ta-SG"
    assert locales[4] == "ta-MY"


@pytest.mark.asyncio
async def test_discover_voices_structure():
    catalog = await discover_voices(force_refresh=True)
    assert "gemini" in catalog
    assert "edge" in catalog
    assert "piper" in catalog
    assert "tamil_voices" in catalog
    assert "english_voices" in catalog

    # Verify ta-IN priority in tamil_voices
    ta_locales = [v.get("locale") for v in catalog["tamil_voices"] if v.get("locale")]
    if len(ta_locales) >= 2:
        assert ta_locales[0] == "ta-IN"


# -----------------------------------------------------------------------------
# 3. Bilingual Sentence Segmentation Tests
# -----------------------------------------------------------------------------

def test_split_bilingual_sentences():
    # Pure English
    chunks = split_bilingual_sentences("Hello boss, how are you doing today?")
    assert len(chunks) == 1
    assert chunks[0][1] == "en"

    # Pure Tamil
    chunks = split_bilingual_sentences("வணக்கம், நான் நல்ல சிவம். உங்களுக்கு எப்படி உதவ வேண்டும்?")
    assert len(chunks) >= 1
    assert all(c[1] == "ta" for c in chunks)

    # Mixed bilingual
    mixed_text = "Good morning boss. இன்று வானிலை மிகவும் இனிமையாக இருக்கிறது. Have a great day!"
    chunks = split_bilingual_sentences(mixed_text)
    assert len(chunks) == 3
    assert chunks[0][1] == "en"
    assert chunks[1][1] == "ta"
    assert chunks[2][1] == "en"


# -----------------------------------------------------------------------------
# 4. Two-Slot Language Routing & Telemetry Tests
# -----------------------------------------------------------------------------

def test_two_slot_language_routing_and_telemetry(monkeypatch):
    manager = TTSManager(primary_engine="gemini")
    manager.active_slot_en = {
        "engine": "gemini",
        "voice": "achird",
        "rate": 1.0,
        "volume": 100,
    }
    manager.active_slot_ta = {
        "engine": "edge-tts",
        "voice": "ta-IN-PallaviNeural",
        "rate": 1.0,
        "volume": 100,
    }

    # Mock synthesize single sentence returning (audio_bytes, info_dict)
    def fake_synth(sentence, lang, slot_cfg, style=None):
        info = {
            "text": sentence,
            "lang": lang,
            "engine": slot_cfg.get("engine", "gemini"),
            "voice": slot_cfg.get("voice", ""),
        }
        return b"DUMMY_AUDIO", info

    mock_synth = MagicMock(side_effect=fake_synth)
    monkeypatch.setattr(manager, "_synthesize_sentence", mock_synth)

    manager.clear_turn_engines()
    mixed = "Good morning everyone. இன்று வானிலை இனிமை."
    audio = manager.synthesize(mixed)

    assert audio != b""
    assert mock_synth.call_count == 2

    # Check calls passed correct engine and slot language
    first_call = mock_synth.call_args_list[0]
    second_call = mock_synth.call_args_list[1]
    assert first_call.args[1] == "en"
    assert second_call.args[1] == "ta"

    # Verify telemetry tracking
    telemetry = manager.get_last_turn_engines()
    assert len(telemetry) == 2
    assert telemetry[0]["lang"] == "en"
    assert telemetry[0]["engine"] == "gemini"
    assert telemetry[1]["lang"] == "ta"
    assert telemetry[1]["engine"] == "edge-tts"


# -----------------------------------------------------------------------------
# 5. HARD RULE 5: Never Read Tamil With English Voice
# -----------------------------------------------------------------------------

def test_hard_rule_5_tamil_never_uses_english_voice(monkeypatch):
    """If Tamil preferred engine fails, fallback only to another Tamil-capable engine or notice."""
    manager = TTSManager(primary_engine="gemini")
    manager.active_slot_ta = {
        "engine": "edge-tts",
        "voice": "ta-IN-PallaviNeural",
        "rate": 1.0,
        "volume": 100,
    }

    # Make edge-tts raise an error
    monkeypatch.setattr(
        manager.edge,
        "synthesize",
        MagicMock(side_effect=RuntimeError("EdgeTTS network down"))
    )
    # Make gemini return audio for Tamil
    mock_gemini = MagicMock(return_value=b"GEMINI_TAMIL_AUDIO")
    monkeypatch.setattr(manager.gemini, "synthesize", mock_gemini)

    audio = manager.synthesize("வணக்கம் பாஸ்", language="ta")
    assert audio == b"GEMINI_TAMIL_AUDIO"
    assert mock_gemini.called

    # If BOTH fail, it must return empty bytes or text-only notice, NEVER call Piper or English voices
    mock_gemini.side_effect = RuntimeError("Gemini quota exceeded")
    mock_piper = MagicMock(return_value=b"PIPER_ENGLISH_AUDIO")
    monkeypatch.setattr(manager.piper, "synthesize", mock_piper)

    audio_fail = manager.synthesize("வணக்கம் பாஸ்", language="ta")
    assert audio_fail == b""
    assert not mock_piper.called, "Hard Rule 5 violated: English/Piper voice called for Tamil text!"


# -----------------------------------------------------------------------------
# 6. Web API Endpoints Tests
# -----------------------------------------------------------------------------

def test_preview_endpoint_with_slots(monkeypatch):
    from fastapi.testclient import TestClient
    from app.web.server import app

    client = TestClient(app)

    # Mock synthesize_preview returning (audio_bytes, mime_type)
    mock_preview = MagicMock(return_value=(b"PREVIEW_WAV", "audio/wav"))
    monkeypatch.setattr(default_pipeline.tts, "preview", mock_preview)

    # 1. Preview English slot
    resp_en = client.post("/api/voice/preview", json={
        "slot": "en",
        "engine": "gemini",
        "voice": "achird",
        "text": "Yes boss, sollunga!"
    })
    assert resp_en.status_code == 200
    assert resp_en.content == b"PREVIEW_WAV"

    # 2. Preview Tamil slot
    resp_ta = client.post("/api/voice/preview", json={
        "slot": "ta",
        "engine": "edge-tts",
        "voice": "ta-IN-PallaviNeural",
        "text": "வணக்கம், நான் நல்ல சிவம்."
    })
    assert resp_ta.status_code == 200
    assert resp_ta.content == b"PREVIEW_WAV"

    # 3. Check /api/voice/last-engines
    resp_telemetry = client.get("/api/voice/last-engines")
    assert resp_telemetry.status_code == 200
    data = resp_telemetry.json()
    assert "engines" in data
    assert isinstance(data["engines"], list)
