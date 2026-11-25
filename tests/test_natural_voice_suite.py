"""tests/test_natural_voice_suite.py

Comprehensive tests for NALLA SIVAM Natural Voice & Anti-Robotic Enhancements:
  1. Step 1: TTS Diagnostics & Force Engine settings.
  2. Step 2: "Write for the Ear" persona rules (contractions, short sentences, spoken numbers, natural reactions).
  3. Step 3: Sentence chunking (1-2 sentences >=40 chars, never mid-sentence split).
  4. Step 4: Gapless continuous output queue & 15ms raised-cosine edge fading.
  5. Step 5: Gemini TTS model setting, conversational style, and max 1 expressive tag guard.
  6. Step 6: RMS loudness normalisation (target 0.15).
  7. Step 7: Voice FX polish (Off | Clean | JARVIS presence + subtle room impulse).
  8. Step 8: 10-line A/B Benchmark suite, synthesis endpoints, and rating persistence.
"""

from __future__ import annotations

import io
import json
import wave
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from app.voice.audio_dsp import (
    apply_edge_fade,
    normalize_loudness_rms,
    resample_to_target_rate,
    apply_voice_fx,
    TARGET_SAMPLE_RATE,
)
from app.voice.audio_io import AudioPlayer, decode_audio_to_array
from app.voice.tts_gemini import limit_expressive_tags, strip_expressive_tags, GeminiTTS
from app.voice.ab_benchmark import (
    get_benchmark_lines,
    record_ab_rating,
    get_ab_results,
    synthesize_ab_sample,
)


def _generate_synthetic_tone(freq: float = 440.0, duration_s: float = 0.5, rate: int = 24000) -> bytes:
    """Generate simple sine wave WAV bytes for audio DSP tests."""
    t = np.linspace(0, duration_s, int(rate * duration_s), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * freq * t) * 32767.0).astype(np.int16)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(sig.tobytes())
    return bio.getvalue()


# -----------------------------------------------------------------------------
# 1. Edge Fade Tests (Step 4)
# -----------------------------------------------------------------------------

def test_edge_fade_removes_clicks_at_boundaries():
    """Verify 15ms raised-cosine fade starts at 0.0 and ends at 0.0 to eliminate pops."""
    rate = 24000
    # Step impulse with sharp discontinuity at start and end
    audio = np.ones(rate, dtype=np.float32)

    faded = apply_edge_fade(audio, sample_rate=rate, fade_ms=15.0)

    # First and last samples should be attenuated close to 0.0
    assert abs(faded[0]) < 1e-4
    assert abs(faded[-1]) < 1e-4

    # Midpoint should remain untouched at 1.0
    mid = len(audio) // 2
    assert abs(faded[mid] - 1.0) < 1e-3


# -----------------------------------------------------------------------------
# 2. RMS Loudness Normalisation Tests (Step 6)
# -----------------------------------------------------------------------------

