"""app/brain/humanisation.py

NALLA SIVAM Humanisation Engine (Step 2):
  1. Response Profiles: quick_fact, chitchat, task_command, emotional, explain, tech_help, planning.
     Sets token ceilings and per-turn guidance hints. Supports overrides ("brief", "in detail").
  2. Spoken vs Screen Delimiter Split: parses answers into spoken summary (<1-2 sentences)
     and screen details separated by `---`.
  3. Dynamic Few-Shot Selection: selects 2 profile-matched examples + 2 general examples.
  4. Mood & Context Detection: stress words, ALL CAPS, curt messages, excitement.
  5. Anti-Repetition Tracker: tracks last 15 openers and 10 closers, flags banned phrases.
  6. Tanglish Quality & Slang Repair: injects Coimbatore Tanglish vocabulary samples and
     repairs common STT mis-heard Tanglish tokens.
  7. Coach Mode & Dedicated Follow-Through.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Tuple

from app import config

logger = logging.getLogger("nalla.brain.humanisation")

# Response profile configurations
PROFILES: dict[str, dict[str, Any]] = {
    "quick_fact": {
        "max_tokens": 70,
        "hint": "Profile: quick fact. One short sentence or phrase only. No conversational fluff.",
    },
    "chitchat": {
        "max_tokens": 90,
        "hint": "Profile: casual chitchat. 1-2 natural sentences like a friend talking.",
    },
    "task_command": {
        "max_tokens": 120,
        "hint": "Profile: task or command. State the status or next action Decisively in 1-2 punchy sentences.",
    },
    "emotional": {
        "max_tokens": 120,
        "hint": "Profile: emotional check-in. Drop sarcasm completely: warm, calm, brief, suggest one small next step.",
    },
    "explain": {
        "max_tokens": 280,
        "hint": (
            "Profile: explanation. Spoken headline first (1-2 sentences with an analogy), "
            "then if detailed explanation is needed, put '---' on its own line followed by the screen details."
        ),
    },
    "tech_help": {
        "max_tokens": 320,
        "hint": (
            "Profile: technical debugging / coding. 1-2 sentence spoken diagnosis first, "
            "then if line-by-line steps or code snippets are needed, put '---' on its own line followed by the screen details."
        ),
    },
    "planning": {
        "max_tokens": 220,
        "hint": "Profile: planning & ideas. Validate the core idea, point out any hole kindly, offer next step in 1-2 sentences.",
    },
}

# Anti-repetition state
_opener_history: list[str] = []
_closer_history: list[str] = []
_session_verbosity_override: str | None = None

BANNED_PHRASES = [
    "as an ai",
    "as a language model",
    "i'd be happy to help",
    "certainly!",
    "great question!",
    "i hope this helps",
    "feel free to ask",
    "how can i assist",
    "is there anything else",
]


def classify_response_profile(user_text: str) -> Tuple[str, dict[str, Any]]:
    """Classify user turn into a response profile based on keywords and patterns."""
    text = user_text.strip().lower()

    # Explicit user overrides
    global _session_verbosity_override
    if re.search(r"\b(in detail|explain properly|elaborate|deep dive|step by step)\b", text):
        _session_verbosity_override = "detailed"
    elif re.search(r"\b(brief|short|tl;?dr|one line|quick answer)\b", text):
        _session_verbosity_override = "short"

    # Emotional patterns
    if re.search(r"\b(tired|exhausted|stuck|frustrated|sad|depressed|overwhelmed|crying|mood illa|stressed|anxious|can't do this)\b", text):
        return "emotional", PROFILES["emotional"]

    # Tech help / debugging
    if re.search(r"\b(error|bug|traceback|exception|crash|reboot|serial|esp32|relay|code|function|syntax|failed|compile)\b", text):
        return "tech_help", PROFILES["tech_help"]

    # Explain patterns
    if re.search(r"\b(explain|what is|how does|what are|analogy|difference between|teach me|tell me about)\b", text):
        return "explain", PROFILES["explain"]

    # Planning / Idea patterns
    if re.search(r"\b(idea|plan|build|project|start|roadmap|should i learn|architecture|feature)\b", text):
        return "planning", PROFILES["planning"]

    # Task / Todo / Browser commands
    if re.search(r"\b(todo|task|tasks|remind|reminder|schedule|due|next up|next task|done|mark done|open|browse|search|website|browser|click)\b", text):
        return "task_command", PROFILES["task_command"]

    # Quick fact queries (short questions: who, when, where, capital, time)
    if re.search(r"\b(capital|who is|when was|where is|how much|price|cost|release date|version)\b", text) or len(text.split()) <= 4 and text.endswith("?"):
        return "quick_fact", PROFILES["quick_fact"]

    # Default: chitchat
    return "chitchat", PROFILES["chitchat"]


def split_spoken_vs_screen(full_text: str) -> Tuple[str, str]:
    """Split response into spoken part (for TTS) and full screen part.

    Uses `---` delimiter if present. Otherwise uses full text.
    """
    if "---" in full_text:
        parts = full_text.split("---", 1)
        spoken = parts[0].strip()
        screen = full_text.strip()
        return spoken, screen
    return full_text.strip(), full_text.strip()


def select_few_shots(profile: str, user_tamil_mix: int = 40) -> list[dict[str, Any]]:
    """Select 2 examples matching the profile + 2 general examples."""
    few_shot_file = Path("persona/few_shot.json")
    if not few_shot_file.exists():
        return []

    try:
        data = json.loads(few_shot_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    matched = [ex for ex in data if ex.get("profile") == profile]
    general = [ex for ex in data if ex.get("profile") != profile]

    selected = matched[:2]
    # Fill remaining from general to get 4 total
    for g in general:
        if len(selected) >= 4:
            break
        if g not in selected:
            selected.append(g)

    # Format for model injection
    formatted = []
    for s in selected:
        formatted.append({"role": "user", "content": s["user"]})
        formatted.append({"role": "assistant", "content": s["model"]})
    return formatted


def detect_user_mood(user_text: str, time_band: str = "morning") -> str:
    """Detect user mood signals and format a 1-line guidance directive."""
    text = user_text.strip()
    upper_count = sum(1 for c in text if c.isupper())
    is_shouting = len(text) > 8 and (upper_count / max(1, len(text))) > 0.65

    # Excitement
    if re.search(r"\b(awesome|great news|finally|worked|passed|success|yay|sema)\b", text, re.IGNORECASE) or text.count("!") >= 2:
        return "MOOD: User is excited or celebrating a win. Celebrate properly and share genuine excitement!"

    # Stress / sadness
    if re.search(r"\b(tired|exhausted|stuck|frustrated|sad|depressed|overwhelmed|crying|mood illa|stressed|anxious|give up)\b", text, re.IGNORECASE):
        return "MOOD: User seems stressed or exhausted: DROP sarcasm completely, be warm and calm, suggest one small next step."

    if is_shouting:
        return "MOOD: User is speaking in all caps / intense. Stay grounded, cool, and helpful."

    if len(text.split()) <= 2 and text.endswith("."):
        return "MOOD: User is being curt. Give an ultra-brief direct response."

    if time_band == "late_night":
        return "MOOD: Late night turn. Keep it calm, quiet, and friendly."

    return "MOOD: Normal casual mood."


def record_opener_and_closer(reply: str) -> None:
    """Track Nalla's opening words and closing words to avoid repetitive speech."""
    words = reply.strip().split()
    if not words:
        return
    opener = words[0].lower().strip(".,!?:;\"'")
    closer = words[-1].lower().strip(".,!?:;\"'")

    global _opener_history, _closer_history
    if opener and opener not in _opener_history[-15:]:
        _opener_history.append(opener)
        if len(_opener_history) > 15:
            _opener_history.pop(0)

    if closer and closer not in _closer_history[-10:]:
        _closer_history.append(closer)
        if len(_closer_history) > 10:
            _closer_history.pop(0)


