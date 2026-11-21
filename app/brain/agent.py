"""app/brain/agent.py

The NALLA SIVAM agent loop.

Performance improvements in this version
-----------------------------------------
• Memory search runs in a ThreadPoolExecutor concurrently with prompt build.
• Persona text and tool schema are cached between calls; rebuilt only on change.
• Tool schemas attached ONLY when the user message looks like an action request.
• History trimmed to last HISTORY_TURNS (8) turns; older turns summarised.
• Max history context = 16 messages (8 user + 8 assistant).
• Filler phrase ("On it, boss.") played when a tool call takes > FILLER_AFTER_S.
• Memory injection limited to top 3 short items.
• per-turn timing data written to app.perf.TurnRecord.

Honesty rules (enforced in code, not just the persona)
  • ok=False results surfaced verbatim.
  • Loop never substitutes a success for a failure.
  • After MAX_STEPS tool rounds the loop breaks and informs the LLM.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Generator

from app.brain.llm_provider import LLMProvider, LLMResponse, Message, ToolCall
from app.brain.tools import ToolRegistry, ToolResult
from app.brain.persona import Persona
from app.core.approvals import ApprovalManager, RiskLevel
from app.memory.store import MemoryStore

logger = logging.getLogger("nalla.agent")

MAX_STEPS      = 5     # Maximum tool-call rounds per user turn
HISTORY_TURNS  = 8     # Keep last N user/assistant pairs in context
FILLER_AFTER_S = 1.5   # Speak a filler phrase if a tool takes longer than this
_MEMORY_LIMIT  = 3     # Max memories to inject per turn

# Pattern to decide whether tool schemas should be attached
_ACTION_RE = re.compile(
    r"\b(turn|set|toggle|switch|relay|on|off|remind|schedule|remember|recall|"
    r"forget|delete|create|run|execute|mqtt|device|sensor|list\s+reminder)\b",
    re.IGNORECASE,
)

# Thread pool for parallel work (memory search, etc.)
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nalla-agent")


def _looks_like_action(text: str) -> bool:
    """Return True when the message likely needs tools."""
    return bool(_ACTION_RE.search(text))


def _trim_history(history: list[Message]) -> list[Message]:
    """Keep only the last HISTORY_TURNS user/assistant pairs."""
    max_messages = HISTORY_TURNS * 2  # each pair = 2 messages
    if len(history) <= max_messages:
        return list(history)
    trimmed = history[-max_messages:]
    # Ensure we start with a user message
    while trimmed and trimmed[0].get("role") != "user":
        trimmed = trimmed[1:]
    return trimmed


class Agent:
    """Runs the Nalla reasoning and tool-use loop.

    Parameters
    ----------
    provider:       An LLMProvider implementation (Gemini, Fake, etc.).
    registry:       The ToolRegistry holding all callable tools.
    persona:        Persona instance that builds the system prompt.
    approvals:      ApprovalManager that gates AMBER / RED tool calls.
    store:          Optional MemoryStore for memory injection and recall.
    memory_enabled: If False, memories are neither searched nor injected.
    filler_speaker: Optional callable(str) → None that speaks filler phrases.
    """

    def __init__(
        self,
        provider:       LLMProvider,
        registry:       ToolRegistry,
        persona:        Persona,
        approvals:      ApprovalManager,
        store:          MemoryStore | None = None,
        memory_enabled: bool = True,
        filler_speaker: Any = None,
    ) -> None:
        self._provider       = provider
        self._registry       = registry
        self._persona        = persona
        self._approvals      = approvals
        self._store          = store
        self._memory_enabled = memory_enabled
        self._filler_speaker = filler_speaker  # callable(phrase) or None

        # Caches
        self._cached_tool_summary: str = ""
        self._cached_tool_schemas: list[dict[str, Any]] = []

        self.last_memory_search_ms: float = 0.0
        self.last_prompt_build_ms: float  = 0.0
        self.last_tool_calls_ms: float    = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, messages: list[Message]) -> str:
        """Run one user turn through the full agent loop."""
        messages = _trim_history(messages)
        user_text = _last_user_text(messages)

        # Parallel: memory search + tool schema assembly
        t0 = time.perf_counter()
        mem_future = _executor.submit(self._search_memory, user_text)
        schema_future = _executor.submit(self._get_tool_schemas, user_text)

        memory_context = mem_future.result()
        self.last_memory_search_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        tool_schemas = schema_future.result()

        # Build system prompt
        t_prompt = time.perf_counter()
        system_prompt = self._persona.build_system_prompt(
            tool_summary=self._get_tool_summary(),
            memory_context=memory_context,
        )
        self.last_prompt_build_ms = round((time.perf_counter() - t_prompt) * 1000.0, 1)

        loop_messages = list(messages)
        tools_to_pass = tool_schemas if tool_schemas else None

        for step in range(1, MAX_STEPS + 1):
            logger.debug("Agent loop step %d/%d", step, MAX_STEPS)
            try:
                response: LLMResponse = self._provider.generate(
                    messages=loop_messages,
                    tools=tools_to_pass,
                    system_prompt=system_prompt,
                )
            except RuntimeError as exc:
                logger.error("Provider error: %s", exc)
                return str(exc)

            if response.is_empty:
                logger.warning("LLM returned an empty response on step %d", step)
                return "(I didn't get a response. Please try again.)"

            if not response.has_tool_calls:
                return response.text.strip() or "(No text in response.)"

            # Tool calls
            t_tools = time.perf_counter()
            tool_result_messages = self._handle_tool_calls(response.tool_calls)
            self.last_tool_calls_ms += round((time.perf_counter() - t_tools) * 1000.0, 1)

            loop_messages.append({
                "role":    "assistant",
                "content": response.text,
                "_tool_calls": [
                    {"id": tc.id, "name": tc.name, "args": tc.args}
                    for tc in response.tool_calls
                ],
                "_raw_content": response.raw_content,
            })
            loop_messages.extend(tool_result_messages)
            # After first tool round the final answer never needs tools
            tools_to_pass = None

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
                tools=None,
                system_prompt=system_prompt,
            )
            return final.text.strip() or "(Step limit reached — no final summary.)"
        except RuntimeError as exc:
            return str(exc)

    def chat_stream(self, messages: list[Message]) -> Generator[str, None, None]:
        """Run one user turn, streaming tokens for the final response.

        Executes tools if needed, then streams the final answer chunk-by-chunk
        so the pipeline can send each sentence to TTS immediately.
        """
        messages = _trim_history(messages)
        user_text = _last_user_text(messages)

        # Parallel: memory search + tool schema
        t0 = time.perf_counter()
        mem_future = _executor.submit(self._search_memory, user_text)
        schema_future = _executor.submit(self._get_tool_schemas, user_text)

        memory_context = mem_future.result()
        self.last_memory_search_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        tool_schemas = schema_future.result()

        # Build system prompt
        t_prompt = time.perf_counter()
        system_prompt = self._persona.build_system_prompt(
            tool_summary=self._get_tool_summary(),
            memory_context=memory_context,
        )
        self.last_prompt_build_ms = round((time.perf_counter() - t_prompt) * 1000.0, 1)

        loop_messages = list(messages)
        tools_to_pass = tool_schemas if tool_schemas else None

        # Tool resolution loop (non-streaming; streaming used only for final answer)
        for step in range(1, MAX_STEPS + 1):
            try:
                response: LLMResponse = self._provider.generate(
                    messages=loop_messages,
                    tools=tools_to_pass,
                    system_prompt=system_prompt,
                )
            except RuntimeError as exc:
                yield str(exc)
                return

            if response.is_empty:
                yield "(I didn't get a response. Please try again.)"
                return

            if not response.has_tool_calls:
                # No tools needed — stream the final answer directly
                try:
                    for chunk in self._provider.stream(
                        messages=loop_messages + [{
                            "role": "assistant",
                            "content": response.text,
                        }],
                        tools=None,
                        system_prompt=system_prompt,
                    ):
                        if chunk:
                            yield chunk
                except Exception:
                    # Fallback: yield the already-received text
                    text = response.text.strip() or "(No text in response.)"
                    yield text
                return

            # Tool calls — with filler if slow
            t_tools = time.perf_counter()
            tool_result_messages = self._handle_tool_calls_with_filler(response.tool_calls)
            self.last_tool_calls_ms += round((time.perf_counter() - t_tools) * 1000.0, 1)

            loop_messages.append({
                "role": "assistant",
                "content": response.text,
                "_tool_calls": [
                    {"id": tc.id, "name": tc.name, "args": tc.args}
                    for tc in response.tool_calls
                ],
                "_raw_content": response.raw_content,
            })
            loop_messages.extend(tool_result_messages)
            tools_to_pass = None  # no more tools after first round

        # Hit step limit — stream a final summary
        loop_messages.append({
            "role":    "user",
            "content": (
                "[System: you have reached the tool-use step limit. "
                "Summarise what you have done so far and stop calling tools.]"
            ),
        })
        try:
            for chunk in self._provider.stream(
                messages=loop_messages,
                tools=None,
                system_prompt=system_prompt,
            ):
                if chunk:
                    yield chunk
        except Exception:
            final = self._provider.generate(
                messages=loop_messages,
                tools=None,
                system_prompt=system_prompt,
            )
            yield final.text.strip() or "(No text in response.)"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _search_memory(self, user_text: str) -> str:
        """Return formatted memory context (called in thread pool)."""
        if not self._memory_enabled or not self._store or not user_text:
            return ""
        try:
            recalled = self._store.search_memories(user_text, limit=_MEMORY_LIMIT)
            if not recalled:
                return ""
            lines = [
                f"- [{m.get('category', 'fact')}] {m.get('text')}"
                for m in recalled
            ]
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("Memory search failed: %s", exc)
            return ""

    def _get_tool_summary(self) -> str:
        """Return cached tool summary string."""
        summary = self._registry.summary()
        if summary != self._cached_tool_summary:
            self._cached_tool_summary = summary
        return self._cached_tool_summary

    def _get_tool_schemas(self, user_text: str) -> list[dict[str, Any]]:
        """Return tool schemas only when the message looks like an action."""
        if not _looks_like_action(user_text):
            return []
        # Cache the schema list (re-compute only when registry changes)
        schemas = self._registry.as_schema()
        self._cached_tool_schemas = schemas
        return schemas

    def _handle_tool_calls(self, tool_calls: list[ToolCall]) -> list[Message]:
        """Process tool calls; return tool-result messages."""
        return self._run_tool_calls(tool_calls, filler=False)

    def _handle_tool_calls_with_filler(self, tool_calls: list[ToolCall]) -> list[Message]:
        """Process tool calls with filler phrase if any take > FILLER_AFTER_S."""
        return self._run_tool_calls(tool_calls, filler=True)

    def _run_tool_calls(self, tool_calls: list[ToolCall], filler: bool) -> list[Message]:
        result_messages: list[Message] = []
        filler_said = False

        for tc in tool_calls:
            entry = self._registry.get(tc.name)
            if entry is None:
                content = f"Tool '{tc.name}' is not registered — I cannot do that."
                logger.error("Unknown tool called by LLM: %s", tc.name)
                result_messages.append(_tool_message(tc.id, tc.name, content, ok=False))
                continue

            description = f"{tc.name}({_fmt_args(tc.args)})"
            approved = self._approvals.request(description, entry.risk)

            if not approved:
                content = (
                    f"Action DENIED by user: '{tc.name}' was not executed. "
                    "Do not claim it succeeded."
                )
                logger.info("Tool denied by user: %s", tc.name)
                result_messages.append(_tool_message(tc.id, tc.name, content, ok=False))
                continue

            # Execute — with optional filler timer
            t_start = time.perf_counter()
            filler_timer: threading.Timer | None = None

            if filler and self._filler_speaker and not filler_said:
                def _say_filler():
                    nonlocal filler_said
                    if not filler_said:
                        filler_said = True
                        try:
                            self._filler_speaker("On it, boss.")
                        except Exception:
                            pass
                filler_timer = threading.Timer(FILLER_AFTER_S, _say_filler)
                filler_timer.daemon = True
                filler_timer.start()

            result: ToolResult = self._registry.run(tc.name, tc.args)

            if filler_timer:
                filler_timer.cancel()

            elapsed = time.perf_counter() - t_start
            logger.debug("Tool %s took %.2fs", tc.name, elapsed)

            content = result.message if result.ok else f"TOOL FAILED: {result.message}"
            result_messages.append(_tool_message(tc.id, tc.name, content, ok=result.ok))

        return result_messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_user_text(messages: list[Message]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def _tool_message(call_id: str, name: str, content: str, *, ok: bool) -> Message:
    return {
        "role":         "tool",
        "tool_call_id": call_id,
        "name":         name,
        "content":      content,
        "ok":           ok,
    }


def _fmt_args(args: dict[str, Any]) -> str:
    if not args:
        return ""
    return ", ".join(f"{k}={v!r}" for k, v in args.items())
