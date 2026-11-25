"""tests/test_phase3.py

Phase 3 unit tests — local memory, FTS5 search, reminders, tools, agent injection, and web API.

All tests run completely offline and use isolated temporary databases.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.brain.llm_provider import LLMProvider, LLMResponse, ToolCall
from app.brain.persona import Persona
from app.brain.tools import ToolRegistry, ToolResult
from app.core.approvals import ApprovalManager, RiskLevel
from app.brain.agent import Agent
from app.memory.db import init_db, get_connection
from app.memory.store import MemoryStore, is_sensitive
from app.memory.scheduler import reminders_loop
from app.web.server import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Return a path to a fresh SQLite database in a temp directory."""
    db_file = tmp_path / "test_nalla.db"
    init_db(db_file)
    return db_file


@pytest.fixture
def store(temp_db: Path) -> MemoryStore:
    """Return a MemoryStore bound to an isolated temporary database."""
    return MemoryStore(temp_db)


# ---------------------------------------------------------------------------
# 1. MemoryStore Core & Sensitive Data Tests
# ---------------------------------------------------------------------------

class TestMemoryStore:
    def test_save_and_get_memory(self, store: MemoryStore):
        mid = store.save_memory("Ugesh drinks green tea in the morning", category="preference", importance=4)
        mem = store.get_memory(mid)
        assert mem is not None
        assert mem["id"] == mid
        assert mem["text"] == "Ugesh drinks green tea in the morning"
        assert mem["category"] == "preference"
        assert mem["importance"] == 4
        assert mem["source"] == "user_said"
        assert mem["last_used_at"] is None

    def test_reject_empty_text(self, store: MemoryStore):
        with pytest.raises(ValueError, match="cannot be empty"):
            store.save_memory("   ", category="fact")

    def test_reject_invalid_category(self, store: MemoryStore):
        with pytest.raises(ValueError, match="Invalid category"):
            store.save_memory("Some text", category="random_category")

    def test_sensitive_data_detection(self):
        # Credit cards
        assert is_sensitive("My card number is 4111 2222 3333 4444")
        assert is_sensitive("card: 4111-2222-3333-4444")
        # SSN / Aadhaar
        assert is_sensitive("SSN: 123-45-6789")
        assert is_sensitive("1234-5678-9012")
        # Passwords / Secrets / API keys
        assert is_sensitive("my password is SuperSecret123")
        assert is_sensitive("api_key: AIzaSyAe24-testkey")
        assert is_sensitive("secret = my_token_xyz")
        # Harmless text
        assert not is_sensitive("Ugesh prefers concise answers")
        assert not is_sensitive("Project Nalla Sivam deadline is next month")

    def test_reject_sensitive_memory(self, store: MemoryStore):
        with pytest.raises(ValueError, match="Sensitive information"):
            store.save_memory("My password is supersecret123", category="fact")

    def test_update_memory(self, store: MemoryStore):
        mid = store.save_memory("Initial note", category="fact", importance=2)
        ok = store.update_memory(mid, text="Updated note text", importance=5)
        assert ok is True

        updated = store.get_memory(mid)
        assert updated["text"] == "Updated note text"
        assert updated["importance"] == 5

    def test_hard_delete_memory(self, store: MemoryStore):
        mid = store.save_memory("Temporary secret thought", category="fact")
        assert store.get_memory(mid) is not None

        deleted = store.delete_memory(mid)
        assert deleted is True

        # Truly gone from database
        assert store.get_memory(mid) is None

        # Truly gone from FTS index
        results = store.search_memories("Temporary secret", limit=5)
        assert all(r["id"] != mid for r in results)

    def test_restart_persistence(self, temp_db: Path):
        """Simulate app restart by creating a new MemoryStore on the same DB file."""
        store1 = MemoryStore(temp_db)
        mid = store1.save_memory("Remember to water the plants", category="task", importance=3)

        # "Restart": new store instance
        store2 = MemoryStore(temp_db)
        mem = store2.get_memory(mid)
        assert mem is not None
        assert mem["text"] == "Remember to water the plants"
        assert mem["category"] == "task"


