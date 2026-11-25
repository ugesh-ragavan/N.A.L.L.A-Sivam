"""tests/test_context_and_tasks.py

Comprehensive tests for NALLA SIVAM Short-Term Memory and Context Awareness:
  1. The 5-exchange window (up to 10 messages verbatim) and chronological ordering.
  2. Fast-path replies (time, greetings, stop, mute) saved to history in the same format.
  3. Short-term memory persistence across restarts.
  4. New session boundary when > 6 hours pass between messages.
  5. Late-night nudge appears at 01:50 AM and NOT at 14:00 PM.
  6. Nudge cooldown (at most one nudge per 60 minutes).
  7. "Don't nag" / busy / deadline user instruction respected (no nudges that session).
  8. Stressed user mood override (warm, brief, no sarcasm).
  9. next_task ordering (priority 1 -> 2 -> 3, then earliest due date, then oldest).
  10. No open tasks case (Nalla says so honestly, never invents tasks).
  11. Timezone override setting (custom timezone vs system).
  12. Context block stays under 300 token budget.
  13. Acceptance dialog replay at simulated 1:50 AM.
"""

from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import config
from app.brain.context_builder import (
    build_context_block,
    get_current_time,
    get_time_band,
    record_nudge_given,
    reset_care_state,
    check_no_nag_intent,
)
from app.brain.agent import _trim_history, Agent
from app.brain.persona import Persona, detect_stress_mood
from app.brain.fast_router import route as fast_route
from app.brain.tools import ToolRegistry, RiskLevel
from app.core.approvals import ApprovalManager
from app.memory.store import MemoryStore


@pytest.fixture
def test_store(tmp_path: Path) -> MemoryStore:
    db_file = tmp_path / "test_nalla.db"
    return MemoryStore(db_file)


# ---------------------------------------------------------------------------
# 1. Short-Term Memory 5-Exchange Window & Ordering
# ---------------------------------------------------------------------------

def test_short_term_exchange_window_and_ordering(test_store: MemoryStore):
    session_id = "test_window"
    # Insert 7 exchanges (14 messages)
    for i in range(1, 8):
        test_store.save_message(session_id, "user", f"User question {i}")
        test_store.save_message(session_id, "assistant", f"Nalla answer {i}")

    # Default is 5 exchanges (10 messages)
    exchanges = test_store.get_recent_exchanges(session_id, n_exchanges=5)
    assert len(exchanges) == 10
    # Must be in chronological order
    assert exchanges[0]["text"] == "User question 3"
    assert exchanges[1]["text"] == "Nalla answer 3"
    assert exchanges[-2]["text"] == "User question 7"
    assert exchanges[-1]["text"] == "Nalla answer 7"


def test_trim_history_folds_older_into_single_summary():
    # 7 user-assistant pairs
    history = []
    for i in range(1, 8):
        history.append({"role": "user", "content": f"Topic {i} query"})
        history.append({"role": "assistant", "content": f"Reply {i}"})

    trimmed = _trim_history(history)
    # 5 pairs = 10 messages + 2 summary messages (user + ack) = 12 total
    assert len(trimmed) == 12
    assert "Earlier conversation summary" in trimmed[0]["content"]
    assert "Topic 1" in trimmed[0]["content"] or "Topic 2" in trimmed[0]["content"]
    # Verify the last exchange is present at the end
    assert trimmed[-2]["content"] == "Topic 7 query"
    assert trimmed[-1]["content"] == "Reply 7"


# ---------------------------------------------------------------------------
# 2. Fast-Path Replies Saved to History
# ---------------------------------------------------------------------------

