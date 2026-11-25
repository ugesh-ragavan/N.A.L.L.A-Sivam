from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest

from app import config
from app.core.presence import PresenceTracker
from app.core.proactive import ProactiveEvent, ProactivePolicyEngine
from app.core.scheduler import PersistentScheduler, _compute_next_daily_run
from app.core.briefings import (
    generate_morning_briefing,
    generate_welcome_back,
    generate_smart_check_in,
    generate_evening_wrap_up,
    get_local_weather,
)
from app.memory.db import init_db, get_connection


@pytest.fixture
def temp_db(tmp_path):
    """Create an isolated test database with all migrations applied."""
    db_file = tmp_path / "presence_test.db"
    init_db(db_file)
    return db_file


def test_acceptance_three_events_delivery_quiet_hours_and_expiry(temp_db):
    """ACCEPTANCE TEST (Prompt 1A):

    Schedule three events with a fake clock and show the policy:
      1. Delivering one (normal midday)
      2. Skipping one for quiet hours (02:30 AM briefing)
      3. Dropping one as expired (past expiration window)
    """
    engine = ProactivePolicyEngine(db_conn_factory=lambda: get_connection(temp_db))
    # Midday 14:00 (Outside quiet hours)
    sim_midday = datetime(2026, 11, 24, 14, 0, 0, tzinfo=timezone.utc)

    # 1. Normal event at midday -> ALLOWED
    event_normal = ProactiveEvent(
        category="check_in",
        text_or_generator="Quick water break, boss.",
    )
    d1 = engine.evaluate_event(event_normal, simulated_now=sim_midday)
    assert d1.allowed is True
    assert d1.action == "spoke"
    assert d1.reason == "policy_satisfied"

    # 2. Quiet hours event (02:30 AM) -> SKIPPED (quiet_hours)
    sim_night = datetime(2026, 11, 24, 2, 30, 0, tzinfo=timezone.utc)
    event_night = ProactiveEvent(
        category="briefing",
        text_or_generator="Good morning!",
    )
    d2 = engine.evaluate_event(event_night, simulated_now=sim_night)
    assert d2.allowed is False
    assert d2.action == "skipped"
    assert d2.reason == "quiet_hours"

    # 3. Expired event -> EXPIRED
    event_expired = ProactiveEvent(
        category="briefing",
        text_or_generator="Morning briefing from earlier",
        expires=sim_midday - timedelta(minutes=15),  # expired 15 mins ago
    )
    d3 = engine.evaluate_event(event_expired, simulated_now=sim_midday)
    assert d3.allowed is False
    assert d3.action == "expired"
    assert d3.reason == "event_expired"


def test_quiet_hours_reminders_break_through(temp_db):
    """User-set reminders (Priority 1) MUST break through quiet hours."""
    engine = ProactivePolicyEngine(db_conn_factory=lambda: get_connection(temp_db))
    sim_night = datetime(2026, 11, 24, 3, 0, 0, tzinfo=timezone.utc)

    reminder_event = ProactiveEvent(
        category="reminder",
        priority=1,
        text_or_generator="Take medication now",
    )
    d = engine.evaluate_event(reminder_event, simulated_now=sim_night)
    assert d.allowed is True
    assert d.action == "spoke"


def test_hourly_cap_enforcement(temp_db, monkeypatch):
    """Max 2 proactive utterances per hour limit enforced."""
    monkeypatch.setattr(config, "PROACTIVE_MAX_PER_HOUR", 2)
    engine = ProactivePolicyEngine(db_conn_factory=lambda: get_connection(temp_db))
    sim_time = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)

    # First utterance
    e1 = ProactiveEvent(category="check_in", text_or_generator="Drink water 1")
    assert engine.evaluate_event(e1, simulated_now=sim_time).allowed is True

    # Second utterance 10 minutes later (different category so no per-category cooldown block)
    e2 = ProactiveEvent(category="welcome_back", text_or_generator="Welcome back 2")
    assert engine.evaluate_event(e2, simulated_now=sim_time + timedelta(minutes=10)).allowed is True

    # Third utterance 20 minutes later -> EXCEEDS HOURLY CAP OF 2
    e3 = ProactiveEvent(category="wrap_up", text_or_generator="Wrap up 3")
    d3 = engine.evaluate_event(e3, simulated_now=sim_time + timedelta(minutes=20))
    assert d3.allowed is False
    assert d3.reason == "hourly_cap_exceeded"


