"""tests/test_personality_6d.py

Acceptance tests for Prompt 6D:
- Sliders: Sarcasm (0-10), Warmth (0-10), Tamil mix (0-100), Verbosity (very short / short / normal).
- Mode selector: Friend, Jarvis, Focus, Engineer.
- Presets: "Cool Friend", "Pure Jarvis", "Roast Mode", "Calm Night".
- Persona text editor with Save & Revert (for persona/nalla_master.md).
- Few-shot API with read-only view and editor for 8 canonical examples (GET, PUT, revert).
- Live prompt preview: exact final system prompt, personality block, and real-time mood override.
- Test chat box answering with unsaved slider settings without mutating saved config.
- Acceptance check: moving sliders changes reply, Roast Mode clearly sharper, Calm Night clearly softer.
- Model comparison endpoint (POST /api/personality/compare).
- Locked Identity Guard non-negotiable character enforcement.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import pytest

from app import config
from app.brain.persona import (
    Persona,
    detect_stress_mood,
    format_personality_sentences,
    get_few_shot_examples,
    save_few_shot_examples,
    revert_few_shot_examples,
)
from app.brain.llm_provider import LLMResponse
from app.web.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. Presets & Sliders
# ---------------------------------------------------------------------------

PRESETS_TEST_MATRIX = [
    ("Cool Friend", "Friend", 7, 8, 40, "very short"),
    ("Pure Jarvis", "Jarvis", 3, 6, 10, "short"),
    ("Roast Mode", "Friend", 10, 1, 60, "short"),
    ("Calm Night", "Focus", 0, 10, 10, "very short"),
]


@pytest.mark.parametrize("preset_name,expected_mode,sarcasm,warmth,tamil,verbosity", PRESETS_TEST_MATRIX)
def test_preset_combinations_formatting(preset_name, expected_mode, sarcasm, warmth, tamil, verbosity):
    block = format_personality_sentences(
        mode=expected_mode,
        sarcasm=sarcasm,
        warmth=warmth,
        tamil_mix=tamil,
        verbosity=verbosity,
    )
    assert f"Your operating mode is {expected_mode}." in block
    assert f"Sarcasm level is {sarcasm} out of 10" in block
    assert f"Warmth level is {warmth} out of 10" in block
    assert f"Tamil mix is {tamil} percent" in block
    assert f"Verbosity is {verbosity}" in block

    if sarcasm >= 9:
        assert "sharp" in block.lower() or "roast" in block.lower()
    if sarcasm == 0:
        assert "zero sarcasm" in block.lower() or "gently" in block.lower()


def test_update_personality_all_modes(tmp_path, monkeypatch):
    test_settings_file = tmp_path / "settings_6d.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", test_settings_file)

    for mode in ["Friend", "Jarvis", "Focus", "Engineer"]:
        res = client.post("/api/personality", json={
            "mode": mode,
            "sarcasm": 5,
            "warmth": 6,
            "tamil_mix": 25,
            "verbosity": "normal",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["mode"] == mode
        assert config.PERSONALITY_MODE == mode


# ---------------------------------------------------------------------------
# 2. Master Persona & Few-Shot API Tests
# ---------------------------------------------------------------------------

def test_persona_raw_editor_and_revert(tmp_path, monkeypatch):
    persona_file = tmp_path / "nalla_master.md"
    default_file = tmp_path / "nalla_master.default.md"
    persona_file.write_text("Default Master Persona Content", encoding="utf-8")
    default_file.write_text("Default Master Persona Content", encoding="utf-8")

    from app.brain import persona as persona_module
    test_persona = Persona(path=persona_file)
    monkeypatch.setattr(persona_module, "persona", test_persona)
    monkeypatch.setattr("app.web.server.persona", test_persona)

    # 1. Read
    get_res = client.get("/api/persona/raw")
    assert get_res.status_code == 200
    assert get_res.json()["text"] == "Default Master Persona Content"

    # 2. Update
    put_res = client.put("/api/persona/raw", json={"text": "Custom Edited Persona"})
    assert put_res.status_code == 200
    assert test_persona.path.read_text(encoding="utf-8") == "Custom Edited Persona"

    # 3. Revert
    rev_res = client.post("/api/persona/revert")
    assert rev_res.status_code == 200
    assert rev_res.json()["text"] == "Default Master Persona Content"


def test_few_shot_api_crud_and_revert(tmp_path, monkeypatch):
    fewshot_file = tmp_path / "few_shot.json"
    default_fewshot = tmp_path / "few_shot.default.json"

    canonical = [
        {"user": f"Query {i}", "model": f"Reply {i}"} for i in range(1, 9)
    ]
    fewshot_file.write_text(json.dumps(canonical), encoding="utf-8")
    default_fewshot.write_text(json.dumps(canonical), encoding="utf-8")

    monkeypatch.setattr("app.brain.persona._DEFAULT_FEW_SHOT_PATH", fewshot_file)

    # 1. GET few-shot
    get_res = client.get("/api/fewshot")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["ok"] is True
    assert data["count"] == 8
    assert len(data["examples"]) == 8

    # 2. PUT few-shot update
    updated_pairs = list(canonical)
    updated_pairs[0] = {"user": "Nalla updated!", "model": "Yes boss, sollunga! Updated."}

    put_res = client.put("/api/fewshot", json={"examples": updated_pairs})
    assert put_res.status_code == 200
    assert put_res.json()["ok"] is True
    assert get_few_shot_examples(fewshot_file)[0]["user"] == "Nalla updated!"

    # 3. POST revert
    rev_res = client.post("/api/fewshot/revert")
    assert rev_res.status_code == 200
    assert rev_res.json()["examples"][0]["user"] == "Query 1"


# ---------------------------------------------------------------------------
# 3. Live Prompt Preview & Real-Time Mood Detection
# ---------------------------------------------------------------------------

def test_prompt_preview_endpoint_neutral_mood():
    res = client.post("/api/personality/preview", json={
        "message": "Turn on relay 1 please.",
        "mode": "Friend",
        "sarcasm": 7,
        "warmth": 8,
        "tamil_mix": 40,
        "verbosity": "very short",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "full_prompt" in data
    assert "personality_block" in data
    assert data["stress_detected"] is False
    assert "No mood override active" in data["mood_override"]
    assert "Sarcasm level is 7 out of 10" in data["personality_block"]


def test_prompt_preview_endpoint_stressed_mood():
    res = client.post("/api/personality/preview", json={
        "message": "I am so exhausted, nothing works and I feel stuck.",
        "mode": "Friend",
        "sarcasm": 9,
        "warmth": 4,
        "tamil_mix": 20,
        "verbosity": "short",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["stress_detected"] is True
    assert "[MOOD OVERRIDE" in data["mood_override"]
    assert "[MOOD OVERRIDE" in data["full_prompt"]


# ---------------------------------------------------------------------------
# 4. Ephemeral Test Chat & Model Comparison
# ---------------------------------------------------------------------------

def test_ephemeral_test_chat_does_not_mutate_saved_config():
    initial_sarcasm = config.PERSONALITY_SARCASM
    initial_mode = config.PERSONALITY_MODE

    mock_resp = LLMResponse(text="Yes boss, paathukalam!")
    with patch("app.brain.gemini_provider.GeminiProvider.generate", return_value=mock_resp):
        res = client.post("/api/personality/test", json={
            "message": "Let's debug this tomorrow.",
            "mode": "Focus",
            "sarcasm": 1,
            "warmth": 9,
            "tamil_mix": 15,
            "verbosity": "very short",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["reply"] == "Yes boss, paathukalam!"
        assert "Sarcasm: 1/10" in data["prompt_line"]
        assert "Mode: Focus" in data["prompt_line"]

    # Saved config was NOT mutated
    assert config.PERSONALITY_SARCASM == initial_sarcasm
    assert config.PERSONALITY_MODE == initial_mode


def test_acceptance_roast_mode_sharper_and_calm_night_softer():
    """Verify prompt generated for Roast Mode is sharper and Calm Night is softer."""
    from app.brain.persona import persona

    # 1. Roast Mode prompt
    roast_prompt = persona.build_system_prompt(
        user_text="I want to build another side project.",
        mode="Friend",
        sarcasm=10,
        warmth=1,
        tamil_mix=60,
        verbosity="short",
    )
    assert "Sarcasm level is 10 out of 10" in roast_prompt
    assert "sharp" in roast_prompt.lower() or "roast" in roast_prompt.lower()
    assert "Warmth level is 1 out of 10" in roast_prompt
    assert "unsentimental" in roast_prompt.lower() or "cold" in roast_prompt.lower() or "blunt" in roast_prompt.lower()

    # 2. Calm Night prompt
    calm_prompt = persona.build_system_prompt(
        user_text="I am wrapping up work for tonight.",
        mode="Focus",
        sarcasm=0,
        warmth=10,
        tamil_mix=10,
        verbosity="very short",
    )
    assert "Sarcasm level is 0 out of 10" in calm_prompt
    assert "zero sarcasm" in calm_prompt.lower() or "gently" in calm_prompt.lower()
    assert "Warmth level is 10 out of 10" in calm_prompt
    assert "gentle" in calm_prompt.lower() or "soothing" in calm_prompt.lower() or "caring" in calm_prompt.lower()


def test_compare_models_endpoint():
    resp_a = LLMResponse(text="Reply from Model A (lite)")
    resp_b = LLMResponse(text="Reply from Model B (flash)")

    def mock_generate(messages, tools=None, system_prompt=None, model=None):
        if model == "gemini-2.5-flash":
            return resp_b
        return resp_a

    with patch("app.brain.gemini_provider.GeminiProvider.generate", side_effect=mock_generate):
        res = client.post("/api/personality/compare", json={
            "message": "Compare this message",
            "mode": "Friend",
            "sarcasm": 7,
            "warmth": 8,
            "tamil_mix": 40,
            "verbosity": "very short",
            "model_a": "gemini-3.1-flash-lite",
            "model_b": "gemini-2.5-flash",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "model_a" in data
        assert "model_b" in data
        assert data["model_a"]["reply"] == "Reply from Model A (lite)"
        assert data["model_b"]["reply"] == "Reply from Model B (flash)"
        assert "latency_ms" in data["model_a"]
        assert "latency_ms" in data["model_b"]


# ---------------------------------------------------------------------------
# 5. Locked Identity Guard Verification
# ---------------------------------------------------------------------------

def test_locked_identity_guard_items_in_persona_text():
    """Verify that core identity guard rules remain intact in master persona."""
    from app.brain.persona import persona
    prompt = persona.build_system_prompt()

    # Name and wake word
    assert "NALLA SIVAM" in prompt or "NALLA" in prompt
    assert "sollunga" in prompt.lower()

    # Honesty rule
    assert "never fabricate" in prompt.lower() or "never claim" in prompt.lower()

    # Style
    assert "tanglish" in prompt.lower()
