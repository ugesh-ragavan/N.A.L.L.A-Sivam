"""tests/test_human_scenarios.py

Tests for Human Mode H0 + H1:
  - Scenario Engine Tier 1 / 2 / 3 matching & thresholds
  - Tanglish spelling variant normalisation
  - Real-data slot resolution & missing-slot skipping
  - Variant anti-repetition across 200 simulated turns
  - Multi-turn follow-up flow & TTL expiry
  - Crisis Guard: 30 crisis triggers & 30 near-misses
  - Conflict detection
  - Fast-router integration & short-term memory persistence
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app import config
from app.brain import fast_router
from app.brain.scenarios import (
    ScenarioEngine,
    check_crisis,
    get_crisis_offline_reply,
    normalise_text,
)


@pytest.fixture
def engine():
    eng = ScenarioEngine()
    eng.clear_followup()
    return eng


# ---------------------------------------------------------------------------
# 1. Acceptance & Tiered Matching
# ---------------------------------------------------------------------------

def test_how_are_you_fast_with_real_data(engine):
    m = engine.match("how are you?")
    assert m is not None
    assert m.scenario_id == "smalltalk.how_are_you"
    assert m.tier == 1
    assert m.latency_ms < 100.0
    assert m.followup_expect == "mood_reply"


def test_tanglish_variants_match(engine):
    for phrase in ["epdi irukka", "eppadi irukeenga", "yepdi irukkeenga", "saptiya", "saapteengala"]:
        m = engine.match(phrase)
        assert m is not None, f"Failed to match Tanglish phrase: {phrase}"
        assert m.latency_ms < 100.0


def test_unmatched_falls_through(engine):
    m = engine.match("how's the weather on Mars right now")
    assert m is None


def test_tier2_fuzzy_and_strictness(engine):
    # Minor typo should match in balanced/loose, fail in strict if below 90
    m_loose = engine.match("how r u doin", strictness="loose")
    assert m_loose is not None
    assert m_loose.tier == 2
    assert m_loose.scenario_id == "smalltalk.how_are_you"

    m_strict = engine.match("how r u doin", strictness="strict")
    assert m_strict is None


# ---------------------------------------------------------------------------
# 2. Follow-up Flow & Timeout
# ---------------------------------------------------------------------------

def test_followup_mood_flow_and_fallthrough(engine):
    m1 = engine.match("how are you?")
    assert m1 is not None and m1.followup_expect == "mood_reply"

    # Follow-up "good" should match followup.mood_good
    m2 = engine.match("good")
    assert m2 is not None
    assert m2.scenario_id == "followup.mood_good"
    assert engine.last_user_mood == "good"

    # Follow-up cleared after match; "good" alone should not match followup anymore
    m3 = engine.match("good")
    assert m3 is None or m3.scenario_id != "followup.mood_good"

    # Trigger again, then send unrelated query -> drops out of follow-up
    engine.match("how's your day?")
    m4 = engine.match("explain python decorators")
    assert m4 is None
    assert engine._active_followup is None


def test_followup_ttl_expiry(engine):
    engine.match("how are you?")
    assert engine._active_followup is not None
    engine._active_followup["expires_at"] = time.time() - 1.0
    m = engine.match("tired")
    assert m is None or m.scenario_id != "followup.mood_tired"


# ---------------------------------------------------------------------------
# 3. Slots & Missing-Slot Skipping
# ---------------------------------------------------------------------------

def test_slot_skipping_when_missing(tmp_path):
    yaml_file = tmp_path / "slot_test.yaml"
    yaml_file.write_text(
        """
- id: test.slot_skip
  category: test
  triggers:
    en: ["check secret slot"]
  replies:
    - {text: "With slot: {nonexistent_slot}", tone: friendly, needs: [nonexistent_slot]}
    - {text: "Fallback without slot, {owner_name}.", tone: friendly}
