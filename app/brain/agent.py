"""app/brain/agent.py

The NALLA SIVAM agent loop.

Orchestrates: LLMProvider → ApprovalManager → ToolRegistry → LLMProvider
in a tight loop (up to MAX_STEPS iterations).

Honesty rules (enforced in code, not just in the persona):
  • A tool result with ok=False is ALWAYS surfaced verbatim to the LLM.
  • The loop NEVER substitutes a success message for a failed result.
  • After MAX_STEPS tool rounds the loop breaks and informs the LLM.
  • If the provider raises a RuntimeError the agent returns the error
    message to the caller (never crashes the whole app).
"""

from __future__ import annotations

import logging
from typing import Any

from app.brain.llm_provider import LLMProvider, LLMResponse, Message, ToolCall
from app.brain.tools import ToolRegistry, ToolResult
from app.brain.persona import Persona
from app.core.approvals import ApprovalManager, RiskLevel

logger = logging.getLogger("nalla.agent")

MAX_STEPS = 5   # Maximum tool-call rounds per user turn.


class Agent:
    """Runs the Nalla reasoning and tool-use loop.

    Parameters
    ----------
    provider:  An LLMProvider implementation (Gemini, Fake, etc.).
    registry:  The ToolRegistry that holds all callable tools.
    persona:   Persona instance that builds the system prompt.
    approvals: ApprovalManager that gates AMBER / RED tool calls.
    """

    def __init__(
        self,
        provider:  LLMProvider,
        registry:  ToolRegistry,
        persona:   Persona,
        approvals: ApprovalManager,
    ) -> None:
        self._provider  = provider
        self._registry  = registry
        self._persona   = persona
        self._approvals = approvals

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, messages: list[Message]) -> str:
        """Run one user turn through the full agent loop.

        Parameters
        ----------
        messages: Conversation history (the caller manages the list).
                  The last entry must be the current user message.

        Returns
        -------
        The agent's final text reply (never empty — an error message
        is returned instead of raising an exception to the UI).
        """
        system_prompt = self._persona.build_system_prompt(
            tool_summary=self._registry.summary()
        )
        tool_schemas = self._registry.as_schema()

        # Working copy of the messages — we append tool results here
        # without mutating the caller's list.
        loop_messages = list(messages)

        for step in range(1, MAX_STEPS + 1):
            logger.debug("Agent loop step %d/%d", step, MAX_STEPS)

            try:
                response: LLMResponse = self._provider.generate(
                    messages=loop_messages,
                    tools=tool_schemas if tool_schemas else None,
                    system_prompt=system_prompt,
                )
            except RuntimeError as exc:
                logger.error("Provider error: %s", exc)
                return str(exc)

            if response.is_empty:
                logger.warning("LLM returned an empty response on step %d", step)
                return "(I didn't get a response. Please try again.)"

            # No tool calls → return the text reply.
            if not response.has_tool_calls:
                return response.text.strip() or "(No text in response.)"

            # Handle each tool call in this step.
            tool_result_messages = self._handle_tool_calls(response.tool_calls)
            loop_messages.append({
                "role":    "assistant",
                "content": response.text,
                # Carry tool_calls for providers that need them in history
                "_tool_calls": [
                    {"id": tc.id, "name": tc.name, "args": tc.args}
                    for tc in response.tool_calls
                ],
            })
            loop_messages.extend(tool_result_messages)

        # Hit the step limit — tell the LLM and get a final answer.
        logger.warning("Agent loop hit MAX_STEPS (%d) — requesting summary.", MAX_STEPS)
        loop_messages.append({
            "role":    "user",
            "content": (
                "[System: you have reached the tool-use step limit. "
                "Summarise what you have done so far and stop calling tools.]"
            ),
        })
        try:
            final = self._provider.generate(
                messages=loop_messages,
                tools=None,         # no more tool calls
                system_prompt=system_prompt,
            )
            return final.text.strip() or "(Step limit reached — no final summary.)"
        except RuntimeError as exc:
            return str(exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_tool_calls(self, tool_calls: list[ToolCall]) -> list[Message]:
        """Process a batch of tool calls; return tool-result messages.

        For each call:
          1. Look up the risk level.
          2. Ask for approval if AMBER or RED.
          3. Run the tool (or return a denial result).
          4. Wrap the ToolResult as a tool-result Message.

        HONESTY RULE: tool results with ok=False are wrapped as-is.
        The model is told clearly that the action did not succeed.
        """
        result_messages: list[Message] = []

        for tc in tool_calls:
            entry = self._registry.get(tc.name)
            if entry is None:
                content = f"Tool '{tc.name}' is not registered — I cannot do that."
                logger.error("Unknown tool called by LLM: %s", tc.name)
                result_messages.append(_tool_message(tc.id, tc.name, content, ok=False))
                continue

            # Approval gate
            description = f"{tc.name}({_fmt_args(tc.args)})"
            approved = self._approvals.request(description, entry.risk)

            if not approved:
                # Honesty rule: tell the LLM the action was denied.
                content = (
                    f"Action DENIED by user: '{tc.name}' was not executed. "
                    "Do not claim it succeeded."
                )
                logger.info("Tool denied by user: %s", tc.name)
                result_messages.append(_tool_message(tc.id, tc.name, content, ok=False))
                continue

            # Execute
            result: ToolResult = self._registry.run(tc.name, tc.args)

            # Honesty rule: surface failures verbatim.
            content = result.message if result.ok else f"TOOL FAILED: {result.message}"
            result_messages.append(_tool_message(tc.id, tc.name, content, ok=result.ok))

        return result_messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_message(call_id: str, name: str, content: str, *, ok: bool) -> Message:
    """Build a tool-result message in our generic format."""
    return {
        "role":         "tool",
        "tool_call_id": call_id,
        "name":         name,
        "content":      content,
        "ok":           ok,
    }


def _fmt_args(args: dict[str, Any]) -> str:
    """Format tool args for human-readable approval prompts."""
    if not args:
        return ""
    return ", ".join(f"{k}={v!r}" for k, v in args.items())
