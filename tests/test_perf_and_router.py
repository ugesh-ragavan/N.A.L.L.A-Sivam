"""tests/test_perf_and_router.py

Tests for:
  1. app.perf.PerfTracker — ring-buffer, TurnRecord, summary table
  2. app.brain.fast_router — all fast-path intents
  3. app.brain.agent — _looks_like_action, _trim_history
  4. GET /api/perf and DELETE /api/perf endpoints
  5. config — GEMINI_CHAT_MODEL / GEMINI_COMMAND_MODEL exist
"""

from __future__ import annotations

import time
import pytest
from fastapi.testclient import TestClient

from app.perf import PerfTracker, TurnRecord
from app.brain.fast_router import route
from app.brain.agent import _looks_like_action, _trim_history
from app import config
from app.web.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. PerfTracker
# ---------------------------------------------------------------------------

class TestPerfTracker:
    def test_new_turn_increments_counter(self):
        pt = PerfTracker()
        r1 = pt.new_turn("hello")
        r2 = pt.new_turn("world")
        assert r1.turn_id == 1
        assert r2.turn_id == 2

    def test_records_stored_as_dicts(self):
        pt = PerfTracker()
        rec = pt.new_turn("test utterance")
        rec.stt_ms = 300.0
        rec.total_ms = 1500.0
        recs = pt.records()
        assert len(recs) == 1
        assert recs[0]["stt_ms"] == 300.0
        assert recs[0]["user_text"] == "test utterance"
        assert "ts_human" in recs[0]

    def test_maxlen_respected(self):
        pt = PerfTracker(maxlen=3)
        for i in range(5):
            pt.new_turn(f"turn {i}")
        assert len(pt.records()) == 3

    def test_summary_table_has_header(self):
        pt = PerfTracker()
        pt.new_turn("x").total_ms = 2000.0
        table = pt.summary_table()
        assert "STT" in table
        assert "Total" in table
        assert "AVG" in table

    def test_summary_empty(self):
        pt = PerfTracker()
        assert "No turns" in pt.summary_table()


# ---------------------------------------------------------------------------
# 2. Fast Router
# ---------------------------------------------------------------------------

class TestFastRouter:
    def test_greeting_nalla(self):
        assert route("Nalla") == "Yes boss, sollunga!"
        assert route("Nalla!") == "Yes boss, sollunga!"
        assert route("hey nalla") == "Yes boss, sollunga!"

    def test_greeting_hi(self):
        assert route("hi nalla") == "Yes boss, sollunga!"

    def test_time_query(self):
        reply = route("what time is it")
        assert reply is not None
        assert "boss" in reply.lower()

    def test_time_current(self):
        reply = route("current time")
        assert reply is not None

    def test_date_query(self):
        reply = route("what's the date")
        assert reply is not None
        assert "boss" in reply.lower()

    def test_date_today(self):
        reply = route("what day is today")
        assert reply is not None

    def test_stop(self):
        reply = route("stop")
        assert reply is not None
        assert "boss" in reply.lower()

    def test_mute(self):
        reply = route("mute mic")
        assert reply is not None

    def test_unmute(self):
        reply = route("unmute mic")
        assert reply is not None

    def test_no_match_returns_none(self):
        assert route("tell me a story about dragons") is None
        assert route("what is MQTT") is None
        assert route("explain quantum computing") is None

    def test_empty_returns_none(self):
        assert route("") is None
        assert route("   ") is None


# ---------------------------------------------------------------------------
# 3. Agent helpers
# ---------------------------------------------------------------------------

class TestAgentHelpers:
    def test_action_detection_relay(self):
        assert _looks_like_action("turn on relay 1") is True
        assert _looks_like_action("toggle the switch") is True
        assert _looks_like_action("set reminder for 5pm") is True

    def test_action_detection_negative(self):
        assert _looks_like_action("what time is it") is False
        assert _looks_like_action("tell me a joke") is False
        assert _looks_like_action("how are you doing") is False

    def test_trim_history_cap(self):
        history = []
        for i in range(20):
            history.append({"role": "user", "content": f"msg {i}"})
            history.append({"role": "assistant", "content": f"reply {i}"})
        trimmed = _trim_history(history)
        # Should have at most 16 messages (8 pairs)
        assert len(trimmed) <= 16

    def test_trim_history_starts_with_user(self):
        # Build a long history that exceeds cap; trimmed version should start with user
        history = []
        for i in range(12):
            history.append({"role": "user", "content": f"msg {i}"})
            history.append({"role": "assistant", "content": f"reply {i}"})
        trimmed = _trim_history(history)
        assert trimmed[0]["role"] == "user"

    def test_trim_history_short_passthrough(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        trimmed = _trim_history(history)
        assert len(trimmed) == 2


# ---------------------------------------------------------------------------
# 4. /api/perf endpoints
# ---------------------------------------------------------------------------

class TestPerfAPI:
    def test_get_perf_returns_structure(self):
        res = client.get("/api/perf")
        assert res.status_code == 200
        data = res.json()
        assert "records" in data
        assert "summary" in data
        assert isinstance(data["records"], list)

    def test_delete_perf_clears_records(self):
        # Add a dummy record
        from app.perf import perf
        rec = perf.new_turn("test")
        rec.total_ms = 1000.0

        del_res = client.delete("/api/perf")
        assert del_res.status_code == 200

        get_res = client.get("/api/perf")
        data = get_res.json()
        assert data["records"] == []


# ---------------------------------------------------------------------------
# 5. Config model slots
# ---------------------------------------------------------------------------

class TestConfigModelSlots:
    def test_chat_model_exists(self):
        assert hasattr(config, "GEMINI_CHAT_MODEL")
        assert isinstance(config.GEMINI_CHAT_MODEL, str)
        assert len(config.GEMINI_CHAT_MODEL) > 0

    def test_command_model_exists(self):
        assert hasattr(config, "GEMINI_COMMAND_MODEL")
        assert isinstance(config.GEMINI_COMMAND_MODEL, str)
        assert len(config.GEMINI_COMMAND_MODEL) > 0

    def test_settings_api_includes_model_slots(self):
        res = client.get("/api/settings")
        assert res.status_code == 200
        data = res.json()
        assert "chat_model" in data
        assert "command_model" in data