# ---------------------------------------------------------------------------
# 2. FTS5 Search & Ranking Tests
# ---------------------------------------------------------------------------

class TestFtsSearch:
    def test_fts_search_ranking_and_last_used(self, store: MemoryStore):
        store.save_memory("Ugesh works on Python and FastAPI projects", category="project", importance=4)
        store.save_memory("FastAPI is an asynchronous web framework", category="fact", importance=2)
        store.save_memory("Ugesh likes strong black coffee", category="preference", importance=5)

        # Search for "FastAPI"
        results = store.search_memories("FastAPI", limit=5)
        assert len(results) >= 2
        texts = [r["text"] for r in results]
        assert any("Python and FastAPI" in t for t in texts)
        assert any("asynchronous web framework" in t for t in texts)
        assert all("coffee" not in t for t in texts)

        # Verify last_used_at was updated on matched items
        for r in results:
            rechecked = store.get_memory(r["id"])
            assert rechecked["last_used_at"] is not None

    def test_fts_prefix_and_punctuation_resilience(self, store: MemoryStore):
        store.save_memory("Raspberry Pi 5 single board computer", category="project", importance=4)
        # Query with prefix and punctuation
        results = store.search_memories("Raspberry*", limit=5)
        assert len(results) >= 1
        assert "Raspberry" in results[0]["text"]


# ---------------------------------------------------------------------------
# 3. Conversation Messages Persistence
# ---------------------------------------------------------------------------

class TestMessageHistory:
    def test_save_and_retrieve_messages(self, store: MemoryStore):
        sid = "test_session"
        store.save_message(sid, "user", "Hello Nalla")
        store.save_message(sid, "assistant", "Yes boss, sollunga!")
        store.save_message(sid, "user", "What is my name?")

        history = store.get_messages(sid, limit=10)
        assert len(history) == 3
        assert history[0]["role"] == "user"
        assert history[0]["text"] == "Hello Nalla"
        assert history[1]["role"] == "assistant"
        assert history[2]["text"] == "What is my name?"


# ---------------------------------------------------------------------------
# 4. Reminders Tests
# ---------------------------------------------------------------------------

class TestReminders:
    def test_save_and_get_due_reminders(self, store: MemoryStore):
        past_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        future_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

        rid_due = store.save_reminder("Overdue task: Check server", due_at=past_iso)
        rid_future = store.save_reminder("Future task: Lunch", due_at=future_iso)

        due = store.get_due_reminders()
        due_ids = [r["id"] for r in due]
        assert rid_due in due_ids
        assert rid_future not in due_ids

        # Mark done
        ok = store.mark_reminder_done(rid_due)
        assert ok is True

        # Now none should be due
        remaining_due = store.get_due_reminders()
        assert rid_due not in [r["id"] for r in remaining_due]


# ---------------------------------------------------------------------------
# 5. Agent Memory Injection Tests
# ---------------------------------------------------------------------------

class FakeProvider(LLMProvider):
    def __init__(self, response_text: str = "Test response") -> None:
        self.response_text = response_text
        self.last_system_prompt: str = ""

    def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        self.last_system_prompt = system_prompt or ""
        return LLMResponse(text=self.response_text)

    def stream(self, messages, tools=None, system_prompt=None):
        yield self.response_text


