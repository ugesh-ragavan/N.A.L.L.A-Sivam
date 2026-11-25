"""
scripts/test_tamil_english.py

Benchmark and evaluation script for Tamil and mixed Tamil-English transcription.
Tests 10 realistic sentences across English, Tanglish, and colloquial Tamil.
Compares Whisper 'base' vs 'small' models to evaluate accuracy on Tamil.

Usage:
    python scripts/test_tamil_english.py --model base
    python scripts/test_tamil_english.py --model small
    python scripts/test_tamil_english.py --model all
"""

import argparse
import difflib
import io
import logging
from pathlib import Path
import sys
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np

# Configure minimal logger
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

from app.voice.audio_io import decode_audio_to_array
from app.voice.stt import FasterWhisperSTT
from app.voice.tts import EdgeTTS

TEST_SENTENCES = [
    {
        "id": 1,
        "type": "English",
        "reference": "Yes boss, checking your weather now.",
        "lang": "en",
        "voice": "en-IN-NeerjaNeural",
    },
    {
        "id": 2,
        "type": "Pure Tamil",
        "reference": "இன்னைக்கு மழை பெய்ய வாய்ப்பு இருக்கு.",
        "lang": "ta",
        "voice": "ta-IN-PallaviNeural",
    },
    {
        "id": 3,
        "type": "English",
        "reference": "Nalla, can you remember this project note?",
        "lang": "en",
        "voice": "en-IN-NeerjaNeural",
    },
    {
        "id": 4,
        "type": "Pure Tamil",
        "reference": "வணக்கம் பாஸ், எப்படி இருக்கீங்க?",
        "lang": "ta",
        "voice": "ta-IN-PallaviNeural",
    },
    {
        "id": 5,
        "type": "English",
        "reference": "Set a reminder for evening coffee at 5 PM.",
        "lang": "en",
        "voice": "en-IN-NeerjaNeural",
    },
    {
        "id": 6,
        "type": "Pure Tamil",
        "reference": "நாளைக்கு காலையில எனக்கு ஒரு அலாரம் வை.",
        "lang": "ta",
        "voice": "ta-IN-PallaviNeural",
    },
    {
        "id": 7,
        "type": "English",
        "reference": "What is the temperature in Coimbatore right now?",
        "lang": "en",
        "voice": "en-IN-NeerjaNeural",
    },
    {
        "id": 8,
        "type": "Pure Tamil",
        "reference": "ரொம்ப நன்றி பாஸ், வேலை முடிஞ்சிடுச்சு.",
        "lang": "ta",
        "voice": "ta-IN-PallaviNeural",
    },
    {
        "id": 9,
        "type": "English",
        "reference": "Check if the bedroom fan is running.",
        "lang": "en",
        "voice": "en-IN-NeerjaNeural",
    },
    {
        "id": 10,
        "type": "Pure Tamil",
        "reference": "கொஞ்சம் மெதுவா பேசுங்க, புரியல.",
        "lang": "ta",
        "voice": "ta-IN-PallaviNeural",
    },
]


def convert_audio_to_pcm16_16k(audio_bytes: bytes) -> bytes:
    """Decode audio bytes to 16kHz mono 16-bit PCM."""
    arr, sample_rate = decode_audio_to_array(audio_bytes)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    arr = arr.flatten().astype(np.float32)

    # Normalize if needed
    peak = max(abs(arr.max()), abs(arr.min())) if len(arr) > 0 else 0
    if peak > 1.0:
        arr = arr / peak

    # Resample to 16000 Hz if needed
    target_rate = 16000
    if sample_rate != target_rate and len(arr) > 0:
        target_len = int(len(arr) * target_rate / sample_rate)
        indices = np.linspace(0, len(arr) - 1, target_len)
        arr = np.interp(indices, np.arange(len(arr)), arr)

    pcm16 = (np.clip(arr, -1.0, 1.0) * 32767.0).astype(np.int16)
    return pcm16.tobytes()


def calculate_similarity(s1: str, s2: str) -> float:
    """Calculate character-level similarity ratio between two strings (0.0 to 1.0)."""
    # Normalize punctuation and case
    norm1 = "".join(c for c in s1.lower() if c.isalnum() or c.isspace()).strip()
    norm2 = "".join(c for c in s2.lower() if c.isalnum() or c.isspace()).strip()
    if not norm1 and not norm2:
        return 1.0
    return difflib.SequenceMatcher(None, norm1, norm2).ratio()


