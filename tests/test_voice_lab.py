"""tests/test_voice_lab.py

Comprehensive tests for Prompt D (Tamil Voice Lab):
1. Test sentence loading from data/tamil_tests.json.
2. Input variant generation (Tamil script, Tanglish, pronunciation dictionary).
3. Audio generation, latency measurement, and disk caching in data/voice_lab/.
4. Rating persistence, issue tags, and flagged words in data/voice_lab_results.json.
5. Summary maths: category averages, top issue tags, and voice recommendations.
6. CSV export formatting.
7. Round-trip Character Error Rate (CER) check.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from app.voice.voice_lab import (
    get_test_sentences,
    get_sentence_variants,
    generate_or_get_lab_clip,
    save_lab_rating,
    load_lab_results,
    compute_lab_summary,
    export_lab_results_csv,
    compute_cer,
    run_round_trip_check,
    clear_lab_cache,
    VOICE_LAB_DIR,
)


def test_get_test_sentences():
    all_sentences = get_test_sentences()
    assert len(all_sentences) >= 19
    ids = {s["id"] for s in all_sentences}
    assert "f1" in ids
    assert "c1" in ids
    assert "k1" in ids
    assert "m1" in ids
    assert "n1" in ids
    assert "p1" in ids
    assert "e1" in ids

    # Test category filter
    colloquial = get_test_sentences(category="colloquial")
    assert len(colloquial) >= 8
    assert all(s["category"] == "colloquial" for s in colloquial)


def test_sentence_variants_generation():
    item = {
        "id": "c1",
        "category": "colloquial",
        "tanglish": "Yes boss, sollunga!",
        "tamil": "எஸ் பாஸ், சொல்லுங்க!",
    }
    variants = get_sentence_variants(item, custom_dict={"sollunga": "சொல்லுங்க", "boss": "பாஸ்"})
    assert variants["a_tamil"] == "எஸ் பாஸ், சொல்லுங்க!"
    assert variants["b_tanglish"] == "Yes boss, sollunga!"
    # Tanglish dictionary applied: 'boss' and 'sollunga' converted, 'Yes' untouched
    assert "சொல்லுங்க" in variants["c_dict"]
    assert "பாஸ்" in variants["c_dict"]
    assert "Yes" in variants["c_dict"]


def test_generate_and_cache_lab_clip(monkeypatch):
    clear_lab_cache("f1")

    # Mock TTS synthesis
    mock_mgr = MagicMock()
    mock_mgr._synthesize_sentence.return_value = (b"TEST_WAV_AUDIO_DATA", {"engine": "edge-tts"})

    result = generate_or_get_lab_clip(
        sentence_id="f1",
        variant="a_tamil",
        voice_id="ta-IN-PallaviNeural",
        engine="edge-tts",
        tts_manager=mock_mgr,
    )
    assert result["cached"] is False
    assert result["audio_url"] is not None
    assert mock_mgr._synthesize_sentence.called

    # Second call must hit disk cache
    mock_mgr._synthesize_sentence.reset_mock()
    cached_result = generate_or_get_lab_clip(
        sentence_id="f1",
        variant="a_tamil",
        voice_id="ta-IN-PallaviNeural",
        engine="edge-tts",
        tts_manager=mock_mgr,
    )
    assert cached_result["cached"] is True
    assert cached_result["time_to_first_audio_ms"] == 0.0
    assert not mock_mgr._synthesize_sentence.called

    # Cleanup
    clear_lab_cache("f1")


def test_rating_persistence_and_summary_maths(monkeypatch, tmp_path):
    temp_results = tmp_path / "test_results.json"
    monkeypatch.setattr("app.voice.voice_lab.LAB_RESULTS_FILE", temp_results)

    # Save ratings
    save_lab_rating(
        sentence_id="f1",
        voice_id="ta-IN-PallaviNeural",
        variant="a_tamil",
        engine="edge-tts",
        rating="Good",
        issue_tags=[],
    )
    save_lab_rating(
        sentence_id="c1",
        voice_id="ta-IN-PallaviNeural",
        variant="a_tamil",
        engine="edge-tts",
        rating="OK",
        issue_tags=["wrong stress"],
        flagged_word="sollunga",
    )
    save_lab_rating(
        sentence_id="f1",
        voice_id="achird",
        variant="b_tanglish",
        engine="gemini",
        rating="Good",
        issue_tags=[],
    )

    summary = compute_lab_summary()
    assert summary["total_ratings"] == 3

    v_summary = summary["voice_summary"]
    assert "ta-IN-PallaviNeural" in v_summary
    # Good (1.0) and OK (0.5) average to 0.75
    assert v_summary["ta-IN-PallaviNeural"]["overall_score"] == 0.75

    recs = summary["recommendations"]
    assert recs["best_tamil_voice"] == "ta-IN-PallaviNeural"
    assert recs["best_english_voice"] == "achird"

    # Flagged words
    flagged = summary["flagged_words"]
    assert any(fw["word"] == "sollunga" for fw in flagged)

    # CSV Export
    csv_text = export_lab_results_csv()
    assert "ta-IN-PallaviNeural" in csv_text
    assert "sollunga" in csv_text


def test_character_error_rate_computation():
    # Exact match
    assert compute_cer("வணக்கம்", "வணக்கம்") == 0.0
    # Empty
    assert compute_cer("", "") == 0.0
    # Different
    cer = compute_cer("hello", "helo")
    assert 0.1 < cer < 0.3


def test_round_trip_check(monkeypatch, tmp_path):
    clip_file = tmp_path / "test_clip.wav"
    clip_file.write_bytes(b"RIFFdummyWAVdata")

    monkeypatch.setattr("app.voice.voice_lab.VOICE_LAB_DIR", tmp_path)

    # Mock STT provider
    mock_stt = MagicMock()
    mock_stt.transcribe.return_value = MagicMock(text="வணக்கம் பாஸ்", confidence=0.95)

    res = run_round_trip_check("test_clip.wav", "வணக்கம் பாஸ்", stt_provider=mock_stt)
    assert res["cer"] == 0.0
    assert res["flagged"] is False
    assert res["transcribed_text"] == "வணக்கம் பாஸ்"
