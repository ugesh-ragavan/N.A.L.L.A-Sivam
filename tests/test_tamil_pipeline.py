"""tests/test_tamil_pipeline.py

Comprehensive tests for Prompt E (Tamil text pipeline & speech-to-text):
1. Script segmentation & short-run merging (< 3 words merged into neighbors).
2. TTS-safe cleaning (emoji, markdown, URLs to 'link', stray symbols).
3. Tanglish-to-Tamil speech conversion (longest match, English untouched).
4. Tamil Text Mode switching in persona system prompt builder.
5. Kongu Touch phrase injection.
"""

from __future__ import annotations

import pytest

from app.brain.persona import persona
from app.voice.tamil_pipeline import (
    clean_text_for_tamil_tts,
    detect_script,
    segment_sentence_by_script,
    segment_reply,
    prepare_text_for_speech,
    get_kongu_flavor_instructions,
)
from app.voice.voice_lab import convert_tanglish_to_tamil


def test_clean_text_for_tamil_tts():
    raw = "Hello boss! 😊 Check this: https://example.com/api with **bold** and `code` text."
    cleaned = clean_text_for_tamil_tts(raw)
    assert "😊" not in cleaned
    assert "https://" not in cleaned
    assert "link" in cleaned
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "bold and code text" in cleaned


def test_script_detection():
    assert detect_script("வணக்கம்") == "ta"
    assert detect_script("Hello") == "en"
    assert detect_script("123") == "other"


def test_segment_sentence_and_merge_short_runs():
    # Long English run followed by long Tamil run
    sent = "Yes boss I checked the system. இன்று வானிலை மிகவும் இனிமையாக இருக்கிறது."
    runs = segment_sentence_by_script(sent)
    assert len(runs) == 2
    assert runs[0][1] == "en"
    assert runs[1][1] == "ta"

    # Short English run (< 3 words) inside Tamil must be merged into Tamil neighbor
    mixed = "வணக்கம் பாஸ் இது ஒரு OK சோதனை முயற்சி."
    # 'OK' is 1 word, so it should merge into adjacent Tamil rather than creating a 1-word English jitter
    runs_merged = segment_sentence_by_script(mixed)
    assert len(runs_merged) == 1
    assert runs_merged[0][1] == "ta"
    assert "OK" in runs_merged[0][0]


def test_convert_tanglish_to_tamil_longest_match():
    custom_dict = {
        "sollunga": "சொல்லுங்க",
        "seri": "சரி",
        "boss": "பாஸ்",
        "paathukalam": "பார்த்துக்கலாம்",
    }
    # Case-insensitive and longest match
    text = "Yes BOSS, sollunga! Seri, paathukalam."
    converted = convert_tanglish_to_tamil(text, custom_dict=custom_dict)
    assert "பாஸ்" in converted
    assert "சொல்லுங்க" in converted
    assert "சரி" in converted
    assert "பார்த்துக்கலாம்" in converted
    # 'Yes' is not in the dictionary, so it stays untouched in English
    assert "Yes" in converted


def test_tamil_text_mode_in_prompt_builder(monkeypatch):
    from app import config

    # 1. Test tanglish_display mode
    monkeypatch.setattr(config, "TAMIL_TEXT_MODE", "tanglish_display")
    prompt_tanglish = persona.build_system_prompt()
    assert "Tanglish display" in prompt_tanglish
    assert "everyday spoken Tamil (colloquial)" in prompt_tanglish

    # 2. Test tamil_script mode
    monkeypatch.setattr(config, "TAMIL_TEXT_MODE", "tamil_script")
    prompt_script = persona.build_system_prompt()
    assert "Tamil script" in prompt_script

    # 3. Test english_only mode
    monkeypatch.setattr(config, "TAMIL_TEXT_MODE", "english_only")
    prompt_en = persona.build_system_prompt()
    assert "English only" in prompt_en


def test_kongu_touch_prompt_injection():
    instructions = get_kongu_flavor_instructions(kongu_touch_level=5)
    assert "KONGU DIALECT FLAVOUR" in instructions
    assert "ஏனுங்க" in instructions
    assert "Never invent" in instructions

    # When level is 0, no instructions added
    assert get_kongu_flavor_instructions(0) == ""
