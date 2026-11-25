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

    def get_risk(self, name: str) -> RiskLevel:
        """Return the risk level for a registered tool, or GREEN if unknown."""
        entry = self.get(name)
        return entry.risk if entry else RiskLevel.GREEN

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


# ---------------------------------------------------------------------------
# Task Management Tools (GREEN risk)
# ---------------------------------------------------------------------------

@registry.tool(
    name="add_task",
    description="Add a new task to your todo list. priority: 1 (high), 2 (medium), 3 (low).",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The title or action of the task."},
            "priority": {"type": "integer", "description": "1 (high), 2 (medium/default), 3 (low).", "default": 2},
            "due_at": {"type": "string", "description": "Optional ISO timestamp or date."},
            "project": {"type": "string", "description": "Optional project tag/name."},
        },
        "required": ["title"],
    },
)
def _add_task(title: str, priority: int = 2, due_at: str | None = None, project: str | None = None) -> ToolResult:
    from app.memory.store import store as memory_store
    try:
        tid = memory_store.add_task(title=title, priority=priority, due_at=due_at, project=project)
        p_str = {1: "high", 2: "medium", 3: "low"}.get(priority, "medium")
        return ToolResult(
            ok=True,
            message=f"Added task #{tid}: '{title}' ({p_str} priority).",
            data={"id": tid, "title": title, "priority": priority, "due_at": due_at, "project": project},
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to add task: {exc}")


@registry.tool(
    name="list_tasks",
    description="List tasks filtered by status ('open' or 'done') and optional project.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "'open' (default) or 'done'.", "default": "open"},
            "project": {"type": "string", "description": "Optional project filter."},
        },
    },
)
def _list_tasks(status: str = "open", project: str | None = None) -> ToolResult:
    from app.memory.store import store as memory_store
    tasks = memory_store.list_tasks(status=status, project=project)
    if not tasks:
        msg = f"No {status} tasks found."
        return ToolResult(ok=True, message=msg, data={"tasks": []})
    formatted = "\n".join(
        f"#{t['id']} [{t['status']}] (P{t['priority']}) {t['title']}"
        + (f" [due {t['due_at']}]" if t.get("due_at") else "")
        + (f" (proj: {t['project']})" if t.get("project") else "")
        for t in tasks
    )
    return ToolResult(ok=True, message=f"Tasks ({status}):\n{formatted}", data={"tasks": tasks})


@registry.tool(
    name="next_task",
    description="Get the single highest priority open task (P1 before P2 before P3, then earliest due date, then oldest).",
    risk=RiskLevel.GREEN,
    parameters={"type": "object", "properties": {}},
)
def _next_task() -> ToolResult:
    from app.memory.store import store as memory_store
    task = memory_store.next_task()
    if not task:
        return ToolResult(
            ok=True,
            message="No open tasks in the list, boss. You're completely caught up!",
            data={"task": None},
        )
    p_str = {1: "high", 2: "medium", 3: "low"}.get(task["priority"], "medium")
    due_info = f", due {task['due_at']}" if task.get("due_at") else ""
    return ToolResult(
        ok=True,
        message=f"Next up is #{task['id']}: '{task['title']}' ({p_str} priority{due_info}).",
        data={"task": task},
    )


@registry.tool(
    name="complete_task",
    description="Mark an open task as completed by its ID.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "The task ID to mark done."},
        },
        "required": ["task_id"],
    },
)
def _complete_task(task_id: int) -> ToolResult:
    from app.memory.store import store as memory_store
    ok = memory_store.complete_task(task_id)
    if ok:
        return ToolResult(ok=True, message=f"Task #{task_id} completed! Great job, boss.", data={"task_id": task_id})
    return ToolResult(ok=False, message=f"Task #{task_id} not found or already completed.")


@registry.tool(
    name="reprioritise_task",
    description="Change priority of a task (1=high, 2=medium, 3=low).",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "The task ID."},
            "new_priority": {"type": "integer", "description": "1 (high), 2 (medium), or 3 (low)."},
        },
        "required": ["task_id", "new_priority"],
    },
)
def _reprioritise_task(task_id: int, new_priority: int) -> ToolResult:
    from app.memory.store import store as memory_store
    try:
        ok = memory_store.reprioritise_task(task_id, new_priority)
        if ok:
            p_str = {1: "high", 2: "medium", 3: "low"}.get(new_priority, str(new_priority))
            return ToolResult(
                ok=True,
                message=f"Task #{task_id} reprioritised to {p_str} (P{new_priority}).",
                data={"task_id": task_id, "priority": new_priority},
            )
        return ToolResult(ok=False, message=f"Task #{task_id} not found.")
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to reprioritise task: {exc}")


