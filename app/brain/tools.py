"""app/brain/tools.py

Tool registry and tool decorator.

Every callable that Nalla can invoke must be registered here via the
``@tool`` decorator.  The registry converts registrations into the
provider-agnostic JSON schema that LLMProvider.generate() expects,
and also holds the risk level for the ApprovalManager.

Data classes
------------
ToolResult — the structured return value every tool function must return.

Usage::

    from app.brain.tools import registry, tool, ToolResult
    from app.core.approvals import RiskLevel

    @tool(
        name="get_time",
        description="Return the current local date and time.",
        risk=RiskLevel.GREEN,
        parameters={},          # no parameters
    )
    def get_time() -> ToolResult:
        from datetime import datetime
        return ToolResult(ok=True, message=datetime.now().isoformat())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from app.core.approvals import RiskLevel

logger = logging.getLogger("nalla.tools")

# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Structured return value for every registered tool.

    Attributes
    ----------
    ok:      True when the action succeeded (confirmed by the tool itself).
             The agent loop MUST NOT fabricate success when ok is False.
    message: Human-readable summary of what happened (success or failure).
    data:    Optional structured payload (sensor readings, relay state, …).
    """
    ok:      bool
    message: str
    data:    dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal registration entry
# ---------------------------------------------------------------------------

@dataclass
class _ToolEntry:
    name:        str
    description: str
    risk:        RiskLevel
    parameters:  dict[str, Any]   # JSON-Schema object for the tool's args
    fn:          Callable[..., ToolResult]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Holds all registered tools and exposes them to the LLM and agent loop."""

    def __init__(self) -> None:
        self._tools: dict[str, _ToolEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        description: str,
        risk: RiskLevel,
        parameters: dict[str, Any],
        fn: Callable[..., ToolResult],
    ) -> None:
        """Register a tool function.  Raises ValueError on name collision."""
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered.")
        self._tools[name] = _ToolEntry(
            name=name,
            description=description,
            risk=risk,
            parameters=parameters,
            fn=fn,
        )
        logger.debug("Tool registered: %s [%s]", name, risk.value)

    def tool(
        self,
        name: str,
        description: str,
        risk: RiskLevel,
        parameters: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
        """Decorator that registers a function as a Nalla tool.

        Example::

            @registry.tool("get_time", "Return current time", RiskLevel.GREEN)
            def get_time() -> ToolResult: ...
        """
        def decorator(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
            self.register(name, description, risk, parameters or {}, fn)
            return fn
        return decorator

    # ------------------------------------------------------------------
    # Lookup / introspection
    # ------------------------------------------------------------------

    def get(self, name: str) -> _ToolEntry | None:
        """Return the entry for *name*, or None if not registered."""
        return self._tools.get(name)

    def run(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Execute a registered tool by name.

        Returns a failed ToolResult (ok=False) if the tool is unknown
        or raises an exception — the agent loop must surface this honestly.
        """
        entry = self._tools.get(name)
        if entry is None:
            msg = f"Tool '{name}' is not registered."
            logger.error(msg)
            return ToolResult(ok=False, message=msg)
        try:
            result = entry.fn(**args)
            if not isinstance(result, ToolResult):
                # Developer error — wrap so the loop can handle it cleanly.
                return ToolResult(ok=False, message=f"Tool '{name}' returned an unexpected type.")
            if result.ok:
                logger.info("Tool OK [%s]: %s", name, result.message)
            else:
                logger.warning("Tool FAILED [%s]: %s", name, result.message)
            return result
        except Exception as exc:
            msg = f"Tool '{name}' raised an exception: {exc}"
            logger.exception(msg)
            return ToolResult(ok=False, message=msg)

    def as_schema(self) -> list[dict[str, Any]]:
        """Return all tools as a list of provider-agnostic JSON schemas."""
        return [
            {
                "name": e.name,
                "description": e.description,
                "parameters": e.parameters,
            }
            for e in self._tools.values()
        ]

    def summary(self) -> str:
        """One-line description of every tool (used in the system prompt)."""
        if not self._tools:
            return "(no tools connected)"
        lines = []
        for e in self._tools.values():
            lines.append(f"  • {e.name} [{e.risk.value.upper()}] — {e.description}")
        return "\n".join(lines)

    @property
    def names(self) -> list[str]:
        """Sorted list of registered tool names."""
        return sorted(self._tools.keys())


# ---------------------------------------------------------------------------
# Global singleton registry
# ---------------------------------------------------------------------------

registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Built-in stub tools — Phase 1
# ---------------------------------------------------------------------------

@registry.tool(
    name="get_time",
    description="Return the current local date and time.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def _get_time() -> ToolResult:
    """Return the current timestamp."""
    now = datetime.now().strftime("%A, %d %B %Y %H:%M:%S")
    return ToolResult(ok=True, message=f"Current time: {now}", data={"timestamp": now})


@registry.tool(
    name="echo_test",
    description="Echo back a message. Used for testing the tool pipeline.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to echo back.",
            }
        },
        "required": ["text"],
    },
)
def _echo_test(text: str) -> ToolResult:
    """Echo the supplied text back to the caller."""
    return ToolResult(ok=True, message=f"Echo: {text}", data={"echoed": text})


