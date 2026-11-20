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