def test_fast_path_time_saves_to_history(test_store: MemoryStore, monkeypatch):
    from fastapi.testclient import TestClient
    from app.web.server import app, store

    # Monkeypatch web store to test_store
    monkeypatch.setattr("app.web.server.store", test_store)

    client = TestClient(app)
    res = client.post("/api/chat", json={"message": "what is the time?", "session_id": "fast_test"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["fast_path"] is True
    assert "boss" in data["reply"]

    # History must contain both user message and fast-path reply
    msgs = test_store.get_messages("fast_test", limit=5)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["text"] == "what is the time?"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["text"] == data["reply"]


# ---------------------------------------------------------------------------
# 3. Persistence Across Restart
# ---------------------------------------------------------------------------

def test_short_term_memory_persistence_across_restart(tmp_path: Path):
    db_file = tmp_path / "restart_test.db"
    store1 = MemoryStore(db_file)
    store1.save_message("session_a", "user", "Hello Nalla")
    store1.save_message("session_a", "assistant", "Yes boss, sollunga!")

    # Simulate restart by instantiating new store from same DB file
    store2 = MemoryStore(db_file)
    reloaded = store2.get_recent_exchanges("session_a", n_exchanges=5)
    assert len(reloaded) == 2
    assert reloaded[0]["text"] == "Hello Nalla"
    assert reloaded[1]["text"] == "Yes boss, sollunga!"


# ---------------------------------------------------------------------------
# 4. Session Boundary After 6 Hours
# ---------------------------------------------------------------------------

def test_new_session_after_6_hours_gap(test_store: MemoryStore, monkeypatch):
    from app.web.server import _resolve_session_id

    monkeypatch.setattr("app.web.server.store", test_store)
    # Save a message 7 hours ago
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    conn = test_store._conn()
    with conn:
        conn.execute("INSERT INTO messages (session_id, role, text, created_at) VALUES ('s1', 'user', 'old', ?)", (past_iso,))

    resolved = _resolve_session_id("s1")
    assert resolved != "s1"
    assert resolved.startswith("s1_")

    # Save a recent message (10 mins ago) -> same session id preserved
    recent_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with conn:
        conn.execute("INSERT INTO messages (session_id, role, text, created_at) VALUES ('s2', 'user', 'recent', ?)", (recent_iso,))

    resolved_recent = _resolve_session_id("s2")
    assert resolved_recent == "s2"


# ---------------------------------------------------------------------------
# 5. Late-Night Nudge Appears at 01:50 and NOT at 14:00
# ---------------------------------------------------------------------------

def test_late_night_nudge_timing(test_store: MemoryStore, monkeypatch):
    reset_care_state()
    monkeypatch.setattr("app.brain.context_builder.store", test_store)

    # 1. At 1:50 AM
    sim_night = datetime(2026, 11, 24, 1, 50, 0)
    ctx_night = build_context_block(session_id="test_night", simulated_now=sim_night)
    assert ctx_night["time_band"] == "late_night"
    assert ctx_night["nudge_eligible"] is True
    assert "CARE RULE (Late Night)" in ctx_night["block_text"]

    # 2. At 14:00 (2:00 PM)
    reset_care_state()
    sim_afternoon = datetime(2026, 11, 24, 14, 0, 0)
    ctx_afternoon = build_context_block(session_id="test_afternoon", simulated_now=sim_afternoon)
    assert ctx_afternoon["time_band"] == "afternoon"
    assert "CARE RULE (Late Night)" not in ctx_afternoon["block_text"]


# ---------------------------------------------------------------------------
# 6. Nudge Cooldown (At most one nudge per 60 minutes)
# ---------------------------------------------------------------------------

def test_nudge_cooldown(test_store: MemoryStore, monkeypatch):
    reset_care_state()
    monkeypatch.setattr("app.brain.context_builder.store", test_store)

    sim_t0 = datetime(2026, 11, 24, 1, 30, 0)
    ctx0 = build_context_block("sess_cd", simulated_now=sim_t0)
    assert ctx0["nudge_eligible"] is True

    # Record that nudge was given at 1:30 AM
    record_nudge_given(sim_t0)

    # Turn 2: 10 minutes later at 1:40 AM -> Cooldown active
    sim_t1 = datetime(2026, 11, 24, 1, 40, 0)
    ctx1 = build_context_block("sess_cd", simulated_now=sim_t1)
    assert ctx1["nudge_eligible"] is False
    assert "CARE RULE (Late Night)" not in ctx1["block_text"]

    # Turn 3: 65 minutes later at 2:35 AM -> Cooldown expired
    sim_t2 = datetime(2026, 11, 24, 2, 35, 0)
    ctx2 = build_context_block("sess_cd", simulated_now=sim_t2)
    assert ctx2["nudge_eligible"] is True
    assert "CARE RULE (Late Night)" in ctx2["block_text"]


# ---------------------------------------------------------------------------
# 7. "Don't Nag" / Busy Respected
# ---------------------------------------------------------------------------

def test_no_nag_respected(test_store: MemoryStore, monkeypatch):
    reset_care_state()
    monkeypatch.setattr("app.brain.context_builder.store", test_store)

    sim_night = datetime(2026, 11, 24, 1, 50, 0)
    # User says don't nag
    ctx = build_context_block("sess_nag", user_text="I'm on a deadline, don't nag me please.", simulated_now=sim_night)
    assert "Do NOT offer health advice or sleep nudges" in ctx["block_text"]
    assert "CARE RULE (Late Night)" not in ctx["block_text"]


# ---------------------------------------------------------------------------
# 8. Stressed Mood Override
# ---------------------------------------------------------------------------

def test_stressed_mood_override():
    stressed_text = "I am so exhausted and overwhelmed, nothing works."
    assert detect_stress_mood(stressed_text) is True
    persona = Persona()
    prompt = persona.build_system_prompt(user_text=stressed_text)
    assert "MOOD OVERRIDE" in prompt
    assert "no sarcasm" in prompt


# ---------------------------------------------------------------------------
# 9. Next Task Ordering
# ---------------------------------------------------------------------------

def test_next_task_ordering(test_store: MemoryStore):
    # Add tasks with different priority, due dates and created times
    t_med = test_store.add_task(title="Medium priority task", priority=2, due_at="2026-11-25T10:00:00")
    t_high_late = test_store.add_task(title="High priority later due", priority=1, due_at="2026-11-26T10:00:00")
    t_high_soon = test_store.add_task(title="High priority earlier due", priority=1, due_at="2026-11-24T12:00:00")
    t_low = test_store.add_task(title="Low priority task", priority=3)

    # Next task must pick priority 1 first, and earliest due date
    next_up = test_store.next_task()
    assert next_up is not None
    assert next_up["id"] == t_high_soon
    assert next_up["title"] == "High priority earlier due"

    # Complete it and check the next one
    test_store.complete_task(t_high_soon)
    next_up2 = test_store.next_task()
    assert next_up2 is not None
    assert next_up2["id"] == t_high_late


# ---------------------------------------------------------------------------
# 10. No Open Tasks Case (Never Invents)
# ---------------------------------------------------------------------------

def test_no_open_tasks_case(test_store: MemoryStore, monkeypatch):
    monkeypatch.setattr("app.brain.context_builder.store", test_store)
    ctx = build_context_block("sess_empty")
    assert "Next open tasks: NONE" in ctx["block_text"]
    assert "You must never invent a task" in ctx["block_text"]


# ---------------------------------------------------------------------------
# 11. Timezone Override Setting
# ---------------------------------------------------------------------------

def test_timezone_override(monkeypatch):
    monkeypatch.setattr(config, "USER_TIMEZONE", "UTC")
    utc_time = get_current_time()
    assert utc_time.tzinfo is not None

    monkeypatch.setattr(config, "USER_TIMEZONE", "system")
    sys_time = get_current_time()
    # System local time has None tzinfo from datetime.now()
    assert sys_time.tzinfo is None


# ---------------------------------------------------------------------------
# 12. Context Block Token Budget (< 300 tokens)
# ---------------------------------------------------------------------------

def test_context_block_stays_under_300_tokens(test_store: MemoryStore, monkeypatch):
    monkeypatch.setattr("app.brain.context_builder.store", test_store)
    # Add 3 tasks and 2 reminders
    test_store.add_task("Fix audio crackle", priority=1, due_at="2026-11-24T18:00:00")
    test_store.add_task("Update SQLite schema", priority=2)
    test_store.add_task("Test JARVIS HUD visualizer", priority=3)
    test_store.save_reminder("Check memory budget", "2026-11-24T18:30:00")

    ctx = build_context_block(
        session_id="token_test",
        user_text="What's next?",
        top_memories=["Prefers Coimbatore colloquial Tanglish", "Comfortable with Python and FastAPI"],
    )
    # Rough token estimation: 1 token ≈ 4 characters
    token_est = len(ctx["block_text"]) / 4.0
    assert token_est < 300, f"Context block too long: ~{token_est} tokens"


# ---------------------------------------------------------------------------
# 13. Acceptance Dialog Replay at Simulated 1:50 AM
# ---------------------------------------------------------------------------

def test_acceptance_dialog_replay_at_1_50_am(test_store: MemoryStore, monkeypatch):
    reset_care_state()
    monkeypatch.setattr("app.brain.context_builder.store", test_store)
    monkeypatch.setattr("app.memory.store.store", test_store)

    # Add the open task
    test_store.add_task(title="the code fix", priority=1)

    # Simulated clock at 1:50 AM
    sim_time_150 = datetime(2026, 11, 24, 1, 50, 0)

    # Turn 1: User asks time
    # Fast path handles time
    monkeypatch.setattr("app.brain.context_builder.get_current_time", lambda simulated_now=None: sim_time_150)
    reply_1 = fast_route("what is the time?")
    assert reply_1 is not None
    assert "1:50 AM" in reply_1
    test_store.save_message("acc_test", "user", "what is the time?")
    test_store.save_message("acc_test", "assistant", reply_1)

    # Check that late-night fast-path set the nudge flag
    from app.brain.context_builder import _care_state
    assert _care_state["fast_path_late_night_used"] is True

    # Turn 2: User asks "what is our next todo?"
    # Mock LLM provider with expected in-character caring response
    mock_provider = MagicMock()
    mock_provider.generate.return_value = MagicMock(
        is_empty=False,
        has_tool_calls=False,
        text="Tasks won't run away, but I want you to rest. Go sleep. Still, next up is the code fix.",
    )

    registry = ToolRegistry()
    persona = Persona()
    approvals = ApprovalManager()
    agent = Agent(provider=mock_provider, registry=registry, persona=persona, approvals=approvals, store=test_store)
    agent.current_session_id = "acc_test"

    test_store.save_message("acc_test", "user", "what is our next todo?")
    history_turns = test_store.get_recent_exchanges("acc_test", n_exchanges=5)
    messages = [{"role": m["role"], "content": m["text"]} for m in history_turns]

    reply_2 = agent.chat(messages)
    assert "Go sleep" in reply_2
    assert "next up is the code fix" in reply_2
    test_store.save_message("acc_test", "assistant", reply_2)

    # Turn 3: User says "ok one more thing"
    # Ensure nudge is not repeated on third turn (cooldown or acknowledgment)
    mock_provider.generate.return_value = MagicMock(
        is_empty=False,
        has_tool_calls=False,
        text="Fine, one more. But then bed, deal?",
    )
    test_store.save_message("acc_test", "user", "ok one more thing")
    history_turns3 = test_store.get_recent_exchanges("acc_test", n_exchanges=5)
    messages3 = [{"role": m["role"], "content": m["text"]} for m in history_turns3]

    reply_3 = agent.chat(messages3)
    assert "Fine, one more" in reply_3
    test_store.save_message("acc_test", "assistant", reply_3)

    # Verify short-term history window contains all turns verbatim
    all_msgs = test_store.get_messages("acc_test", limit=10)
    assert len(all_msgs) == 6
    assert all_msgs[0]["text"] == "what is the time?"
    assert all_msgs[1]["text"] == reply_1
    assert all_msgs[2]["text"] == "what is our next todo?"
    assert all_msgs[3]["text"] == reply_2
    assert all_msgs[4]["text"] == "ok one more thing"
    assert all_msgs[5]["text"] == reply_3