def test_mid_conversation_idle_gating(temp_db):
    """Proactive events must skip if an active conversation turn occurred < 60s ago."""
    tracker = PresenceTracker()
    sim_time = datetime(2026, 11, 24, 15, 0, 0, tzinfo=timezone.utc)
    tracker.record_interaction(simulated_now=sim_time)

    # 15 seconds after user spoke -> not idle
    assert tracker.is_conversation_idle(60.0, simulated_now=sim_time + timedelta(seconds=15)) is False

    # 65 seconds after user spoke -> idle
    assert tracker.is_conversation_idle(60.0, simulated_now=sim_time + timedelta(seconds=65)) is True


def test_session_snooze_not_now(temp_db):
    """Saying 'not now' or 'stop' snoozes proactive speech for the session."""
    from app.core import presence as p_mod
    p_mod.presence.snooze_session()

    engine = ProactivePolicyEngine(db_conn_factory=lambda: get_connection(temp_db))
    sim_time = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)

    e = ProactiveEvent(category="check_in", text_or_generator="Stretch break")
    d = engine.evaluate_event(e, simulated_now=sim_time)
    assert d.allowed is False
    assert d.reason == "session_snoozed"

    # Reset snooze state
    p_mod.presence._is_snoozed_for_session = False


def test_persistent_scheduler_crud_and_due(temp_db):
    """Scheduler persists jobs to SQLite and detects due jobs."""
    sched = PersistentScheduler(db_conn_factory=lambda: get_connection(temp_db))
    sim_time = datetime(2026, 11, 24, 8, 0, 0, tzinfo=timezone.utc)

    jid = sched.add_job(
        name="Test Daily Briefing",
        job_type="daily",
        schedule_expr="08:30",
        category="briefing",
        simulated_now=sim_time,
    )
    jobs = sched.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == jid
    assert "08:30" in jobs[0]["next_run_at"]

    # At 08:00 -> not due yet
    due_before = sched.get_due_jobs(simulated_now=sim_time)
    assert len(due_before) == 0

    # At 08:35 -> due!
    due_after = sched.get_due_jobs(simulated_now=sim_time + timedelta(minutes=35))
    assert len(due_after) == 1
    assert due_after[0]["id"] == jid

    # Mark executed -> advances next_run_at to tomorrow
    sched.mark_job_executed(jid, simulated_now=sim_time + timedelta(minutes=35))
    jobs_after = sched.list_jobs()
    assert "2026-11-25" in jobs_after[0]["next_run_at"]


def test_briefing_generators_sound_like_nalla():
    """Verify Prompt 1B content generators sound in-character, stay concise, and format screen lines."""
    sim_time = datetime(2026, 11, 24, 8, 30, 0, tzinfo=timezone.utc)

    # 1. Morning briefing
    mb = generate_morning_briefing(simulated_now=sim_time)
    assert ("boss" in mb.lower() or "vanakkam" in mb.lower())
    assert "---" in mb
    assert "### Morning Briefing" in mb

    # 2. Welcome back
    wb = generate_welcome_back(simulated_now=sim_time)
    assert len(wb.split()) < 35  # stays concise
    assert ("back" in wb.lower() or "machan" in wb.lower() or "boss" in wb.lower())

    # 3. Smart check-in
    ci = generate_smart_check_in(simulated_now=sim_time)
    assert len(ci.split()) < 30
    assert ("water" in ci.lower() or "stretch" in ci.lower() or "task" in ci.lower())

    # 4. Evening wrap-up
    wu = generate_evening_wrap_up(simulated_now=datetime(2026, 11, 24, 21, 30, 0, tzinfo=timezone.utc))
    assert "---" in wu
    assert "### Evening Wrap-Up" in wu


def test_api_presence_endpoints(temp_db):
    """Test REST API endpoints for presence status, settings update, test generation, and snooze."""
    from fastapi.testclient import TestClient
    from app.web.server import app

    client = TestClient(app)

    # 1. GET /api/presence
    res_get = client.get("/api/presence")
    assert res_get.status_code == 200
    data = res_get.json()
    assert "status" in data
    assert "settings" in data
    assert "jobs" in data
    assert "recent_decisions" in data

    # 2. POST /api/presence
    res_post = client.post("/api/presence", json={"max_per_hour": 3, "dnd": False})
    assert res_post.status_code == 200
    assert res_post.json()["settings"]["max_per_hour"] == 3

    # 3. POST /api/presence/test for all 4 categories
    for cat in ["briefing", "welcome_back", "check_in", "wrap_up"]:
        res_test = client.post("/api/presence/test", json={"category": cat})
        assert res_test.status_code == 200
        t_data = res_test.json()
        assert t_data["ok"] is True
        assert len(t_data["spoken"]) > 0
        assert t_data["category"] == cat

    # 4. POST /api/presence/snooze
    res_snooze = client.post("/api/presence/snooze", json={"category": "check_in", "minutes": 45})
    assert res_snooze.status_code == 200
    assert res_snooze.json()["ok"] is True