class TestAgentMemoryInjection:
    def test_agent_injects_memories_when_enabled(self, store: MemoryStore):
        store.save_memory("Ugesh prefers short answers without preamble", category="preference", importance=5)

        provider = FakeProvider()
        reg = ToolRegistry()
        persona = Persona()
        approvals = ApprovalManager(callback=lambda d, r: True)

        agent = Agent(provider, reg, persona, approvals, store=store, memory_enabled=True)
        reply = agent.chat([{"role": "user", "content": "What answers do I prefer?"}])

        assert reply == "Test response"
        # Verify system prompt has injected memory section
        assert "## Known about the user (may be outdated)" in provider.last_system_prompt
        assert "Ugesh prefers short answers without preamble" in provider.last_system_prompt
        assert "Rule: if a memory or fact is not in this list, say you don't remember it" in provider.last_system_prompt

    def test_agent_skips_memory_injection_when_disabled(self, store: MemoryStore):
        store.save_memory("Ugesh prefers short answers", category="preference")

        provider = FakeProvider()
        reg = ToolRegistry()
        persona = Persona()
        approvals = ApprovalManager(callback=lambda d, r: True)

        agent = Agent(provider, reg, persona, approvals, store=store, memory_enabled=False)
        agent.chat([{"role": "user", "content": "What answers do I prefer?"}])

        assert "## Known about the user" not in provider.last_system_prompt


# ---------------------------------------------------------------------------
# 6. Web Dashboard Endpoints Tests
# ---------------------------------------------------------------------------

class TestWebDashboard:
    def test_get_dashboard_html(self):
        client = TestClient(app)
        res = client.get("/")
        assert res.status_code == 200
        assert "NALLA SIVAM" in res.text
        assert "Memory" in res.text

    def test_memory_crud_api(self):
        client = TestClient(app)

        # 1. Add memory
        post_res = client.post("/api/memories", json={
            "text": "Automated web test memory item",
            "category": "project",
            "importance": 4
        })
        assert post_res.status_code == 201
        data = post_res.json()
        mid = data["id"]

        # 2. Search / List
        list_res = client.get(f"/api/memories?q=Automated")
        assert list_res.status_code == 200
        items = list_res.json()
        assert any(m["id"] == mid for m in items)

        # 3. Update
        put_res = client.put(f"/api/memories/{mid}", json={
            "text": "Updated web test memory item",
            "importance": 5
        })
        assert put_res.status_code == 200

        # 4. Export JSON
        export_res = client.get("/api/memories/export")
        assert export_res.status_code == 200
        assert "Updated web test memory item" in export_res.text

        # 5. Delete
        del_res = client.delete(f"/api/memories/{mid}")
        assert del_res.status_code == 200

    def test_settings_toggle_api(self):
        client = TestClient(app)
        # Turn off
        res1 = client.post("/api/settings", json={"memory_enabled": False})
        assert res1.status_code == 200
        assert res1.json()["memory_enabled"] is False

        # Turn on
        res2 = client.post("/api/settings", json={"memory_enabled": True})
        assert res2.status_code == 200
        assert res2.json()["memory_enabled"] is True


# ---------------------------------------------------------------------------
# 7. Memory Tools Registration & Execution Tests
# ---------------------------------------------------------------------------

class TestMemoryTools:
    def test_registered_memory_tools_exist(self):
        from app.brain.tools import registry
        tool_names = [s["name"] for s in registry.as_schema()]
        assert "remember" in tool_names
        assert "recall" in tool_names
        assert "forget" in tool_names
        assert "set_reminder" in tool_names
        assert "list_reminders" in tool_names

    def test_forget_tool_risk_level_is_amber(self):
        from app.brain.tools import registry
        entry = registry._tools["forget"]
        assert entry.risk == RiskLevel.AMBER

    def test_remember_tool_stores_memory(self):
        from app.brain.tools import registry
        res = registry.run("remember", {"text": "Ugesh's favourite tea is masala chai", "category": "preference"})
        assert res.ok is True
        assert "masala chai" in res.message
        mid = res.data["id"]

        # Now recall it
        res_recall = registry.run("recall", {"query": "masala chai"})
        assert res_recall.ok is True
        assert "masala chai" in res_recall.message

        # Forget it
        res_forget = registry.run("forget", {"memory_id": mid})
        assert res_forget.ok is True

    def test_remember_tool_rejects_sensitive(self):
        from app.brain.tools import registry
        res = registry.run("remember", {"text": "password: secretPassword99", "category": "fact"})
        assert res.ok is False
        assert "Sensitive information" in res.message

