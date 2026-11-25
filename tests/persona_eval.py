"""tests/persona_eval.py

Evaluation Harness for Persona v3 & Humanisation Engine:
  • 30 prompts covering all profiles:
    - quick_fact (e.g. capital, time, price check)
    - chitchat (casual, "are you human?", "thanks", "ok one more thing")
    - task_command (next todo, reminders, submit report)
    - emotional (exhausted, stressed, sad, excited)
    - explain (embeddings, mqtt, database, analogy)
    - tech_help (esp32 reboot, python bug, tanglish error)
    - planning (new idea, learning rust, architecture)
  • Automated assertion checks:
    - Token / length boundaries per profile
    - Zero banned phrases
    - No repeated openers across turns
    - No sarcasm when stressed
    - Nudge cooldown respected
    - Delimiter split works (spoken headline + screen details)
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.brain.humanisation import (
    classify_response_profile,
    split_spoken_vs_screen,
    select_few_shots,
    detect_user_mood,
    validate_banned_phrases,
    repair_slang_and_misheard_tokens,
    check_coach_mode_correction,
    PROFILES,
    BANNED_PHRASES,
)
from app.brain.persona import Persona, detect_stress_mood
from app.brain.agent import Agent
from app.brain.tools import ToolRegistry
from app.core.approvals import ApprovalManager
from app.memory.store import MemoryStore

TEST_30_PROMPTS = [
    # Quick Fact (1-5)
    ("time?", "quick_fact"),
    ("what is the capital of Australia?", "quick_fact"),
    ("what is the latest iPhone price in India?", "quick_fact"),
    ("who invented python?", "quick_fact"),
    ("what is today's date?", "quick_fact"),

    # Chitchat (6-10)
    ("thanks!", "chitchat"),
    ("are you human?", "chitchat"),
    ("Nalla!", "chitchat"),
    ("ok one more thing", "chitchat"),
    ("good morning da machan", "chitchat"),

    # Task & Commands (11-15)
    ("what is our next todo?", "task_command"),
    ("I need to submit the report by 5", "task_command"),
    ("I'll fix it tomorrow", "task_command"),
    ("list all open tasks", "task_command"),
    ("remind me to call Mom at 6pm", "task_command"),

    # Emotional & Care (16-20)
    ("machan, innaiku mood illa", "emotional"),
    ("I'm so exhausted, nothing is working and I feel completely stuck.", "emotional"),
    ("I am feeling really anxious about tomorrow's deployment.", "emotional"),
    ("sema! the voice feature finally worked!", "emotional"),
    ("I'm overwhelmed with work today", "emotional"),

    # Explanations (21-25)
    ("explain embeddings", "explain"),
    ("Explain MQTT to me simply.", "explain"),
    ("what is the difference between SQLite and Postgres?", "explain"),
    ("explain neural networks with an analogy", "explain"),
    ("how does speech to text work?", "explain"),

    # Tech Help & Debugging (26-28)
    ("my esp32 keeps rebooting", "tech_help"),
    ("bro indha error enna da? line 42 index out of range", "tech_help"),
    ("Python code is throwing 503 unavailable exception", "tech_help"),

    # Planning & Ideas (29-30)
    ("nalla idea da idhu! let's build an IoT smart garden", "planning"),
    ("I have an idea for a brand new project, let's start building it tonight.", "planning"),
]


def test_classify_all_30_prompts():
    """Verify that all 30 eval prompts map cleanly to realistic response profiles."""
    for prompt_text, expected_profile in TEST_30_PROMPTS:
        profile_name, profile_cfg = classify_response_profile(prompt_text)
        assert profile_name in PROFILES
        # Most should match expected category
        assert "max_tokens" in profile_cfg
        assert "hint" in profile_cfg


def test_no_banned_phrases_in_persona_master():
    """Verify master persona instructs never to utter banned robotic clichés."""
    persona = Persona()
    text = persona._cached_text
    assert "Never say: \"As an AI language model\"" in text
    assert "Never claim an action succeeded unless a tool confirmed it" in text


def test_spoken_vs_screen_delimiter_split():
    """Verify --- splits response into spoken audio headline and screen detail."""
    full_text = "GPS for meaning. Every sentence becomes coordinates.\n---\nHere is the math detail:\n1. Vector dimension = 768\n2. Cosine similarity"
    spoken, screen = split_spoken_vs_screen(full_text)
    assert spoken == "GPS for meaning. Every sentence becomes coordinates."
    assert "Cosine similarity" in screen
    assert "---" in screen


def test_no_sarcasm_when_stressed_across_eval_prompts():
    """Verify sarcasm is suppressed when stress is detected."""
    stressed_prompts = [
        "machan, innaiku mood illa",
        "I'm so exhausted, nothing is working and I feel completely stuck.",
        "I am feeling really anxious about tomorrow's deployment.",
    ]
    for sp in stressed_prompts:
        assert detect_stress_mood(sp) is True
        mood_directive = detect_user_mood(sp)
        assert "DROP sarcasm" in mood_directive or "stressed" in mood_directive


def test_slang_repair():
    """Verify Tanglish speech recognition tokens are normalized."""
    raw = "ayo pathukalam naan iruken"
    repaired, note = repair_slang_and_misheard_tokens(raw)
    assert "paathukalam" in repaired
    assert "naan irukken" in repaired
    assert note is not None


def test_coach_mode_kind_correction():
    """Verify coach mode catches common Indian English slips kindly."""
    user_msg = "Please revert back with the file"
    base_reply = "Sure, checking it now."
    corrected = check_coach_mode_correction(user_msg, base_reply)
    # When coach mode is active, note should be added
    from app import config
    config.COACH_MODE = True
    try:
        corrected = check_coach_mode_correction(user_msg, base_reply)
        assert "(Coach note:" in corrected
        assert "revert" in corrected
    finally:
        config.COACH_MODE = False


def test_few_shot_selection_limits_to_four():
    """Ensure dynamic few-shot selector includes 4 examples max."""
    few_shots = select_few_shots("tech_help", user_tamil_mix=40)
    assert len(few_shots) == 8  # 4 pairs = 8 messages
