import pytest
from unittest.mock import MagicMock
from app.voice.tts import TTSManager, EdgeTTS, PiperTTS
from app import config


def test_diagnose_tracks_engine_and_model(monkeypatch):
    """Verify that every synthesized sentence logs engine, model, and voice."""
    manager = TTSManager(primary_engine="edge-tts")
    manager.active_slot_en = {
        "engine": "edge-tts",
        "voice": "en-IN-NeerjaNeural",
        "rate": 1.0,
        "volume": 100,
    }
    
    # Mock EdgeTTS synthesize
    mock_edge = MagicMock(return_value=b"MOCK_EDGE_AUDIO")
    monkeypatch.setattr(manager.edge, "synthesize", mock_edge)
    
    manager.clear_turn_engines()
    audio = manager.synthesize("Hello friend, how are you?")
    assert audio == b"MOCK_EDGE_AUDIO"
    
    telemetry = manager.get_last_turn_engines()
    assert len(telemetry) == 1
    assert telemetry[0]["engine"] == "edge-tts"
    assert telemetry[0]["model"] == "edge-neural"
    assert telemetry[0]["voice"] == "en-IN-NeerjaNeural"
    assert telemetry[0]["fallback"] is False


def test_force_engine_setting(monkeypatch):
    """Verify that FORCE_TTS_ENGINE forces the requested engine and prevents fallback."""
    monkeypatch.setattr(config, "FORCE_TTS_ENGINE", "piper")
    manager = TTSManager(primary_engine="gemini")
    manager.active_slot_en = {
        "engine": "gemini",
        "voice": "achird",
        "rate": 1.0,
        "volume": 100,
    }
    
    # Mock piper synthesize
    mock_piper = MagicMock(return_value=b"MOCK_PIPER_AUDIO")
    monkeypatch.setattr(manager.piper, "synthesize", mock_piper)
    
    manager.clear_turn_engines()
    audio = manager.synthesize("Testing forced piper engine.")
    assert audio == b"MOCK_PIPER_AUDIO"
    
    telemetry = manager.get_last_turn_engines()
    assert len(telemetry) == 1
    assert telemetry[0]["engine"] == "piper"
    assert telemetry[0]["model"] == "piper-onnx"


def test_silent_fallback_detected_and_reported(monkeypatch):
    """Verify that when a primary engine fails, fallback is recorded with source and notice."""
    notices = []
    manager = TTSManager()
    manager.add_fallback_listener(lambda reason, primary=None, fallback=None: notices.append((reason, primary, fallback)))
    
    manager.active_slot_en = {
        "engine": "gemini",
        "voice": "achird",
        "rate": 1.0,
        "volume": 100,
    }
    
    # Mock Gemini failing
    class FailingGemini:
        is_online = True
        model = "gemini-3.8-flash-lite-tts"
        def synthesize(self, *args, **kwargs):
            raise RuntimeError("Gemini quota 429")
            
    manager.gemini = FailingGemini()
    
    # Mock Edge succeeding
    mock_edge = MagicMock(return_value=b"FALLBACK_EDGE_AUDIO")
    monkeypatch.setattr(manager.edge, "synthesize", mock_edge)
    
    manager.clear_turn_engines()
    audio = manager.synthesize("Testing fallback detection.")
    assert audio == b"FALLBACK_EDGE_AUDIO"
    
    telemetry = manager.get_last_turn_engines()
    assert len(telemetry) == 1
    assert telemetry[0]["engine"] == "edge-tts"
    assert telemetry[0]["fallback"] is True
    assert telemetry[0]["fallback_from"] == "gemini"
    assert "Fallback from gemini" in telemetry[0]["notice"]
    assert len(notices) >= 1