# ---------------------------------------------------------------------------
# Phase 6: Browser Control & Web Automation (Adapted from Jarvis Architecture)
# ---------------------------------------------------------------------------

@registry.tool(
    name="browser_open_url",
    description="Open a public webpage directly in the browser (e.g. YouTube, Wikipedia, documentation). Prefer this over search when a specific site or URL is named.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Complete HTTP or HTTPS URL to open."},
        },
        "required": ["url"],
    },
)
def _browser_open_url(url: str) -> ToolResult:
    from app.brain.browser import browser_manager
    try:
        res = browser_manager.open_url(url)
        return ToolResult(
            ok=True,
            message=f"Opened '{res['url']}'. Page title: '{res['title']}'.",
            data=res,
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to open '{url}': {exc}")


@registry.tool(
    name="browser_search",
    description="Perform a fallback DuckDuckGo web search in the browser when no specific website destination is specified.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query text."},
        },
        "required": ["query"],
    },
)
def _browser_search(query: str) -> ToolResult:
    from app.brain.browser import browser_manager
    try:
        res = browser_manager.search_the_web(query)
        return ToolResult(
            ok=True,
            message=f"Search executed for '{query}'. Current page: '{res['title']}'.",
            data=res,
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Search failed for '{query}': {exc}")


@registry.tool(
    name="browser_read_page",
    description="Read visible readable text from the currently open webpage in the browser.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "max_chars": {"type": "integer", "description": "Maximum characters to return (default 2500).", "default": 2500},
        },
    },
)
def _browser_read_page(max_chars: int = 2500) -> ToolResult:
    from app.brain.browser import browser_manager
    try:
        res = browser_manager.read_page(max_chars=max_chars)
        return ToolResult(
            ok=True,
            message=f"Content from '{res['title']}':\n{res['text']}",
            data=res,
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to read page: {exc}")


@registry.tool(
    name="browser_inspect_page",
    description="Inspect interactive controls (buttons, links, inputs) on the current browser page to find targets for click or typing.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "max_elements": {"type": "integer", "description": "Maximum elements to inspect (default 40).", "default": 40},
        },
    },
)
def _browser_inspect_page(max_elements: int = 40) -> ToolResult:
    from app.brain.browser import browser_manager
    try:
        res = browser_manager.inspect_page(max_elements=max_elements)
        elements_fmt = "\n".join(
            f"- [{e['role']}] '{e['name']}' ({e['tag']})" for e in res["elements"]
        )
        return ToolResult(
            ok=True,
            message=f"Found {len(res['elements'])} controls on '{res['title']}':\n{elements_fmt}",
            data=res,
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to inspect page: {exc}")


@registry.tool(
    name="browser_click",
    description="Click a visible control (button, link) by accessible name, visible text, or selector for safe non-consequential navigation.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Name or text of the control to click."},
        },
        "required": ["target"],
    },
)
def _browser_click(target: str) -> ToolResult:
    from app.brain.browser import browser_manager, is_consequential_target
    if is_consequential_target(target):
        return ToolResult(
            ok=False,
            message=(
                f"Target '{target}' appears to be consequential (e.g. submit/buy/delete). "
                "Use 'browser_consequential_click' so the user can review and approve it."
            ),
        )
    try:
        res = browser_manager.click(target)
        return ToolResult(
            ok=True,
            message=f"Clicked '{target}'. Now on '{res['title']}'.",
            data=res,
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to click '{target}': {exc}")


@registry.tool(
    name="browser_consequential_click",
    description="Click a consequential control (e.g. 'Submit', 'Buy', 'Delete', 'Pay', 'Confirm'). AMBER: asks user approval first.",
    risk=RiskLevel.AMBER,
    parameters={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Consequential button or control to click."},
        },
        "required": ["target"],
    },
)
def _browser_consequential_click(target: str) -> ToolResult:
    from app.brain.browser import browser_manager
    try:
        res = browser_manager.click(target)
        return ToolResult(
            ok=True,
            message=f"Consequential action approved and executed: clicked '{target}'. Now on '{res['title']}'.",
            data=res,
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Consequential click failed for '{target}': {exc}")


@registry.tool(
    name="browser_type_text",
    description="Type text into a search box, input field, or textarea on the active page.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Label, placeholder, or selector of the input field."},
            "text": {"type": "string", "description": "The text to type."},
        },
        "required": ["target", "text"],
    },
)
def _browser_type_text(target: str, text: str) -> ToolResult:
    from app.brain.browser import browser_manager
    try:
        res = browser_manager.type_text(target, text)
        return ToolResult(
            ok=True,
            message=f"Typed text into '{target}' and submitted.",
            data=res,
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to type text into '{target}': {exc}")


@registry.tool(
    name="browser_scroll",
    description="Scroll the current page 'down' or 'up'.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["down", "up"], "description": "'down' (default) or 'up'.", "default": "down"},
        },
    },
)
def _browser_scroll(direction: str = "down") -> ToolResult:
    from app.brain.browser import browser_manager
    try:
        res = browser_manager.scroll(direction)
        return ToolResult(ok=True, message=f"Scrolled {direction}.", data=res)
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to scroll: {exc}")