def get_anti_repetition_directive() -> str:
    """Return prompt line to prevent repetitive openers and banned phrases."""
    recent_openers = _opener_history[-8:]
    if not recent_openers:
        return "ANTI-REPETITION: Start naturally. Never start consecutive replies with the same word."
    openers_str = ", ".join(f"'{o}'" for o in set(recent_openers))
    return f"ANTI-REPETITION: Avoid starting this reply with recently used openers: [{openers_str}]. Start with variety."


def get_tanglish_sample_instructions(tamil_mix: int) -> str:
    """Load curated Coimbatore Tanglish style sample from data/tanglish_style.json."""
    style_path = Path("data/tanglish_style.json")
    if not style_path.exists():
        return ""
    try:
        data = json.loads(style_path.read_text(encoding="utf-8"))
        level = "light" if tamil_mix < 30 else ("heavy" if tamil_mix > 65 else "medium")
        samples = data.get("sample_phrases", {}).get(level, [])
        sample_str = " | ".join(f'"{s}"' for s in samples[:3])
        return (
            f"TANGLISH VIBE (Coimbatore Style, {level}): {sample_str}. "
            f"Real spoken Tamil grammar with English words naturally integrated. "
            f"Never use bookish written Tamil."
        )
    except Exception:
        return ""


def repair_slang_and_misheard_tokens(raw_stt: str) -> Tuple[str, str | None]:
    """Pre-LLM fuzzy check for mis-heard speech recognition tokens against slang glossary.

    Returns (repaired_text, optional_note_for_prompt).
    """
    style_path = Path("data/tanglish_style.json")
    if not style_path.exists():
        return raw_stt, None

    try:
        data = json.loads(style_path.read_text(encoding="utf-8"))
        glossary = data.get("slang_glossary", {})
    except Exception:
        glossary = {}

    lower = raw_stt.lower()
    rep = raw_stt
    # Common speech-to-text misspellings in Tanglish
    stt_fixes = {
        "naan iruken": "naan irukken",
        "paathukalam": "paathukalam",
        "pathukalam": "paathukalam",
        "aiyo": "aiyo",
        "ayo": "aiyo",
        "machan": "machan",
        "macha": "machan",
        "sollunga": "sollunga",
        "solunga": "sollunga",
    }
    for wrong, right in stt_fixes.items():
        if wrong in lower:
            rep = re.sub(rf"\b{wrong}\b", right, rep, flags=re.IGNORECASE)

    note = "SLANG CONTEXT: The user is speaking conversational Tanglish. Infer likely intent from context. Ask a confirming question ONLY if meaning is genuinely ambiguous and risky."
    return rep, note


