"""app/voice/voice_lab.py

Tamil Voice Lab backend implementation (Prompt D & E):
- Test sentences manager (data/tamil_tests.json)
- Tanglish pronunciation dictionary (data/tanglish_dict.json)
- Tanglish-to-Tamil speech conversion (longest match, case-insensitive, word boundary)
- Comparison Grid audio clip generation & caching in data/voice_lab/
- Rating persistence in data/voice_lab_results.json
- Summary metrics, error tags, flagged words table, and voice recommendations
- Round-trip Character Error Rate (CER) check
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from app.config import BASE_DIR

logger = logging.getLogger("nalla.voice.lab")

DATA_DIR = BASE_DIR / "data"
VOICE_LAB_DIR = DATA_DIR / "voice_lab"
VOICE_LAB_DIR.mkdir(parents=True, exist_ok=True)

TAMIL_TESTS_FILE = DATA_DIR / "tamil_tests.json"
TANGLISH_DICT_FILE = DATA_DIR / "tanglish_dict.json"
KONGU_PHRASES_FILE = DATA_DIR / "kongu_phrases.json"
LAB_RESULTS_FILE = DATA_DIR / "voice_lab_results.json"

_TAMIL_CHAR_RE = re.compile(r"[\u0B80-\u0BFF]")


# -----------------------------------------------------------------------------
# 1. Test Sentences & Data Loaders
# -----------------------------------------------------------------------------

def get_test_sentences(category: str | None = None) -> list[dict[str, Any]]:
    """Return test sentences from data/tamil_tests.json, optionally filtered by category."""
    if not TAMIL_TESTS_FILE.exists():
        return []
    try:
        with open(TAMIL_TESTS_FILE, "r", encoding="utf-8") as f:
            sentences = json.load(f)
        if category and category.lower() != "all":
            sentences = [s for s in sentences if s.get("category", "").lower() == category.lower()]
        return sentences
    except Exception as exc:
        logger.error("Failed to load tamil_tests.json: %s", exc)
        return []


def get_tanglish_dict() -> dict[str, str]:
    """Return Tanglish -> Tamil dictionary."""
    if not TANGLISH_DICT_FILE.exists():
        return {}
    try:
        with open(TANGLISH_DICT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Failed to load tanglish_dict.json: %s", exc)
        return {}


def save_tanglish_dict(mapping: dict[str, str]) -> bool:
    """Save updated Tanglish dictionary."""
    try:
        with open(TANGLISH_DICT_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)
        return True
    except Exception as exc:
        logger.error("Failed to save tanglish_dict.json: %s", exc)
        return False


def get_kongu_phrases() -> list[dict[str, Any]]:
    """Return Kongu curated phrases list."""
    if not KONGU_PHRASES_FILE.exists():
        return []
    try:
        with open(KONGU_PHRASES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Failed to load kongu_phrases.json: %s", exc)
        return []


def save_kongu_phrases(phrases: list[dict[str, Any]]) -> bool:
    """Save Kongu curated phrases."""
    try:
        with open(KONGU_PHRASES_FILE, "w", encoding="utf-8") as f:
            json.dump(phrases, f, indent=2, ensure_ascii=False)
        return True
    except Exception as exc:
        logger.error("Failed to save kongu_phrases.json: %s", exc)
        return False


# -----------------------------------------------------------------------------
# 2. Tanglish-to-Tamil Speech Conversion (Longest-Match)
# -----------------------------------------------------------------------------

def convert_tanglish_to_tamil(text: str, custom_dict: dict[str, str] | None = None) -> str:
    """Convert Tanglish words to Tamil script using longest-match regex substitution.

    Rules:
    - Case-insensitive matching.
    - Matches only whole word tokens (or tokens bounded by punctuation/spaces).
    - Tokens NOT in the dictionary remain strictly untouched (English words stay English).
    """
    mapping = custom_dict if custom_dict is not None else get_tanglish_dict()
    if not mapping or not text:
        return text

    # Sort keys by length descending (longest-match first)
    sorted_keys = sorted(mapping.keys(), key=len, reverse=True)
    if not sorted_keys:
        return text

    # Build regex with word boundaries: (?:\b|_)(word)(?:\b|_)
    pattern_str = r"\b(" + "|".join(re.escape(k) for k in sorted_keys) + r")\b"
    pattern = re.compile(pattern_str, re.IGNORECASE)

    def _replace_match(match: re.Match) -> str:
        word = match.group(1).lower()
        return mapping.get(word, match.group(1))

    return pattern.sub(_replace_match, text)


def get_sentence_variants(item: dict[str, Any], custom_dict: dict[str, str] | None = None) -> dict[str, str]:
    """Return the three variants for a test sentence:
    a) tamil_script: pure Tamil script
    b) tanglish: Tanglish in English letters
    c) dict_converted: Tanglish after applying pronunciation dictionary
    """
    tanglish = item.get("tanglish", "")
    tamil = item.get("tamil", "")
    dict_converted = convert_tanglish_to_tamil(tanglish, custom_dict=custom_dict)
    return {
        "a_tamil": tamil,
        "b_tanglish": tanglish,
        "c_dict": dict_converted,
    }


# -----------------------------------------------------------------------------
# 3. Audio Generation & Caching in data/voice_lab/
# -----------------------------------------------------------------------------

def _make_clip_filename(sentence_id: str, variant: str, voice_id: str, engine: str) -> str:
    safe_voice = re.sub(r"[^\w\-]", "_", voice_id)
    safe_engine = re.sub(r"[^\w\-]", "_", engine)
    return f"{sentence_id}_{variant}_{safe_engine}_{safe_voice}.wav"


def generate_or_get_lab_clip(
    sentence_id: str,
    variant: str,
    voice_id: str,
    engine: str = "edge-tts",
    tts_manager: Any = None,
) -> dict[str, Any]:
    """Generate or retrieve a cached audio clip for (sentence x variant x voice x engine).

    Returns:
        {
            "sentence_id": ...,
            "variant": ...,
            "voice_id": ...,
            "engine": ...,
            "text": ...,
            "audio_url": ...,
            "time_to_first_audio_ms": ...,
            "cached": True/False,
            "error": None or str
        }
    """
    sentences = {s["id"]: s for s in get_test_sentences()}
    sentence_item = sentences.get(sentence_id)
    if not sentence_item:
        return {"error": f"Sentence '{sentence_id}' not found"}

    variants = get_sentence_variants(sentence_item)
    variant_key = f"{variant}"
    # Map common alias (e.g. 'a', 'b', 'c')
    if variant_key == "a":
        variant_key = "a_tamil"
    elif variant_key == "b":
        variant_key = "b_tanglish"
    elif variant_key == "c":
        variant_key = "c_dict"

    text_to_speak = variants.get(variant_key, sentence_item.get("tamil", ""))
    filename = _make_clip_filename(sentence_id, variant_key, voice_id, engine)
    clip_path = VOICE_LAB_DIR / filename

    # Check cache
    if clip_path.exists() and clip_path.stat().st_size > 0:
        return {
            "sentence_id": sentence_id,
            "variant": variant_key,
            "voice_id": voice_id,
            "engine": engine,
            "text": text_to_speak,
            "audio_url": f"/api/voice/lab/audio/{filename}",
            "time_to_first_audio_ms": 0.0,
            "cached": True,
            "error": None,
        }

    # Synthesize audio
    t0 = time.perf_counter()
    audio_bytes = b""
    try:
        from app.voice.pipeline import default_pipeline
        mgr = tts_manager or default_pipeline.tts

        # Detect language
        has_tamil = bool(_TAMIL_CHAR_RE.search(text_to_speak))
        target_lang = "ta" if has_tamil else "en"

        slot = {
            "engine": engine,
            "voice": voice_id,
            "rate": 1.0,
            "volume": 100,
        }
        audio_bytes, _ = mgr._synthesize_sentence(text_to_speak, target_lang, slot)
    except Exception as exc:
        logger.warning("Lab synthesis failed: %s", exc)
        return {
            "sentence_id": sentence_id,
            "variant": variant_key,
            "voice_id": voice_id,
            "engine": engine,
            "text": text_to_speak,
            "audio_url": None,
            "time_to_first_audio_ms": 0.0,
            "cached": False,
            "error": str(exc),
        }

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    if not audio_bytes:
        return {
            "sentence_id": sentence_id,
            "variant": variant_key,
            "voice_id": voice_id,
            "engine": engine,
            "text": text_to_speak,
            "audio_url": None,
            "time_to_first_audio_ms": elapsed_ms,
            "cached": False,
            "error": "Engine returned empty audio bytes (possible network or quota limit)",
        }

    # Write to disk cache
    try:
        with open(clip_path, "wb") as f:
            f.write(audio_bytes)
    except Exception as exc:
        logger.error("Failed saving lab clip to disk: %s", exc)

    return {
        "sentence_id": sentence_id,
        "variant": variant_key,
        "voice_id": voice_id,
        "engine": engine,
        "text": text_to_speak,
        "audio_url": f"/api/voice/lab/audio/{filename}",
        "time_to_first_audio_ms": elapsed_ms,
        "cached": False,
        "error": None,
    }


def clear_lab_cache(sentence_id: str | None = None) -> int:
    """Clear all or specific sentence cached clips."""
    count = 0
    if not VOICE_LAB_DIR.exists():
        return 0
    for p in VOICE_LAB_DIR.glob("*.wav"):
        if sentence_id is None or p.name.startswith(f"{sentence_id}_"):
            try:
                p.unlink()
                count += 1
            except Exception:
                pass
    return count


# -----------------------------------------------------------------------------
# 4. Ratings & Results Persistence
# -----------------------------------------------------------------------------

def load_lab_results() -> dict[str, Any]:
    """Load current results from data/voice_lab_results.json."""
    if not LAB_RESULTS_FILE.exists():
        return {"ratings": {}, "history": []}
    try:
        with open(LAB_RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ratings": {}, "history": []}


def save_lab_rating(
    sentence_id: str,
    voice_id: str,
    variant: str,
    engine: str,
    rating: str,  # "Good" | "OK" | "Bad"
    issue_tags: list[str] | None = None,
    flagged_word: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Save or update rating for a cell."""
    data = load_lab_results()
    key = f"{sentence_id}:{voice_id}:{variant}"
    record = {
        "sentence_id": sentence_id,
        "voice_id": voice_id,
        "variant": variant,
        "engine": engine,
        "rating": rating,
        "issue_tags": issue_tags or [],
        "flagged_word": flagged_word.strip(),
        "note": note.strip(),
        "timestamp": time.time(),
    }
    data["ratings"][key] = record
    data["history"].append(record)

    try:
        with open(LAB_RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("Failed saving lab rating: %s", exc)

    return record


# -----------------------------------------------------------------------------
# 5. Summary Maths, Issue Tags, and Voice Recommendations
# -----------------------------------------------------------------------------

_RATING_WEIGHTS = {
    "good": 1.0,
    "ok": 0.5,
    "bad": 0.0,
}


def compute_lab_summary() -> dict[str, Any]:
    """Calculate average scores per voice per category, common issue tags,
    table of flagged words, and voice recommendations.
    """
    results_data = load_lab_results()
    ratings_map = results_data.get("ratings", {})
    sentences = {s["id"]: s for s in get_test_sentences()}

    # Group scores by (voice_id, category)
    voice_cat_scores: dict[str, dict[str, list[float]]] = {}
    voice_totals: dict[str, list[float]] = {}
    issue_tags_counts: dict[str, int] = {}
    flagged_words_list: list[dict[str, Any]] = []

    for key, rec in ratings_map.items():
        voice = rec.get("voice_id", "unknown")
        sid = rec.get("sentence_id", "")
        rating_str = rec.get("rating", "").lower()
        score = _RATING_WEIGHTS.get(rating_str, 0.5)

        cat = sentences.get(sid, {}).get("category", "other")

        if voice not in voice_cat_scores:
            voice_cat_scores[voice] = {}
            voice_totals[voice] = []
        if cat not in voice_cat_scores[voice]:
            voice_cat_scores[voice][cat] = []

        voice_cat_scores[voice][cat].append(score)
        voice_totals[voice].append(score)

        for tag in rec.get("issue_tags", []):
            issue_tags_counts[tag] = issue_tags_counts.get(tag, 0) + 1

        fw = rec.get("flagged_word", "")
        if fw:
            flagged_words_list.append({
                "word": fw,
                "sentence_id": sid,
                "voice_id": voice,
                "note": rec.get("note", ""),
                "timestamp": rec.get("timestamp", 0),
            })

    # Compute averages
    voice_summary: dict[str, Any] = {}
    for voice, cat_map in voice_cat_scores.items():
        cat_averages = {c: round(sum(scores) / len(scores), 2) for c, scores in cat_map.items()}
        total_scores = voice_totals.get(voice, [])
        overall = round(sum(total_scores) / len(total_scores), 2) if total_scores else 0.0
        voice_summary[voice] = {
            "overall_score": overall,
            "ratings_count": len(total_scores),
            "by_category": cat_averages,
        }

    # Best Tamil voice recommendation: highest score among voices capable of Tamil
    best_tamil_voice = "ta-IN-PallaviNeural"
    best_tamil_score = -1.0
    best_english_voice = "achird"
    best_english_score = -1.0

    for voice, summary in voice_summary.items():
        score = summary["overall_score"]
        if "ta-" in voice or "valluvar" in voice.lower() or "pallavi" in voice.lower():
            if score > best_tamil_score:
                best_tamil_score = score
                best_tamil_voice = voice
        else:
            if score > best_english_score:
                best_english_score = score
                best_english_voice = voice

    # Sort common issue tags
    sorted_tags = sorted(issue_tags_counts.items(), key=lambda x: x[1], reverse=True)

    return {
        "voice_summary": voice_summary,
        "top_issue_tags": [{"tag": t, "count": c} for t, c in sorted_tags],
        "flagged_words": flagged_words_list,
        "recommendations": {
            "best_tamil_voice": best_tamil_voice,
            "best_tamil_score": best_tamil_score if best_tamil_score >= 0 else None,
            "best_english_voice": best_english_voice,
            "best_english_score": best_english_score if best_english_score >= 0 else None,
        },
        "total_ratings": len(ratings_map),
    }


def export_lab_results_csv() -> str:
    """Generate CSV string containing all rating records and scores."""
    results_data = load_lab_results()
    ratings_map = results_data.get("ratings", {})
    sentences = {s["id"]: s for s in get_test_sentences()}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Sentence ID", "Category", "Voice ID", "Engine", "Variant",
        "Rating", "Score", "Issue Tags", "Flagged Word", "Note", "Timestamp"
    ])

    for key, rec in ratings_map.items():
        sid = rec.get("sentence_id", "")
        cat = sentences.get(sid, {}).get("category", "")
        rating = rec.get("rating", "")
        score = _RATING_WEIGHTS.get(rating.lower(), "")
        writer.writerow([
            sid,
            cat,
            rec.get("voice_id", ""),
            rec.get("engine", ""),
            rec.get("variant", ""),
            rating,
            score,
            "; ".join(rec.get("issue_tags", [])),
            rec.get("flagged_word", ""),
            rec.get("note", ""),
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.get("timestamp", 0))),
        ])

    return output.getvalue()