@registry.tool(
    name="browser_go_back",
    description="Navigate back to the previous webpage.",
    risk=RiskLevel.GREEN,
    parameters={"type": "object", "properties": {}},
)
def _browser_go_back() -> ToolResult:
    from app.brain.browser import browser_manager
    try:
        res = browser_manager.go_back()
        return ToolResult(ok=True, message=f"Navigated back to '{res['title']}'.", data=res)
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to go back: {exc}")


@registry.tool(
    name="browser_close",
    description="Close the active browser window when web browsing is finished.",
    risk=RiskLevel.GREEN,
    parameters={"type": "object", "properties": {}},
)
def _browser_close() -> ToolResult:
    from app.brain.browser import browser_manager
    try:
        browser_manager.close()
        return ToolResult(ok=True, message="Browser closed cleanly, boss.", data={})
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to close browser: {exc}")


# ---------------------------------------------------------------------------
# Screen Capture Tool (Prompt 2A)
# ---------------------------------------------------------------------------

@registry.tool(
    name="capture_screen",
    description="Capture user screen ('full', 'active_window', or 'region'). Triggers local Sensitivity Gate preview.",
    risk=RiskLevel.AMBER,
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["full", "active_window", "region"],
                "description": "'full' (default), 'active_window', or 'region'.",
                "default": "full",
            },
        },
    },
)
def _capture_screen_tool(target: str = "full") -> ToolResult:
    from app.core.screen import screen_manager
    try:
        res = screen_manager.create_capture(target=target)
        return ToolResult(
            ok=True,
            message=f"Screen captured ({res['target']}, {res['width']}x{res['height']}). Sensitivity Gate preview is ready.",
            data={"capture_id": res["capture_id"], "width": res["width"], "height": res["height"], "target": res["target"]},
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Screen capture failed: {exc}")


# ---------------------------------------------------------------------------
# Notes Tools (Prompt 3A & 3C)
# ---------------------------------------------------------------------------

@registry.tool(
    name="create_note",
    description="Save a markdown note to data/notes/.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Title of the note."},
            "content": {"type": "string", "description": "Markdown body text."},
        },
        "required": ["title", "content"],
    },
)
def _create_note_tool(title: str, content: str) -> ToolResult:
    from app.core.notes import create_note
    try:
        res = create_note(title=title, content=content)
        return ToolResult(
            ok=True,
            message=f"Saved note '{title}' at {res['filename']}.",
            data=res,
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to create note: {exc}")


@registry.tool(
    name="append_note",
    description="Append content to an existing note.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Title or slug of note."},
            "content": {"type": "string", "description": "Text to append."},
        },
        "required": ["title", "content"],
    },
)
def _append_note_tool(title: str, content: str) -> ToolResult:
    from app.core.notes import append_note
    try:
        res = append_note(title=title, content=content)
        return ToolResult(
            ok=True,
            message=f"Appended to note '{title}'.",
            data=res,
        )
    except Exception as exc:
        return ToolResult(ok=False, message=f"Failed to append note: {exc}")


@registry.tool(
    name="read_note",
    description="Read full markdown content of a note by title.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Title or slug of note."},
        },
        "required": ["title"],
    },
)
def _read_note_tool(title: str) -> ToolResult:
    from app.core.notes import read_note
    txt = read_note(title=title)
    if txt is None:
        return ToolResult(ok=False, message=f"Note '{title}' not found.")
    return ToolResult(ok=True, message=txt, data={"content": txt})