def check_coach_mode_correction(user_text: str, reply: str) -> str:
    """If coach_mode is on or user explicitly requested it, add a kind correction."""
    is_coach_on = getattr(config, "COACH_MODE", False)
    user_requested = bool(re.search(r"\b(correct my english|correct my tamil|coach me)\b", user_text, re.IGNORECASE))

    if not is_coach_on and not user_requested:
        return reply

    # Don't correct if user is stressed or sad
    if re.search(r"\b(tired|exhausted|sad|depressed|overwhelmed|crying|mood illa|stressed)\b", user_text, re.IGNORECASE):
        return reply

    # Check common English slips (e.g., "revert back", "prepone", "did you went")
    correction = None
    if re.search(r"\brevert back\b", user_text, re.IGNORECASE):
        correction = "Quick tip: 'revert' already means go back, so you can just say 'revert' instead of 'revert back'."
    elif re.search(r"\bdid you (went|saw|spoke|came)\b", user_text, re.IGNORECASE):
        correction = "Quick English tip: After 'did you', use base verb ('did you go', 'did you see')."
    elif re.search(r"\bpass out\b", user_text, re.IGNORECASE) and "college" in user_text.lower():
        correction = "Quick phrasing tip: 'graduated' is clearer than 'passed out' (which usually means fainted)."

    if correction:
        return f"{reply}\n\n(Coach note: {correction})"
    return reply


