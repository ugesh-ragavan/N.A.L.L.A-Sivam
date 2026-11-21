"""tests/test_personality.py

Tests for Step 2: Personality Sliders & Modes.
- Persona system prompt includes the exact personality line:
  "Sarcasm: 7/10. Warmth: 8/10. Tamil mix: 40%. Replies: very short."
- Mode updates change the sliders and the generated prompt line.
- REST API GET & POST /api/personality work correctly.
- Settings persistence to data/settings.json works correctly.
"""

from __future__ import annotations

import json
from fastapi.testclient import TestClient
import pytest

from app import config
from app.brain.persona import Persona
from app.web.server import app

client = TestClient(app)


def test_personality_prompt_line_default():
    config.PERSONALITY_SARCASM = 7
    config.PERSONALITY_WARMTH = 8
    config.PERSONALITY_TAMIL_MIX = 40
    config.PERSONALITY_VERBOSITY = "very short"
    expected = "Sarcasm: 7/10. Warmth: 8/10. Tamil mix: 40%. Replies: very short."
    assert config.get_personality_prompt_line() == expected


def test_persona_build_system_prompt_includes_personality_line():
    config.PERSONALITY_SARCASM = 7
    config.PERSONALITY_WARMTH = 8
    config.PERSONALITY_TAMIL_MIX = 40
    config.PERSONALITY_VERBOSITY = "very short"
    persona = Persona()
    prompt = persona.build_system_prompt()
    assert "Sarcasm: 7/10. Warmth: 8/10. Tamil mix: 40%. Replies: very short." in prompt


def test_get_and_post_personality_api(tmp_path, monkeypatch):
    test_settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", test_settings_file)

    # GET initial personality
    res = client.get("/api/personality")
    assert res.status_code == 200
    data = res.json()
    assert "mode" in data
    assert "sarcasm" in data
    assert "prompt_line" in data
    assert "modes" in data
    assert "Jarvis" in data["modes"]

    # POST update personality to Jarvis preset
    update_payload = {
        "mode": "Jarvis",
        "sarcasm": 3,
        "warmth": 6,
        "tamil_mix": 10,
        "verbosity": "short",
    }
    post_res = client.post("/api/personality", json=update_payload)
    assert post_res.status_code == 200
    res_data = post_res.json()
    assert res_data["ok"] is True
    assert res_data["mode"] == "Jarvis"
    assert res_data["prompt_line"] == "Sarcasm: 3/10. Warmth: 6/10. Tamil mix: 10%. Replies: short."

    # Verify config updated
    assert config.PERSONALITY_MODE == "Jarvis"
    assert config.PERSONALITY_SARCASM == 3
    assert config.get_personality_prompt_line() == "Sarcasm: 3/10. Warmth: 6/10. Tamil mix: 10%. Replies: short."

    # Verify settings file persisted
    assert test_settings_file.exists()
    saved = json.loads(test_settings_file.read_text(encoding="utf-8"))
    assert saved["personality"]["mode"] == "Jarvis"

    # Reset back to Friend
    config.PERSONALITY_MODE = "Friend"
    config.PERSONALITY_SARCASM = 7
    config.PERSONALITY_WARMTH = 8
    config.PERSONALITY_TAMIL_MIX = 40
    config.PERSONALITY_VERBOSITY = "very short"
    config.save_persisted_settings()
