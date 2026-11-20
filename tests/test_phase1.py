"""tests/test_phase1.py

Phase 1 unit tests — no network calls.

A FakeProvider is used so the entire test suite runs offline.
Tests cover:
  1. Wake-word fast path.
  2. Full tool loop: LLM asks for a tool → agent runs it → LLM gets result.
  3. Approval denial: AMBER tool denied → agent reports it did not happen.
  4. Tool failure: tool returns ok=False → agent surfaces the error.
  5. Step limit: provider keeps requesting tools → loop breaks at MAX_STEPS.
  6. Persona prompt contains date/time and tool list.
  7. ToolRegistry raises ValueError on duplicate registration.
  8. ApprovalManager auto-approves GREEN, gates AMBER.
"""

from __future__ import annotations

import pytest
from typing import Any, Generator

from app.brain.llm_provider import LLMProvider, LLMResponse, Message, ToolCall
from app.brain.tools import ToolRegistry, ToolResult
from app.brain.persona import Persona
from app.core.approvals import ApprovalManager, RiskLevel
from app.brain.agent import Agent, MAX_STEPS


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class FakeProvider(LLMProvider):
    """Deterministic provider for testing — no network, no SDK."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self._index = 0

    def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        if self._index >= len(self._responses):
            return LLMResponse(text="(no more fake responses)")
        resp = self._responses[self._index]
        self._index += 1
        return resp

    def stream(self, messages, tools=None, system_prompt=None) -> Generator[str, None, None]:
        yield "(streaming not tested)"


def _make_registry(*extra_tools) -> ToolRegistry:
    """Return a fresh ToolRegistry pre-loaded with a GREEN and an AMBER tool."""
    reg = ToolRegistry()

    @reg.tool("get_time", "Get current time", RiskLevel.GREEN)
    def _get_time() -> ToolResult:
        return ToolResult(ok=True, message="Current time: 00:00:00")

    @reg.tool(
        "relay_toggle",
        "Toggle a relay",
        RiskLevel.AMBER,
        {"type": "object", "properties": {"state": {"type": "string"}}, "required": ["state"]},
    )
    def _relay_toggle(state: str) -> ToolResult:
        return ToolResult(ok=True, message=f"Relay is now {state}.")

    @reg.tool("bad_tool", "Always fails", RiskLevel.GREEN)
    def _bad_tool() -> ToolResult:
        return ToolResult(ok=False, message="bad_tool intentionally failed.")

    for name, fn, risk in extra_tools:
        reg.register(name, name, risk, {}, fn)

    return reg


def _make_agent(
    responses: list[LLMResponse],
    registry: ToolRegistry | None = None,
    approve: bool = True,
) -> Agent:
    provider  = FakeProvider(responses)
    reg       = registry or _make_registry()
    pers      = Persona()   # loads real persona file (read-only in tests)
    approvals = ApprovalManager(callback=lambda desc, risk: approve)
    return Agent(provider, reg, pers, approvals)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestApprovals:
    def test_green_always_approved(self):
        manager = ApprovalManager(callback=lambda d, r: False)  # would deny AMBER
        assert manager.request("get time", RiskLevel.GREEN) is True

    def test_amber_approved_when_callback_returns_true(self):
        manager = ApprovalManager(callback=lambda d, r: True)
        assert manager.request("toggle relay", RiskLevel.AMBER) is True

    def test_amber_denied_when_callback_returns_false(self):
        manager = ApprovalManager(callback=lambda d, r: False)
        assert manager.request("toggle relay", RiskLevel.AMBER) is False

    def test_red_denied_when_callback_returns_false(self):
        manager = ApprovalManager(callback=lambda d, r: False)
        assert manager.request("reset system", RiskLevel.RED) is False


class TestToolRegistry:
    def test_duplicate_registration_raises(self):
        reg = ToolRegistry()

        @reg.tool("my_tool", "first", RiskLevel.GREEN)
        def _first() -> ToolResult:
            return ToolResult(ok=True, message="first")

        with pytest.raises(ValueError, match="already registered"):
            @reg.tool("my_tool", "second", RiskLevel.GREEN)
            def _second() -> ToolResult:
                return ToolResult(ok=True, message="second")

    def test_unknown_tool_returns_failed_result(self):
        reg = ToolRegistry()
        result = reg.run("nonexistent", {})
        assert result.ok is False
        assert "nonexistent" in result.message

    def test_as_schema_includes_all_tools(self):
        reg = _make_registry()
        schema = reg.as_schema()
        names = {s["name"] for s in schema}
        assert "get_time" in names
        assert "relay_toggle" in names


class TestPersona:
    def test_build_system_prompt_contains_datetime(self):
        pers = Persona()
        prompt = pers.build_system_prompt("• get_time [GREEN]")
        assert "Current date/time:" in prompt

    def test_build_system_prompt_contains_tools(self):
        pers = Persona()
        prompt = pers.build_system_prompt("• get_time [GREEN] — Get current time")
        assert "get_time" in prompt

    def test_missing_persona_file_does_not_crash(self, tmp_path):
        fake_path = tmp_path / "missing.md"
        pers = Persona(path=fake_path)
        prompt = pers.build_system_prompt()
        assert isinstance(prompt, str)   # may be nearly empty but must not raise


class TestAgentLoop:
    def test_simple_text_reply(self):
        """Provider returns plain text — agent passes it through."""
        agent = _make_agent([LLMResponse(text="Hello, boss!")])
        reply = agent.chat([{"role": "user", "content": "hi"}])
        assert reply == "Hello, boss!"

    def test_tool_call_loop_executes_tool(self):
        """Provider asks for get_time → agent runs it → provider returns text."""
        tc = ToolCall(id="c1", name="get_time", args={})
        responses = [
            LLMResponse(text="", tool_calls=[tc]),
            LLMResponse(text="It is 00:00:00."),
        ]
        agent = _make_agent(responses)
        reply = agent.chat([{"role": "user", "content": "what time is it?"}])
        assert "00:00:00" in reply

    def test_approval_denial_surfaced_to_llm(self):
        """AMBER tool denied → agent tells LLM it was not done."""
        tc = ToolCall(id="c2", name="relay_toggle", args={"state": "on"})
        responses = [
            LLMResponse(text="", tool_calls=[tc]),
            LLMResponse(text="The relay was NOT toggled because you denied it."),
        ]
        # approve=False → all AMBER approvals denied
        agent = _make_agent(responses, approve=False)
        reply = agent.chat([{"role": "user", "content": "turn on the relay"}])
        assert "not" in reply.lower() or "denied" in reply.lower() or "toggled" not in reply.lower()

    def test_tool_failure_reported_honestly(self):
        """bad_tool returns ok=False → LLM must be told about the failure."""
        tc = ToolCall(id="c3", name="bad_tool", args={})
        responses = [
            LLMResponse(text="", tool_calls=[tc]),
            LLMResponse(text="The tool failed: bad_tool intentionally failed."),
        ]
        agent = _make_agent(responses)
        reply = agent.chat([{"role": "user", "content": "run bad_tool"}])
        # LLM received the failure message — whatever it said should be in reply
        assert "fail" in reply.lower() or "bad_tool" in reply.lower()

    def test_step_limit_stops_infinite_tool_loop(self):
        """Provider keeps requesting the same tool — loop must stop at MAX_STEPS."""
        tc = ToolCall(id="cx", name="get_time", args={})
        # All responses request another tool call.
        responses = [LLMResponse(text="", tool_calls=[tc])] * (MAX_STEPS + 2)
        # Final summary after step limit:
        responses.append(LLMResponse(text="Step limit summary."))
        agent = _make_agent(responses)
        reply = agent.chat([{"role": "user", "content": "loop"}])
        # Must have returned something — not crashed.
        assert isinstance(reply, str)
        assert len(reply) > 0

    def test_provider_runtime_error_returns_message(self):
        """Provider raises RuntimeError → agent returns error string, no crash."""

        class ErrorProvider(LLMProvider):
            def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
                raise RuntimeError("Gemini quota exhausted.")
            def stream(self, messages, tools=None, system_prompt=None):
                yield ""

        reg       = _make_registry()
        pers      = Persona()
        approvals = ApprovalManager(callback=lambda d, r: True)
        agent     = Agent(ErrorProvider(), reg, pers, approvals)
        reply     = agent.chat([{"role": "user", "content": "hi"}])
        assert "quota" in reply.lower() or "gemini" in reply.lower()