@registry.tool(
    name="list_notes",
    description="List all saved notes.",
    risk=RiskLevel.GREEN,
    parameters={"type": "object", "properties": {}},
)
def _list_notes_tool() -> ToolResult:
    from app.core.notes import list_notes
    notes = list_notes()
    if not notes:
        return ToolResult(ok=True, message="No notes saved yet.", data={"notes": []})
    formatted = "\n".join(f"- {n['title']} ({n['filename']})" for n in notes)
    return ToolResult(ok=True, message=f"Saved notes:\n{formatted}", data={"notes": notes})


# ---------------------------------------------------------------------------
# Memory 2.0 Tools (Prompt 4)
# ---------------------------------------------------------------------------

@registry.tool(
    name="propose_memory",
    description="Propose a learned fact to the Candidate Inbox for user approval before saving. Rejects secrets automatically.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "entity_name": {"type": "string", "description": "Name of person, project, place, or topic."},
            "text": {"type": "string", "description": "The fact learned."},
            "entity_type": {
                "type": "string",
                "enum": ["person", "project", "place", "org", "topic"],
                "description": "Category of entity (default 'topic').",
                "default": "topic",
            },
        },
        "required": ["entity_name", "text"],
    },
)
def _propose_memory_tool(entity_name: str, text: str, entity_type: str = "topic") -> ToolResult:
    from app.memory.memory_v2 import memory_v2
    res = memory_v2.propose_candidate(entity_name=entity_name, text=text, entity_type=entity_type, source="conversation")
    if not res.get("ok", True):
        return ToolResult(ok=False, message=res.get("message", "Rejected."))
    return ToolResult(
        ok=True,
        message=f"Proposed to Candidate Inbox: '{entity_name}: {text}'. Ask user briefly if they want you to remember it.",
        data=res,
    )


# ---------------------------------------------------------------------------
# Deterministic Offline Skills (Human Mode H3 — GREEN)
# ---------------------------------------------------------------------------

@registry.tool(
    name="calculate",
    description="Deterministic AST calculator for arithmetic, percentages, and math functions. Always use for maths instead of mental arithmetic.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "Math or spoken arithmetic expression."},
        },
        "required": ["expression"],
    },
)
def _calculate_tool(expression: str) -> ToolResult:
    from app.brain.skills import safe_calculate
    res = safe_calculate(expression)
    if not res.get("ok"):
        return ToolResult(ok=False, message=res.get("error", "Calculation failed."))
    return ToolResult(ok=True, message=f"{res['formatted']} ({res['working']})", data=res)


@registry.tool(
    name="convert_units",
    description="Deterministic physical unit conversion (length, weight, temperature, volume, speed, data size).",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "value": {"type": "number", "description": "Numeric value to convert."},
            "from_unit": {"type": "string", "description": "Source unit (e.g. fahrenheit, km, mb)."},
            "to_unit": {"type": "string", "description": "Target unit (e.g. celsius, miles, gb)."},
        },
        "required": ["value", "from_unit", "to_unit"],
    },
)
def _convert_units_tool(value: float, from_unit: str, to_unit: str) -> ToolResult:
    from app.brain.skills import convert_units
    res = convert_units(value, from_unit, to_unit)
    if not res.get("ok"):
        return ToolResult(ok=False, message=res.get("error", "Unit conversion failed."))
    return ToolResult(ok=True, message=res["working"], data=res)


@registry.tool(
    name="set_timer",
    description="Set a local persistent countdown timer.",
    risk=RiskLevel.GREEN,
    parameters={
        "type": "object",
        "properties": {
            "duration_seconds": {"type": "integer", "description": "Duration in seconds."},
            "label": {"type": "string", "description": "Optional timer label.", "default": "Timer"},
        },
        "required": ["duration_seconds"],
    },
)
def _set_timer_tool(duration_seconds: int, label: str = "Timer") -> ToolResult:
    from app.brain.skills import set_timer
    res = set_timer(duration_seconds, label)
    return ToolResult(ok=True, message=res["reply"], data=res)


@registry.tool(
    name="get_system_status",
    description="Get truthful local CPU, RAM, and battery status.",
    risk=RiskLevel.GREEN,
    parameters={"type": "object", "properties": {}},
)
def _get_system_status_tool() -> ToolResult:
    from app.brain.skills import get_system_status_reply
    return ToolResult(ok=True, message=get_system_status_reply())