@registry.tool(
    name="fake_relay_toggle",
    description="Toggle a named relay on or off. AMBER: requires approval before running.",
    risk=RiskLevel.AMBER,
    parameters={
        "type": "object",
        "properties": {
            "relay_id": {
                "type": "string",
                "description": "Identifier of the relay to toggle (e.g. 'relay_1').",
            },
            "state": {
                "type": "string",
                "enum": ["on", "off"],
                "description": "Desired state: 'on' or 'off'.",
            },
        },
        "required": ["relay_id", "state"],
    },
)
def _fake_relay_toggle(relay_id: str, state: str) -> ToolResult:
    """Stub relay toggle — will be replaced by real MQTT in Phase 5."""
    logger.info("STUB: relay %s → %s", relay_id, state)
    return ToolResult(
        ok=True,
        message=f"Done, boss. Relay '{relay_id}' is now {state}. Everything checks out.",
        data={"relay_id": relay_id, "state": state},
    )


# ---------------------------------------------------------------------------
# Phase 3: Memory & Reminder Tools
# ---------------------------------------------------------------------------

@registry.tool(
    name="remember",
    description=(
        "Save an item to Nalla's durable local memory. "
        "ONLY call when the user explicitly asks ('remember this', 'note that', 'keep in mind') "
        "or explicitly confirms a suggestion. NEVER store passwords, card numbers, or ID numbers."
    ),
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The exact fact, preference, project detail, or task to remember.",
            },
            "category": {
                "type": "string",
                "enum": ["preference", "fact", "project", "person", "task"],
                "description": "Classification category for the memory.",
            },
            "importance": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "Subjective importance level from 1 (low) to 5 (critical). Default is 3.",
            },
        },
        "required": ["text", "category"],
    },
)
def _remember(text: str, category: str, importance: int = 3) -> ToolResult:
    from app.memory.store import store as memory_store

    try:
        mid = memory_store.save_memory(
            text=text,
            category=category,
            importance=importance,
            source="user_said",
        )
        return ToolResult(
            ok=True,
            message=f"Done, boss. Remembered: '{text}' (category: {category}, ID: {mid}).",
            data={"id": mid, "text": text, "category": category, "importance": importance},
        )
    except ValueError as exc:
        return ToolResult(ok=False, message=str(exc))


@registry.tool(
    name="recall",
    description="Search Nalla's local memory for relevant facts, preferences, projects, or persons.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords or phrase to search for in local memory.",
            }
        },
        "required": ["query"],
    },
)
def _recall(query: str) -> ToolResult:
    from app.memory.store import store as memory_store

    memories = memory_store.search_memories(query, limit=5)
    if not memories:
        return ToolResult(
            ok=True,
            message=f"No memories found matching '{query}'.",
            data={"results": []},
        )
    formatted = "\n".join(
        f"#{m['id']} [{m['category']}, imp:{m['importance']}]: {m['text']}" for m in memories
    )
    return ToolResult(
        ok=True,
        message=f"Found {len(memories)} memory/memories:\n{formatted}",
        data={"results": memories},
    )


@registry.tool(
    name="forget",
    description="Permanently and irreversibly delete a memory by ID. Requires user confirmation before deletion.",
    risk=RiskLevel.AMBER,
    parameters={
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "The numeric ID of the memory to delete.",
            }
        },
        "required": ["memory_id"],
    },
)
def _forget(memory_id: int) -> ToolResult:
    from app.memory.store import store as memory_store

    deleted = memory_store.delete_memory(memory_id)
    if deleted:
        return ToolResult(
            ok=True,
            message=f"Done, boss. Memory #{memory_id} permanently forgotten.",
            data={"id": memory_id},
        )
    return ToolResult(ok=False, message=f"Memory #{memory_id} not found.")


@registry.tool(
    name="set_reminder",
    description="Set a scheduled reminder for a future time. due_at should be an ISO timestamp (e.g. 2026-11-20T16:00:00).",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "What to remind the user about.",
            },
            "due_at": {
                "type": "string",
                "description": "ISO timestamp for when the reminder is due (e.g. '2026-11-20T16:00:00').",
            },
        },
        "required": ["text", "due_at"],
    },
)
def _set_reminder(text: str, due_at: str) -> ToolResult:
    from app.memory.store import store as memory_store

    try:
        rid = memory_store.save_reminder(text=text, due_at=due_at)
        return ToolResult(
            ok=True,
            message=f"Done, boss. Reminder set for {due_at}: '{text}' (ID: #{rid}).",
            data={"id": rid, "text": text, "due_at": due_at},
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to set reminder: {exc}")


@registry.tool(
    name="list_reminders",
    description="List all pending reminders.",
    risk=RiskLevel.GREEN,
    parameters={"type": "object", "properties": {}},
)
def _list_reminders() -> ToolResult:
    from app.memory.store import store as memory_store

    reminders = memory_store.list_reminders(include_done=False)
    if not reminders:
        return ToolResult(
            ok=True,
            message="No pending reminders.",
            data={"reminders": []},
        )
    formatted = "\n".join(f"#{r['id']} (due {r['due_at']}): {r['text']}" for r in reminders)
    return ToolResult(
        ok=True,
        message=f"Pending reminders:\n{formatted}",
        data={"reminders": reminders},
    )

