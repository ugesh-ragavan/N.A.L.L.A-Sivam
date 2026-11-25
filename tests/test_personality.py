"""tests/test_personality.py

Comprehensive tests for Nalla's Personality Engine & Prompt Builder:
- Plain sentences for all personality slider configurations.
- Preset modes (Friend, Jarvis, Focus, Engineer) and arbitrary slider combinations.
- Mood stress override (tired, stuck, frustrated, sad, anxious, can't, exhausted).
- Expressive audio tags conditioning and stripping.
- Few-shot loader (8 canonical pairs, 16 messages).
- REST API GET & POST /api/personality.
- Settings persistence to data/settings.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from app import config
from app.brain.persona import (
    Persona,
    detect_stress_mood,
    format_personality_sentences,
    load_few_shot_messages,
    strip_expressive_tags,
)
from app.web.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. Personality Slider Formatting & Presets
# ---------------------------------------------------------------------------

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


@pytest.mark.parametrize("mode,sarcasm,warmth,tamil_mix,verbosity", [
    ("Friend", 7, 8, 40, "very short"),
    ("Jarvis", 3, 6, 10, "short"),
    ("Focus", 1, 4, 0, "very short"),
    ("Engineer", 5, 5, 20, "normal"),
    ("ExtremeSarcasm", 10, 0, 100, "very short"),
    ("ZeroSarcasmMaxWarmth", 0, 10, 0, "normal"),
])
def test_format_personality_sentences_all_combinations(mode, sarcasm, warmth, tamil_mix, verbosity):
    block = format_personality_sentences(
        mode=mode,
        sarcasm=sarcasm,
        warmth=warmth,
        tamil_mix=tamil_mix,
        verbosity=verbosity,
    )
    assert f"Your operating mode is {mode}." in block
    assert f"Sarcasm level is {sarcasm} out of 10" in block
    assert f"Warmth level is {warmth} out of 10" in block
    assert f"Tamil mix is {tamil_mix} percent" in block
    assert f"Verbosity is {verbosity}" in block


# ---------------------------------------------------------------------------
# 2. Mood Override (Stress Detection)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stress_text", [
    "I'm feeling so tired today, boss.",
    "I am completely stuck on this bug.",
    "This is so frustrating and I'm frustrated.",
    "I feel really sad about how the sprint went.",
    "I'm very anxious about the deployment.",
    "I can't seem to get this working.",
    "I cannot figure this out.",
    "I am exhausted from midnight debugging.",
    "I'm struggling to get this feature done.",
])
def test_mood_override_triggers_on_stress_words(stress_text):
    assert detect_stress_mood(stress_text) is True

    persona = Persona()
    prompt = persona.build_system_prompt(user_text=stress_text)
    assert "[MOOD OVERRIDE: The user seems stressed: no sarcasm, be warm and brief.]" in prompt


@pytest.mark.parametrize("neutral_text", [
    "What's the status of the MQTT broker?",
    "Can you explain async IO in Python?",
    "Turn on relay 1 please.",
    "Hey Nalla, let's start a new project.",
    "What time is it in Tokyo?",
])
def test_mood_override_absent_on_neutral_words(neutral_text):
    assert detect_stress_mood(neutral_text) is False

    persona = Persona()
    prompt = persona.build_system_prompt(user_text=neutral_text)
    assert "[MOOD OVERRIDE" not in prompt


# ---------------------------------------------------------------------------
# 3. Expressive Tags Conditioning & Stripping
# ---------------------------------------------------------------------------

def test_expressive_tags_prompt_conditioning():
    persona = Persona()

    # Off by default
    config.EXPRESSIVE_TAGS = False
    config.TTS_ENGINE = "edge-tts"
    prompt = persona.build_system_prompt()
    assert "Do not include XML or audio tags" in prompt

    # On with Gemini TTS
    config.EXPRESSIVE_TAGS = True
    config.TTS_ENGINE = "gemini"
    prompt_gemini = persona.build_system_prompt()
    assert "<chuckle>, <sigh>, or <short pause>" in prompt_gemini

    # On but with Piper or Edge-TTS (not Gemini)
    config.EXPRESSIVE_TAGS = True
    config.TTS_ENGINE = "piper"
    prompt_piper = persona.build_system_prompt()
    assert "Do not include XML or audio tags" in prompt_piper

    # Reset
    config.EXPRESSIVE_TAGS = False
    config.TTS_ENGINE = "edge-tts"


def test_strip_expressive_tags():
    raw_1 = "<chuckle> Haha, boss, you are funny."
    assert strip_expressive_tags(raw_1) == "Haha, boss, you are funny."

    raw_2 = "Seri boss, <sigh> not again."
    assert strip_expressive_tags(raw_2) == "Seri boss, not again."

    raw_3 = "One moment <short pause> here it is."
    assert strip_expressive_tags(raw_3) == "One moment here it is."

    raw_clean = "Clean reply with no tags."
    assert strip_expressive_tags(raw_clean) == "Clean reply with no tags."


# ---------------------------------------------------------------------------
# 4. Few-shot Examples Loader
# ---------------------------------------------------------------------------

def test_few_shot_loader_loads_8_pairs():
    turns = load_few_shot_messages()
    assert len(turns) == 16  # 8 user + 8 assistant

    # Verify alternating roles
    for i, t in enumerate(turns):
        expected_role = "user" if i % 2 == 0 else "assistant"
        assert t["role"] == expected_role
        assert len(t["content"]) > 0

    # Verify key Nalla signature elements are present
    all_content = " ".join(t["content"] for t in turns)
    assert "sollunga" in all_content.lower()
    assert "graveyard" in all_content.lower()
    assert "paathukalam" in all_content.lower()
    assert "offline" in all_content.lower()  # honesty rule


# ---------------------------------------------------------------------------
# 5. REST API & Settings Persistence
# ---------------------------------------------------------------------------

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