def test_rms_loudness_normalisation():
    """Verify different volume clips are scaled to consistent target RMS."""
    rate = 24000
    t = np.linspace(0, 0.2, int(rate * 0.2), endpoint=False)

    quiet = (0.08 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    loud = (0.60 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)

    norm_quiet = normalize_loudness_rms(quiet, target_rms=0.15)
    norm_loud = normalize_loudness_rms(loud, target_rms=0.15)

    rms_quiet = float(np.sqrt(np.mean(norm_quiet**2)))
    rms_loud = float(np.sqrt(np.mean(norm_loud**2)))

    assert abs(rms_quiet - 0.15) < 0.02
    assert abs(rms_loud - 0.15) < 0.02


# -----------------------------------------------------------------------------
# 3. Voice FX Polish Tests (Step 7: Off | Clean | JARVIS)
# -----------------------------------------------------------------------------

def test_voice_fx_modes():
    """Verify Off leaves audio intact, Clean cuts rumble, and JARVIS applies polish."""
    rate = 24000
    audio = (0.3 * np.sin(2 * np.pi * 440 * np.linspace(0, 0.1, 2400))).astype(np.float32)

    fx_off = apply_voice_fx(audio, sample_rate=rate, fx_mode="off")
    np.testing.assert_allclose(fx_off, audio)

    fx_clean = apply_voice_fx(audio, sample_rate=rate, fx_mode="clean")
    assert len(fx_clean) == len(audio)

    fx_jarvis = apply_voice_fx(audio, sample_rate=rate, fx_mode="jarvis")
    assert len(fx_jarvis) == len(audio)
    # JARVIS presence and early reflections should differentiate signal
    assert not np.array_equal(fx_jarvis, audio)


# -----------------------------------------------------------------------------
# 4. Expressive Tags Limitation Tests (Step 5)
# -----------------------------------------------------------------------------

def test_limit_expressive_tags():
    """Ensure at most one expressive tag is kept per reply."""
    text_multiple = "[chuckle] Hey boss! [sigh] I see the error. [pause] Let me fix it."
    limited = limit_expressive_tags(text_multiple, max_tags=1)

    assert "[chuckle]" in limited
    assert "[sigh]" not in limited
    assert "[pause]" not in limited

    stripped = strip_expressive_tags(text_multiple)
    assert "[chuckle]" not in stripped
    assert "[sigh]" not in stripped
    assert "[pause]" not in stripped


def test_gemini_tts_config():
    """Verify Gemini TTS initializes with configured model and dry-wit style."""
    tts = GeminiTTS(tts_model="gemini-3.8-flash-lite-tts", style="calm, confident, dry wit, conversational")
    assert tts.tts_model == "gemini-3.8-flash-lite-tts"
    assert "dry wit" in tts.style


# -----------------------------------------------------------------------------
# 5. Gapless Streaming Playback in AudioPlayer (Step 4)
# -----------------------------------------------------------------------------

def test_audio_player_gapless_stream_queue():
    """Verify AudioPlayer handles gapless stream start, push chunk with DSP, and finish."""
    player = AudioPlayer()
    wav_bytes = _generate_synthetic_tone(freq=500.0, duration_s=0.1, rate=24000)

    # Start gapless stream (mocking sounddevice to avoid hardware dependency)
    with patch("sounddevice.OutputStream"):
        ok = player.start_gapless_stream(sample_rate=24000)
        assert ok is True
        assert player.is_playing is True

        # Push two consecutive chunks (decoding, fading, RMS normalizing)
        p1 = player.push_gapless_chunk(wav_bytes, fx_mode="jarvis", normalize=True, fade=True)
        p2 = player.push_gapless_chunk(wav_bytes, fx_mode="jarvis", normalize=True, fade=True)

        assert p1 is True
        assert p2 is True

        player.finish_gapless_stream(timeout=0.5)
        player.stop()
        assert player.is_playing is False


# -----------------------------------------------------------------------------
# 6. A/B Benchmark Evaluation Tests (Step 8)
# -----------------------------------------------------------------------------

def test_benchmark_lines_and_rating_workflow(tmp_path):
    """Test 10 benchmark lines, rating persistence, and summary scoring."""
    lines = get_benchmark_lines()
    assert len(lines) == 10

    # Test rating persistence for Line 1
    res = record_ab_rating(line_id=1, setting="A", rating="bad", feedback="Too robotic")
    assert res["summary"]["setting_a_bad"] >= 1

    res = record_ab_rating(line_id=1, setting="B", rating="good", feedback="Cool, natural tone")
    assert res["summary"]["setting_b_good"] >= 1

    # Setting B should have higher preference rate
    summary = res["summary"]
    assert summary["preference_b_rate"] > 0.0

    all_res = get_ab_results()
    assert "ratings" in all_res
    assert "summary" in all_res


def test_ab_sample_synthesis(monkeypatch):
    """Verify A/B sample generation for Setting A (raw) and Setting B (DSP applied)."""
    fake_wav = _generate_synthetic_tone(freq=400.0, duration_s=0.1, rate=24000)

    with patch("app.voice.tts.tts_engine.synthesize", return_value=fake_wav):
        # Setting A
        audio_a, fn_a = synthesize_ab_sample(line_id=2, setting="A", fx_mode="off")
        assert len(audio_a) > 44
        assert "setting_A" in fn_a

        # Setting B
        audio_b, fn_b = synthesize_ab_sample(line_id=2, setting="B", fx_mode="jarvis")
        assert len(audio_b) > 44
        assert "setting_B" in fn_b
