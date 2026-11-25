"""app/voice/tamil_pipeline.py

Tamil Text Pipeline & Speech-To-Text Handler (Prompt E):
1. Script segmentation: split reply into sentences, then runs by script (Tamil vs Latin),
   merging runs shorter than 3 words into neighbors to avoid excessive switching.
2. TTS-safe cleaner: strip emoji, markdown, URLs (pronounce as 'link'), and symbols.
3. Tanglish-to-Tamil speech conversion: applied before TTS when mode is 'tanglish_display'.
4. Tamil text mode coordinator (tanglish_display, tamil_script, english_only).
5. Kongu touch injector using curated data/kongu_phrases.json.
6. STT engine manager (faster-whisper model sizes + Gemini audio transcription).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.voice.voice_lab import convert_tanglish_to_tamil, get_kongu_phrases

logger = logging.getLogger("nalla.voice.tamil")

_TAMIL_CHAR_RE = re.compile(r"[\u0B80-\u0BFF]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MARKDOWN_RE = re.compile(r"[*_~`#>\[\]\(\)\-\+\|]")
_EMOJI_RE = re.compile(
    r"[\U0001F600-\U0001F64F"  # emoticons
    r"\U0001F300-\U0001F5FF"  # symbols & pictographs
    r"\U0001F680-\U0001F6FF"  # transport & map symbols
    r"\U0001F1E0-\U0001F1FF"  # flags
    r"\U00002702-\U000027B0"
    r"\U000024C2-\U0001F251"
    r"]+",
    flags=re.UNICODE,
)


# -----------------------------------------------------------------------------
# 1. TTS-Safe Text Cleaning
# -----------------------------------------------------------------------------

def clean_text_for_tamil_tts(text: str) -> str:
    """Prepare text for TTS:
    - Replace URLs with 'link'
    - Strip emojis
    - Strip markdown symbols
    - Collapse excessive whitespace
    """
    if not text:
        return ""

    # Replace URLs
    cleaned = _URL_RE.sub(" link ", text)

    # Strip emoji
    cleaned = _EMOJI_RE.sub("", cleaned)

    # Strip code blocks and inline code ticks
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)

    # Strip markdown bold, italics, headers, bullets
    cleaned = re.sub(r"[*_~#>\[\]\(\)\-\+\|]", " ", cleaned)

    # Clean double dashes, arrows
    cleaned = re.sub(r"[-=]{2,}", " ", cleaned)
    cleaned = re.sub(r"[➔➜→←]", " ", cleaned)

    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# -----------------------------------------------------------------------------
# 2. Script Segmentation & Short Run Merging
# -----------------------------------------------------------------------------

def detect_script(word: str) -> str:
    """Return 'ta' if word contains Tamil script, 'en' if Latin, else 'other'."""
    if _TAMIL_CHAR_RE.search(word):
        return "ta"
    if _LATIN_CHAR_RE.search(word):
        return "en"
    return "other"


def segment_sentence_by_script(sentence: str) -> list[tuple[str, str]]:
    """Segment a sentence into runs of Tamil script ('ta') and Latin script ('en').
    Merges runs shorter than 3 words into their neighbors to avoid rapid voice jitter.
    """
    sentence = clean_text_for_tamil_tts(sentence)
    words = sentence.split()
    if not words:
        return []

    # 1. Group into contiguous runs
    raw_runs: list[dict[str, Any]] = []
    current_script = detect_script(words[0])
    if current_script == "other":
        current_script = "en"
    current_words = [words[0]]

    for w in words[1:]:
        s = detect_script(w)
        if s == "other" or s == current_script:
            current_words.append(w)
        else:
            raw_runs.append({
                "script": current_script,
                "words": current_words,
            })
            current_script = s
            current_words = [w]

    raw_runs.append({
        "script": current_script,
        "words": current_words,
    })

    if len(raw_runs) <= 1:
        return [(" ".join(raw_runs[0]["words"]), raw_runs[0]["script"])]

    # 2. Merge runs shorter than 3 words into adjacent neighbors
    merged_runs: list[dict[str, Any]] = []
    for r in raw_runs:
        if not merged_runs:
            merged_runs.append(r)
            continue

        # If current run is shorter than 3 words, merge into previous run
        if len(r["words"]) < 3:
            merged_runs[-1]["words"].extend(r["words"])
        else:
            # If previous run was shorter than 3 words and this run is distinct, check if prev should merge into this
            if len(merged_runs[-1]["words"]) < 3 and len(merged_runs) == 1:
                # Merge into current run's script
                r["words"] = merged_runs[-1]["words"] + r["words"]
                merged_runs = [r]
            else:
                merged_runs.append(r)

    # 3. Final consolidation of adjacent runs having the same script
    final_segments: list[tuple[str, str]] = []
    for r in merged_runs:
        text = " ".join(r["words"]).strip()
        if not text:
            continue
        if final_segments and final_segments[-1][1] == r["script"]:
            prev_text, prev_lang = final_segments.pop()
            final_segments.append((f"{prev_text} {text}", prev_lang))
        else:
            final_segments.append((text, r["script"]))

    return final_segments


def segment_reply(full_reply: str) -> list[tuple[str, str]]:
    """Split complete reply into sentence and sub-sentence runs by script."""
    # First split on sentence boundaries
    raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", full_reply)
    all_runs: list[tuple[str, str]] = []

    for sent in raw_sentences:
        clean = sent.strip()
        if not clean:
            continue
        runs = segment_sentence_by_script(clean)
        all_runs.extend(runs)

    return all_runs


# -----------------------------------------------------------------------------
# 3. Tamil Speech Preprocessor
# -----------------------------------------------------------------------------

def prepare_text_for_speech(
    text: str,
    tamil_text_mode: str = "tanglish_display",
    dict_mapping: dict[str, str] | None = None,
) -> str:
    """Prepare text for TTS based on the active Tamil text mode:
    - 'tanglish_display': convert Tanglish dictionary words to Tamil script for speech
    - 'tamil_script': already in Tamil script, clean for TTS
    - 'english_only': clean for TTS
    """
    cleaned = clean_text_for_tamil_tts(text)
    if tamil_text_mode == "tanglish_display":
        return convert_tanglish_to_tamil(cleaned, custom_dict=dict_mapping)
    return cleaned


# -----------------------------------------------------------------------------
# 4. Kongu Touch Formatter
# -----------------------------------------------------------------------------

def get_kongu_flavor_instructions(kongu_touch_level: int) -> str:
    """Generate prompt instructions and curated examples based on Kongu Touch slider (0-10)."""
    if kongu_touch_level <= 0:
        return ""

    phrases = get_kongu_phrases()
    if not phrases:
        return ""

    examples = ", ".join([f"'{p['phrase']}' ({p.get('meaning', '')})" for p in phrases[:6]])
    return (
        f"\n[KONGU DIALECT FLAVOUR (Level {kongu_touch_level}/10)]:\n"
        f"Subtly incorporate authentic Kongu regional colloquial phrasing. "
        f"Allowed Kongu expressions: {examples}. "
        f"Never invent or over-exaggerate dialect words. Keep it natural and warm."
    )
