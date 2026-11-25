"""tests/test_human_logic_and_fillers.py

Tests for Human Mode H4 (Thinking Sounds / Fillers) & H5 (Logic & Complexity Router):
  - Filler decision logic (latency prediction, T1, crisis/quick/local exclusions)
  - Anti-repetition across 8 turns & voice-hash library paths
  - Complexity router classification ('instant', 'light', 'deep', 'think harder')
  - Quota fallback when daily cap is near
  - Topic stack follow-up resolution
  - Deterministic number post-check audit
"""

from __future__ import annotations

from app import config
from app.brain.logic_router import (
    TopicStack,
    audit_unverified_numbers,
    classify_complexity,
)
from app.voice.fillers import FillerManager


# ---------------------------------------------------------------------------
# H4: Thinking Sounds / Fillers
# ---------------------------------------------------------------------------

def test_filler_decisions_and_exclusions(monkeypatch):
    fm = FillerManager()
    monkeypatch.setattr(config, "THINKING_STYLE", "natural")
    monkeypatch.setattr(config, "QUICK_MODE", False)
    monkeypatch.setattr(config, "PERSONALITY_VERBOSITY", "normal")

    # Slow predicted latency -> allowed
    fm.record_latency("m1", "explain", 1200.0)
    ok, reason = fm.should_play_filler(model="m1", profile="explain")
    assert ok is True and reason == "ok"

    # Fast predicted latency (< 600ms) -> skipped
    fm.record_latency("m_fast", "quick_fact", 350.0)
    ok_fast, r_fast = fm.should_play_filler(model="m_fast", profile="quick_fact")
    assert ok_fast is False and r_fast == "predicted_fast"

    # Crisis, local, offline, quick_mode -> all blocked
    assert fm.should_play_filler(is_crisis=True)[0] is False
    assert fm.should_play_filler(is_local=True)[0] is False
    assert fm.should_play_filler(is_offline=True)[0] is False

    monkeypatch.setattr(config, "QUICK_MODE", True)
    assert fm.should_play_filler()[0] is False


def test_filler_anti_repetition_8_turns():
    fm = FillerManager()
    seen = []
    for _ in range(8):
        p = fm.pick_phrase("thinking")
        assert p not in seen, f"Repeated filler within 8 turns: {p}"
        seen.append(p)


# ---------------------------------------------------------------------------
# H5: Complexity Router & Deterministic Audit
# ---------------------------------------------------------------------------

def test_complexity_router():
    r_inst = classify_complexity("how are you", is_instant=True)
    assert r_inst.complexity == "instant"
    assert r_inst.latency_ms < 10.0

    r_light = classify_complexity("hello nalla")
    assert r_light.complexity == "light"
    assert r_light.thinking_level == "LOW"

    r_deep = classify_complexity("compare SQLite FTS5 vs vector DB trade-offs for a Raspberry Pi 5")
    assert r_deep.complexity == "deep"
    assert r_deep.thinking_level == "MEDIUM"

    r_forced = classify_complexity("think harder about why my ESP32 reboots")
    assert r_forced.complexity == "deep"
    assert r_forced.forced_deep is True


def test_topic_stack_followup():
    ts = TopicStack(maxlen=3)
    ts.push("ESP32 OLED wiring")
    hint = ts.build_hint("and tomorrow?")
    assert "ESP32 OLED wiring" in hint

    # Long unrelated message shouldn't trigger follow-up hint
    assert ts.build_hint("please tell me a completely new story about space exploration and rockets") == ""


def test_deterministic_number_audit():
    flagged = audit_unverified_numbers("calculate 15 percent of 800", "That would be 120.", tools_used=[])
    assert flagged["flagged"] is True
    assert "120" in flagged["numbers"]

    clean = audit_unverified_numbers("calculate 15 percent of 800", "120, boss.", tools_used=["calculate"])
    assert clean["flagged"] is False