# -----------------------------------------------------------------------------
# 6. Round-Trip Character Error Rate (CER) Check
# -----------------------------------------------------------------------------

def compute_cer(reference: str, hypothesis: str) -> float:
    """Calculate Character Error Rate (CER) via Levenshtein distance."""
    ref = reference.strip()
    hyp = hypothesis.strip()
    if not ref:
        return 0.0 if not hyp else 1.0

    r_len = len(ref)
    h_len = len(hyp)

    # Dynamic programming matrix for Levenshtein distance
    dp = [[0] * (h_len + 1) for _ in range(r_len + 1)]
    for i in range(r_len + 1):
        dp[i][0] = i
    for j in range(h_len + 1):
        dp[0][j] = j

    for i in range(1, r_len + 1):
        for j in range(1, h_len + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,       # deletion
                dp[i][j - 1] + 1,       # insertion
                dp[i - 1][j - 1] + cost  # substitution
            )

    dist = dp[r_len][h_len]
    return min(1.0, round(dist / r_len, 3))


def run_round_trip_check(
    clip_filename: str,
    expected_text: str,
    stt_provider: Any = None,
) -> dict[str, Any]:
    """Transcribe generated audio clip back with STT and compute Character Error Rate (CER).

    Note for UI: State clearly that STT errors can confuse this check; it is a hint, not a verdict.
    """
    clip_path = VOICE_LAB_DIR / clip_filename
    if not clip_path.exists():
        return {"error": f"Audio clip '{clip_filename}' not found on disk."}

    try:
        with open(clip_path, "rb") as f:
            audio_bytes = f.read()

        if stt_provider is None:
            from app.voice.pipeline import default_pipeline
            stt_provider = default_pipeline.stt

        result = stt_provider.transcribe(audio_bytes)
        transcribed_text = result.text.strip()
        cer = compute_cer(expected_text, transcribed_text)

        return {
            "clip_filename": clip_filename,
            "expected_text": expected_text,
            "transcribed_text": transcribed_text,
            "cer": cer,
            "flagged": cer > 0.35,  # High error flag
            "stt_confidence": getattr(result, "confidence", 1.0),
            "disclaimer": "Round-trip STT check is an automated hint; STT engine limitations can affect accuracy.",
        }
    except Exception as exc:
        logger.warning("Round trip check error: %s", exc)
        return {"error": str(exc)}
