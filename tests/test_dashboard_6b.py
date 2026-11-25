"""tests/test_dashboard_6b.py

Unit and integration tests for Prompt 6B additions:
  1. Home: Personality greeting & mode switching.
  2. Chat: SSE token streaming (/api/chat/stream), expressive tag suppression, tool cards with approval status.
  3. Approvals: Web approval workflow (AMBER risk modal, Approve / Deny resolution, pending approvals registry).
  4. Test AMBER endpoint (/api/test/amber).
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.web.server import app, pending_approvals, web_approval_callback
from app.brain.persona import strip_expressive_tags
from app.brain.agent import Agent
from app.core.approvals import RiskLevel, ApprovalManager
from app.brain.tools import ToolRegistry, ToolResult
from app.brain.llm_provider import LLMResponse, ToolCall


@pytest.fixture
def client():
    return TestClient(app)


def test_strip_expressive_tags():
    raw = "<chuckle> Yes boss! <sigh> Here is your answer. <short pause> All clear."
    clean = strip_expressive_tags(raw)
    assert "<chuckle>" not in clean
    assert "<sigh>" not in clean
    assert "<short pause>" not in clean
    assert "Yes boss!" in clean
    assert "Here is your answer." in clean


def test_personality_greeting_and_mode_switch(client):
    res = client.get("/api/personality")
    assert res.status_code == 200
    data = res.json()
    assert "mode" in data
    assert "modes" in data
    assert "Friend" in data["modes"]
    assert "Jarvis" in data["modes"]

    # Switch mode to Jarvis
    res_update = client.post("/api/personality", json={
        "mode": "Jarvis",
        "sarcasm": 3,
        "warmth": 6,
        "tamil_mix": 10,
        "verbosity": "short",
    })
    assert res_update.status_code == 200
    assert res_update.json()["mode"] == "Jarvis"

    # Revert back to Friend
    res_revert = client.post("/api/personality", json={
        "mode": "Friend",
        "sarcasm": 7,
        "warmth": 8,
        "tamil_mix": 40,
        "verbosity": "very short",
    })
    assert res_revert.status_code == 200
    assert res_revert.json()["mode"] == "Friend"


def test_chat_stream_fast_path(client):
    res = client.post("/api/chat/stream", json={"message": "What time is it?", "session_id": "test_stream"})
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]

    content = res.text
    assert "data:" in content
    assert '"fast_path": true' in content
    assert '"done"' in content


def test_chat_stream_generative(client):
    mock_chunks = ["Vanakkam! ", "I am ", "Nalla Sivam."]
    with patch("app.brain.agent.Agent.chat_stream", return_value=iter(mock_chunks)):
        res = client.post("/api/chat/stream", json={"message": "Tell me your name", "session_id": "test_stream_gen"})
        assert res.status_code == 200
        content = res.text
        assert "data:" in content
        assert "Vanakkam!" in content
        assert "Nalla Sivam" in content
        assert '"done"' in content


def test_approval_respond_not_found(client):
    res = client.post("/api/approvals/non-existent-id/respond", json={"approved": True})
    assert res.status_code == 404


def test_approval_flow_amber_approve(client):
    result_holder = {}

    def run_amber_request():
        res = client.post("/api/test/amber", json={"description": "Toggle relay 1 (AMBER)"})
        result_holder["response"] = res

    t = threading.Thread(target=run_amber_request, daemon=True)
    t.start()

    # Poll pending approvals until available
    pending_id = None
    for _ in range(50):
        time.sleep(0.05)
        res_list = client.get("/api/approvals/pending")
        if res_list.status_code == 200:
            pending = res_list.json().get("pending", [])
            if pending:
                pending_id = pending[0]["id"]
                break

    assert pending_id is not None, "Pending approval was not created"

    # User approves via modal
    res_resp = client.post(f"/api/approvals/{pending_id}/respond", json={"approved": True})
    assert res_resp.status_code == 200
    assert res_resp.json()["approved"] is True

    t.join(timeout=3.0)
    assert not t.is_alive()
    final_res = result_holder.get("response")
    assert final_res is not None
    assert final_res.status_code == 200
    data = final_res.json()
    assert data["ok"] is True
    assert data["approved"] is True
    assert "fake_relay_toggle" in data["tool"]


def test_approval_flow_amber_deny(client):
    result_holder = {}

    def run_amber_request():
        res = client.post("/api/test/amber", json={"description": "Toggle relay 1 (AMBER)"})
        result_holder["response"] = res

    t = threading.Thread(target=run_amber_request, daemon=True)
    t.start()

    pending_id = None
    for _ in range(50):
        time.sleep(0.05)
        res_list = client.get("/api/approvals/pending")
        if res_list.status_code == 200:
            pending = res_list.json().get("pending", [])
            if pending:
                pending_id = pending[0]["id"]
                break

    assert pending_id is not None, "Pending approval was not created"

    # User denies via modal
    res_resp = client.post(f"/api/approvals/{pending_id}/respond", json={"approved": False})
    assert res_resp.status_code == 200
    assert res_resp.json()["approved"] is False

    t.join(timeout=3.0)
    assert not t.is_alive()
    final_res = result_holder.get("response")
    assert final_res is not None
    assert final_res.status_code == 200
    data = final_res.json()
    assert data["ok"] is False
    assert data["approved"] is False
    assert "denied" in data["message"].lower()


def test_agent_tool_calls_tracking():
    mock_provider = MagicMock()
    mock_persona = MagicMock()
    mock_persona.build_system_prompt.return_value = "System Prompt"

    reg = ToolRegistry()

    @reg.tool(name="test_tool", description="Test", risk=RiskLevel.AMBER, parameters={})
    def _test_tool():
        return ToolResult(ok=True, message="Test tool executed")

    approvals = ApprovalManager(callback=lambda desc, risk: True)

    agent = Agent(
        provider=mock_provider,
        registry=reg,
        persona=mock_persona,
        approvals=approvals,
        memory_enabled=False,
    )

    # 1. First step returns a tool call
    tc = ToolCall(id="call_1", name="test_tool", args={})
    resp1 = LLMResponse(text="", tool_calls=[tc], raw_content=[])
    # 2. Second step returns final text
    resp2 = LLMResponse(text="Done testing.", tool_calls=[], raw_content=[])
    mock_provider.generate.side_effect = [resp1, resp2]

    reply = agent.chat([{"role": "user", "content": "run test_tool"}])
    assert reply == "Done testing."
    assert len(agent.last_tool_calls) == 1
    assert agent.last_tool_calls[0]["name"] == "test_tool"
    assert agent.last_tool_calls[0]["approved"] is True
    assert agent.last_tool_calls[0]["ok"] is True
    assert agent.last_tool_calls[0]["risk"] == "amber"