def validate_banned_phrases(text: str) -> list[str]:
    """Test utility to ensure no robotic or banned phrases leaked into response."""
    lower = text.lower()
    return [b for b in BANNED_PHRASES if b in lower]


# ---------------------------------------------------------------------------
# Human Speech Polish (Prompt H6)
# ---------------------------------------------------------------------------

_TONE_PRESETS: dict[str, dict[str, str]] = {
    "dry": {"gemini_preset": "dry, calm, conversational wit", "rate_delta": "+5%"},
    "friendly": {"gemini_preset": "warm, upbeat, natural friend", "rate_delta": "+0%"},
    "warm": {"gemini_preset": "warm, gentle, supportive", "rate_delta": "-5%"},
    "serious": {"gemini_preset": "warm, calm, steady", "rate_delta": "-5%"},
    "excited": {"gemini_preset": "upbeat, energetic, bright", "rate_delta": "+5%"},
    "tanglish": {"gemini_preset": "warm, upbeat, natural friend", "rate_delta": "+0%"},
}

_variety_openers_10: list[str] = []
_variety_first3: list[str] = []
_variety_closers: list[str] = []
_turns_since_callback: int = 5


def map_tone_to_preset(tone: str, engine: str = "edge-tts") -> dict[str, str | None]:
    """Map a reply tone to one of 3 constant Gemini TTS presets or rate adjustment."""
    spec = _TONE_PRESETS.get((tone or "friendly").lower(), _TONE_PRESETS["friendly"])
    is_gemini = "gemini" in (engine or "").lower()
    return {
        "style_preset": spec["gemini_preset"] if is_gemini else None,
        "rate_delta": spec["rate_delta"],
    }


def should_backchannel(hold_duration_s: float, is_instant: bool = False) -> bool:
    """Backchannel only when user held walkie-talkie > 8s and answer is not instant."""
    if not getattr(config, "HUMAN_SPEECH_POLISH", True):
        return False
    return (hold_duration_s > 8.0) and (not is_instant)


def record_reply_for_variety(reply: str) -> dict[str, Any]:
    """Track first 3 words, opener, and closer across all routes for Variety Audit."""
    global _turns_since_callback
    _turns_since_callback += 1
    words = reply.strip().split()
    first3 = " ".join(w.lower().strip(".,!?") for w in words[:3])
    opener = words[0].lower().strip(".,!?") if words else ""
    closer = words[-1].lower().strip(".,!?") if words else ""

    repeated_in_10 = opener in _variety_openers_10[-10:] if opener else False
    if opener:
        _variety_openers_10.append(opener)
        if len(_variety_openers_10) > 100:
            _variety_openers_10.pop(0)
    if first3:
        _variety_first3.append(first3)
        if len(_variety_first3) > 200:
            _variety_first3.pop(0)
    if closer:
        _variety_closers.append(closer)
        if len(_variety_closers) > 200:
            _variety_closers.pop(0)
    return {"first3": first3, "opener": opener, "repeated_in_10": repeated_in_10}


def get_variety_audit() -> dict[str, Any]:
    """Return repeated openers, closers, and most common 3-word starts."""
    from collections import Counter
    opener_counts = Counter(_variety_openers_10)
    first3_counts = Counter(_variety_first3)
    closer_counts = Counter(_variety_closers)
    return {
        "total_logged": len(_variety_first3),
        "top_openers": opener_counts.most_common(5),
        "top_first3": first3_counts.most_common(5),
        "top_closers": closer_counts.most_common(5),
    }


def maybe_callback_hint(open_tasks: list[str] | None = None) -> str:
    """At most 1 short callback hint per 5 replies referencing an open task."""
    global _turns_since_callback
    if not getattr(config, "HUMAN_SPEECH_POLISH", True):
        return ""
    if _turns_since_callback < 5 or not open_tasks:
        return ""
    _turns_since_callback = 0
    return f"Optional callback (use at most once if natural): 'still working on {open_tasks[0]}?'"

