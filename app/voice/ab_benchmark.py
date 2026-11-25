"""app/voice/ab_benchmark.py

A/B Benchmark and Evaluation Suite for NALLA SIVAM Voice:
Renders 10 standardized benchmark lines under:
  • Setting A (Old Setup):
      - Formal phrasing (no contractions, longer sentences, un-spelled numbers)
      - Raw audio output (no edge fade, no RMS normalization, Voice FX Off)
      - Separate play chunks (unbuffered)
  • Setting B (New Setup):
      - "Write for the Ear" persona rules (contractions, <18 word sentences, spoken numbers/times, dashes/commas for pauses, natural reactions)
      - Continuous gapless playback queue
      - 15ms raised-cosine fade at chunk edges (zero pops/clicks)
      - RMS loudness normalization (target 0.15)
      - Voice FX (Clean / JARVIS presence + subtle room impulse)
      - Gemini TTS model & conversational style string

Keeps user ratings (Good / OK / Bad) persisted in data/ab_test_results.json.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("nalla.voice.ab_benchmark")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
RESULTS_FILE = DATA_DIR / "ab_test_results.json"
SAMPLES_DIR = DATA_DIR / "ab_samples"

BENCHMARK_LINES: list[dict[str, Any]] = [
    {
        "id": 1,
        "title": "Casual Greeting & Personality",
        "old_text": "Hello boss. How may I assist you today? All systems are functioning within normal operational parameters.",
        "new_text": "Hey boss, good to see you! What's on your mind today?",
        "focus": "Warm opening, conversational rhythm vs mechanical greeting",
    },
    {
        "id": 2,
        "title": "Contractions & Natural Flow",
        "old_text": "I have checked all the background servers. There are no critical alerts and memory utilization is low.",
        "new_text": "I've checked the servers, and honestly, everything's running smoother than usual.",
        "focus": "Contractions (I've, everything's) vs uncontracted formal speech",
    },
    {
        "id": 3,
        "title": "Spoken Numbers & Clock Time",
        "old_text": "The current time is 10:45 AM. You have 3 pending tasks scheduled for 14:00 hours today.",
        "new_text": "The clock says ten forty-five, and we've got about three tasks left on the list.",
        "focus": "Natural time & number articulation vs digital 24h clock syntax",
    },
    {
        "id": 4,
        "title": "Dry Wit & Thinking Pause",
        "old_text": "That is an interesting question. I will search the database and provide an answer shortly.",
        "new_text": "Hmm, that's a tricky one. Give me a second, let me think about it.",
        "focus": "Natural reaction ('Hmm'), short sentence chunks (<12 words)",
    },
    {
        "id": 5,
        "title": "Sarcastic & Caring Persona",
        "old_text": "You have been sitting at the terminal for 4 consecutive hours. Prolonged sitting causes fatigue.",
        "new_text": "You've been staring at that code for four hours straight. Drink some water, boss.",
        "focus": "Caring banter with sarcastic punch vs clinical notification",
    },
    {
        "id": 6,
        "title": "Tanglish Flavor & Camaraderie",
        "old_text": "Okay, we can finish this work now. Should I shut down the running processes?",
        "new_text": "Seri boss, let's wrap this up before dinner, what do you say?",
        "focus": "Natural Tanglish marker ('Seri boss'), upbeat companion vibe",
    },
    {
        "id": 7,
        "title": "Pacing & Em-Dash Pause",
        "old_text": "Warning: do not deploy the repository before you push the latest commit to the remote server.",
        "new_text": "Wait—before you hit deploy, did you remember to push the latest commit?",
        "focus": "Em-dash pause creating human anticipation vs monotone warning",
    },
    {
        "id": 8,
        "title": "Quick Reaction & Short Sentences",
        "old_text": "No, that statement is incorrect. The bug you referenced was resolved yesterday morning.",
        "new_text": "Ha, nice try! But that bug was fixed yesterday morning.",
        "focus": "Chuckle/reaction ('Ha'), punchy response",
    },
    {
        "id": 9,
        "title": "Clear & Confident Confirmation",
        "old_text": "The database backup process has completed successfully. Verification checksums have matched.",
        "new_text": "Done. The backups are safely stored and verified.",
        "focus": "Ultra-concise spoken delivery vs robotic status log reading",
    },
    {
        "id": 10,
        "title": "Parting Sign-off",
        "old_text": "I will continue monitoring background operations. Please contact me if additional assistance is needed.",
        "new_text": "Alright, I'll keep the monitors running. Holler if you need anything else.",
        "focus": "Friendly companion sign-off vs sterile corporate prompt",
    },
]


def ensure_samples_dir() -> Path:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    return SAMPLES_DIR


def get_benchmark_lines() -> list[dict[str, Any]]:
    """Return all 10 benchmark lines with text for Setting A and Setting B."""
    return BENCHMARK_LINES


def get_ab_results() -> dict[str, Any]:
    """Load persisted A/B ratings and summary stats."""
    if not RESULTS_FILE.exists():
        return {
            "ratings": [],
            "summary": {
                "total_ratings": 0,
                "setting_a_good": 0,
                "setting_a_ok": 0,
                "setting_a_bad": 0,
                "setting_b_good": 0,
                "setting_b_ok": 0,
                "setting_b_bad": 0,
                "preference_b_rate": 0.0,
            },
        }

    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except Exception as exc:
        logger.warning("Could not read ab_test_results.json: %s", exc)
        return {"ratings": [], "summary": {}}


def record_ab_rating(
    line_id: int,
    setting: str,
    rating: str,
    feedback: str = "",
) -> dict[str, Any]:
    """Record a user rating ('good', 'ok', 'bad') for Setting A or Setting B."""
    setting = setting.upper().strip()
    rating = rating.lower().strip()
    if setting not in ("A", "B"):
        raise ValueError("Setting must be 'A' or 'B'.")
    if rating not in ("good", "ok", "bad"):
        raise ValueError("Rating must be 'good', 'ok', or 'bad'.")

    data = get_ab_results()
    ratings_list = data.get("ratings", [])

    # Replace or add rating for this line_id + setting
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    updated = False
    for r in ratings_list:
        if r.get("line_id") == line_id and r.get("setting") == setting:
            r["rating"] = rating
            r["feedback"] = feedback
            r["timestamp"] = now_iso
            updated = True
            break
    if not updated:
        ratings_list.append({
            "line_id": line_id,
            "setting": setting,
            "rating": rating,
            "feedback": feedback,
            "timestamp": now_iso,
        })

    # Recalculate summary metrics
    a_good = sum(1 for r in ratings_list if r.get("setting") == "A" and r.get("rating") == "good")
    a_ok = sum(1 for r in ratings_list if r.get("setting") == "A" and r.get("rating") == "ok")
    a_bad = sum(1 for r in ratings_list if r.get("setting") == "A" and r.get("rating") == "bad")

    b_good = sum(1 for r in ratings_list if r.get("setting") == "B" and r.get("rating") == "good")
    b_ok = sum(1 for r in ratings_list if r.get("setting") == "B" and r.get("rating") == "ok")
    b_bad = sum(1 for r in ratings_list if r.get("setting") == "B" and r.get("rating") == "bad")

    # Score calculation: good=2, ok=1, bad=0
    a_score = a_good * 2 + a_ok
    b_score = b_good * 2 + b_ok
    tot_score = a_score + b_score
    pref_rate = round((b_score / tot_score * 100.0), 1) if tot_score > 0 else 0.0

    summary = {
        "total_ratings": len(ratings_list),
        "setting_a_good": a_good,
        "setting_a_ok": a_ok,
        "setting_a_bad": a_bad,
        "setting_b_good": b_good,
        "setting_b_ok": b_ok,
        "setting_b_bad": b_bad,
        "preference_b_rate": pref_rate,
    }

    full_data = {"ratings": ratings_list, "summary": summary}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(full_data, f, indent=2)

    return full_data


def synthesize_ab_sample(
    line_id: int,
    setting: str,
    fx_mode: str = "jarvis",
) -> tuple[bytes, str]:
    """Generate audio for Setting A or Setting B and return (wav_bytes, filename)."""
    setting = setting.upper().strip()
    matched = next((item for item in BENCHMARK_LINES if item["id"] == line_id), None)
    if not matched:
        raise ValueError(f"Benchmark line #{line_id} not found.")

    ensure_samples_dir()
    sample_filename = f"line_{line_id}_setting_{setting}_{fx_mode}.wav"
    sample_path = SAMPLES_DIR / sample_filename

    # If already synthesized on disk, return cached file
    if sample_path.exists() and sample_path.stat().st_size > 1000:
        return sample_path.read_bytes(), sample_filename

    from app.voice.audio_io import decode_audio_to_array
    from app.voice.audio_dsp import (
        apply_edge_fade,
        normalize_loudness_rms,
        resample_to_target_rate,
        apply_voice_fx,
        TARGET_SAMPLE_RATE,
    )
    from app.voice.tts import tts_engine

    manager = tts_engine

    if setting == "A":
        # SETTING A (Old): Formal robotic text, RAW output (no edge fade, no RMS norm, FX off)
        text = matched["old_text"]
        raw_audio = manager.synthesize(text, language="en")
        if not raw_audio:
            raise RuntimeError(f"Failed to synthesize Setting A audio for line {line_id}")

        arr, rate = decode_audio_to_array(raw_audio)
        if arr.ndim > 1:
            arr = arr.mean(axis=1)
        arr = arr.astype(np.float32)
        if np.max(np.abs(arr)) > 1.5:
            arr = arr / 32768.0
        if rate != TARGET_SAMPLE_RATE:
            arr = resample_to_target_rate(arr, orig_rate=rate, target_rate=TARGET_SAMPLE_RATE)

        # Raw output: NO RMS normalization, NO FX, NO edge fade
        processed = arr

    else:
        # SETTING B (New): Persona spoken text, 15ms cosine fade, RMS norm, Voice FX
        text = matched["new_text"]
        raw_audio = manager.synthesize(text, language="en")
        if not raw_audio:
            raise RuntimeError(f"Failed to synthesize Setting B audio for line {line_id}")

        arr, rate = decode_audio_to_array(raw_audio)
        if arr.ndim > 1:
            arr = arr.mean(axis=1)
        arr = arr.astype(np.float32)
        if np.max(np.abs(arr)) > 1.5:
            arr = arr / 32768.0
        if rate != TARGET_SAMPLE_RATE:
            arr = resample_to_target_rate(arr, orig_rate=rate, target_rate=TARGET_SAMPLE_RATE)

        # Apply RMS loudness normalization (target 0.15)
        norm = normalize_loudness_rms(arr, target_rms=0.15)
        # Apply Voice FX
        fx = apply_voice_fx(norm, sample_rate=TARGET_SAMPLE_RATE, fx_mode=fx_mode)
        # Apply 15ms raised-cosine fade at chunk edges
        processed = apply_edge_fade(fx, sample_rate=TARGET_SAMPLE_RATE, fade_ms=15.0)

    # Encode float32 numpy array back to WAV bytes
    import io
    import wave
    # Scale float32 [-1.0, 1.0] to int16
    clamped = np.clip(processed, -1.0, 1.0)
    int16_arr = (clamped * 32767.0).astype(np.int16)

    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SAMPLE_RATE)
        wf.writeframes(int16_arr.tobytes())

    wav_bytes = bio.getvalue()
    sample_path.write_bytes(wav_bytes)
    return wav_bytes, sample_filename