def run_benchmark_for_model(model_size: str, tts: EdgeTTS) -> dict[str, Any]:
    print(f"\n================================================================================")
    print(f" TESTING WHISPER MODEL: '{model_size}'")
    print(f"================================================================================")

    stt = FasterWhisperSTT(model_size=model_size, language="auto")

    results = []
    total_time = 0.0

    print(f"{'#':<3} | {'Type':<10} | {'Lang':<5} | {'Acc':<6} | {'Time':<6} | {'Transcript'}")
    print("-" * 80)

    for item in TEST_SENTENCES:
        ref = item["reference"]
        voice = item["voice"]

        # Synthesize audio with EdgeTTS
        audio_bytes = tts.synthesize(ref, voice=voice)
        pcm_bytes = convert_audio_to_pcm16_16k(audio_bytes)

        # Transcribe with Whisper
        t0 = time.perf_counter()
        res = stt.transcribe(pcm_bytes)
        elapsed = time.perf_counter() - t0
        total_time += elapsed

        sim = calculate_similarity(ref, res.text)
        results.append({
            "id": item["id"],
            "type": item["type"],
            "ref": ref,
            "transcription": res.text,
            "detected_lang": res.language,
            "confidence": res.confidence,
            "similarity": sim,
            "time_sec": elapsed,
        })

        preview = res.text[:35] + ("..." if len(res.text) > 35 else "")
        print(f"{item['id']:<3} | {item['type']:<10} | {res.language:<5} | {sim*100:>5.1f}% | {elapsed:>5.2f}s | {preview}")

    # Summary
    avg_sim = sum(r["similarity"] for r in results) / len(results)
    tamil_results = [r for r in results if r["type"] == "Pure Tamil"]
    english_results = [r for r in results if r["type"] == "English"]

    avg_tamil_sim = sum(r["similarity"] for r in tamil_results) / len(tamil_results)
    avg_eng_sim = sum(r["similarity"] for r in english_results) / len(english_results)

    print("-" * 80)
    print(f"Overall Accuracy:       {avg_sim*100:.1f}%")
    print(f"English Accuracy:       {avg_eng_sim*100:.1f}%")
    print(f"Tamil Accuracy:         {avg_tamil_sim*100:.1f}%")
    print(f"Total Inference Time:   {total_time:.2f}s (avg {total_time/len(results):.2f}s/turn)")

    return {
        "model": model_size,
        "results": results,
        "overall_acc": avg_sim,
        "tamil_acc": avg_tamil_sim,
        "eng_acc": avg_eng_sim,
        "total_time": total_time,
    }


def main():
    parser = argparse.ArgumentParser(description="Test Tamil & English transcription accuracy with Whisper STT")
    parser.add_argument("--model", choices=["base", "small", "all"], default="all", help="Whisper model size to evaluate")
    args = parser.parse_args()

    tts = EdgeTTS()

    models_to_test = ["base", "small"] if args.model == "all" else [args.model]
    benchmark_reports = []

    for m in models_to_test:
        report = run_benchmark_for_model(m, tts)
        benchmark_reports.append(report)

    if len(benchmark_reports) > 1:
        print("\n" + "=" * 80)
        print(" COMPARISON SUMMARY: WHISPER 'base' vs 'small'")
        print("=" * 80)
        print(f"{'Model':<10} | {'Overall Acc':<12} | {'Tamil Acc':<12} | {'English Acc':<12} | {'Avg Time/Turn'}")
        print("-" * 80)
        for r in benchmark_reports:
            avg_t = r["total_time"] / len(r["results"])
            print(f"{r['model']:<10} | {r['overall_acc']*100:>10.1f}% | {r['tamil_acc']*100:>10.1f}% | {r['eng_acc']*100:>10.1f}% | {avg_t:>10.2f}s")
        print("=" * 80)

        # Recommendation analysis
        base_rep = benchmark_reports[0]
        small_rep = benchmark_reports[1]
        diff = small_rep["tamil_acc"] - base_rep["tamil_acc"]

        print("\n[RECOMMENDATION]")
        if small_rep["tamil_acc"] > base_rep["tamil_acc"]:
            print(f"• Whisper 'small' delivers +{diff*100:.1f}% higher Tamil accuracy than 'base'.")
            print("• For users speaking colloquial Tamil or Tanglish, Whisper 'small' is recommended.")
            print("• You can switch to 'small' live in Voice Studio or via POST /api/voice/stt-settings.")
        else:
            print(f"• Whisper 'base' performs comparably to 'small' while being faster.")
            print("• 'base' is recommended for lower latency on CPU.")


if __name__ == "__main__":
    main()