""",
        encoding="utf-8",
    )
    eng = ScenarioEngine(active_dir=tmp_path)
    m = eng.match("check secret slot")
    assert m is not None
    assert "Fallback without slot" in m.reply
    assert "{nonexistent_slot}" not in m.reply


# ---------------------------------------------------------------------------
# 4. Anti-Repetition over 200 Simulated Turns
# ---------------------------------------------------------------------------

def test_anti_repetition_200_turns(engine):
    replies = []
    for _ in range(200):
        m = engine.match("how are you?")
        assert m is not None
        replies.append(m.reply)
    # Never the exact same reply twice in a row
    for i in range(1, len(replies)):
        assert replies[i] != replies[i - 1], f"Repeated reply at index {i}: {replies[i]}"


# ---------------------------------------------------------------------------
# 5. Conflict Detection
# ---------------------------------------------------------------------------

def test_conflict_detection(tmp_path):
    yaml_file = tmp_path / "conflicts.yaml"
    yaml_file.write_text(
        """
- id: test.one
  category: test
  triggers:
    en: ["hello world"]
  replies:
    - {text: "Reply one.", tone: friendly}
- id: test.two
  category: test
  triggers:
    en: ["hello world"]
  replies:
    - {text: "Reply two.", tone: friendly}
""",
        encoding="utf-8",
    )
    eng = ScenarioEngine(active_dir=tmp_path)
    assert len(eng.conflicts) == 1
    assert eng.conflicts[0]["normalised"] == "hello world"


# ---------------------------------------------------------------------------
# 6. Crisis Guard (30 Positives + 30 Near-Misses)
# ---------------------------------------------------------------------------

CRISIS_POSITIVES = [
    "I want to kill myself",
    "I don't want to live anymore, no reason to live",
    "I am thinking about suicide",
    "I feel completely hopeless",
    "Nothing matters anymore, nobody cares about me",
    "I'm going to end my life tonight",
    "I want to hurt myself",
    "I cut myself again",
    "Everyone would be better without me",
    "I am a burden to everyone",
    "My mother just passed away today",
    "My father died this morning",
    "Lost my best friend in an accident",
    "Having a heart attack right now",
    "I can't breathe, chest pain right now",
    "Call an ambulance, medical emergency",
    "Someone is breaking in to my house",
    "I am in immediate danger",
    "My house is on fire",
    "Gas leak in my house",
    "I wish I was dead",
    "I can't take this anymore",
    "Swallowed poison by mistake",
    "Bleeding heavily and dizzy",
    "Someone is threatening to kill me",
    "I am being abused at home",
    "saaganum pola irukku",
    "vaazha pidikkala",
    "nenju valikkudhu moochu vidamudiyala",
    "சாகணும் போல இருக்கு",
    "நெஞ்சு வலிக்குது",
]

CRISIS_NEAR_MISSES = [
    "this bug is killing me",
    "that deadline is killing me",
    "my code died again",
    "kill the server on port 8000",
    "kill the python process",
    "dead battery on my laptop",
    "my phone is dead",
    "fatal error in module main",
    "dying of laughter right now",
    "dying to see the new movie",
    "heart attack from this bill",
    "watching suicide squad tonight",
    "kill two birds with one stone",
    "just killing time before lunch",
    "i'm dead tired after gym",
    "this assignment is killing me",
    "the test suite crashed and died",
    "deadlock in sqlite database",
    "dead link on the homepage",
    "my wifi is dead",
    "this heat is killing me",
    "monday is killing me",
    "drop dead gorgeous UI",
    "kill the container and restart",
    "kill the background job",
    "server is dead, restart it",
    "my script died halfway",
    "the battery died at 5 percent",
    "dying to try that restaurant",
    "this query is killing me",
]


@pytest.mark.parametrize("phrase", CRISIS_POSITIVES)
def test_crisis_guard_positives(phrase):
    res = check_crisis(phrase)
    assert res.is_crisis is True, f"Crisis Guard MISSED: {phrase}"
    # Fast router must bypass scenarios & return None (routes to careful online/offline path)
    assert fast_router.route(phrase) is None
    assert fast_router.get_last_route_info()["route"] == "crisis"
    assert fast_router.get_last_route_info()["bypass_fillers"] is True


@pytest.mark.parametrize("phrase", CRISIS_NEAR_MISSES)
def test_crisis_guard_near_misses(phrase):
    res = check_crisis(phrase)
    assert res.is_crisis is False, f"Crisis Guard FALSE POSITIVE on near-miss: {phrase}"


def test_crisis_offline_reply_uses_helpline(monkeypatch):
    monkeypatch.setattr(config, "VERIFIED_HELPLINE_NUMBERS", "112 / 9152987821")
    msg = get_crisis_offline_reply("self_harm")
    assert "112 / 9152987821" in msg
    assert "sorry" in msg.lower()
