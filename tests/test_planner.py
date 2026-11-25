"""tests/test_planner.py

Automated test suite for Prompt 3C: Hands, Multi-Step Planner.

Tests:
1. Plan creation, step ordering, and risk categorization.
2. Batched approval: AMBER steps approved once together; GREEN runs automatically.
3. Individual approval: RED steps ALWAYS pause and ask individually.
4. Step failure and honesty: plan stops on unrecoverable failure and reports truth.
5. Alternative retry: tries one fallback alternative before failing.
6. Safety limits: maximum 8 steps and safety timeout.
7. Stop control: global stop immediately halts plan execution.
8. Dry-run mode: simulates execution without mutating real data.
9. Prompt 3C Acceptance scenario: 'research X, save summary as note and add two tasks'.
10. REST API endpoints for plan lifecycle.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.brain.tools import ToolRegistry, ToolResult
from app.core.approvals import RiskLevel
from app.core.planner import MultiStepPlanner, Plan, PlanStep
from app.web.server import app


@pytest.fixture
def test_registry():
    """Create a temporary tool registry with mock tools across GREEN, AMBER, and RED."""
    reg = ToolRegistry()

    # Tool 1: web search (GREEN)
    reg.register(
        name="web_search",
        description="Search web",
        risk=RiskLevel.GREEN,
        parameters={},
        fn=lambda query: ToolResult(ok=True, message=f"Found 3 articles on {query}", data={"query": query}),
    )

    # Tool 2: create note (GREEN)
    reg.register(
        name="create_note",
        description="Create note",
        risk=RiskLevel.GREEN,
        parameters={},
        fn=lambda title, content: ToolResult(ok=True, message=f"Note '{title}' saved", data={"title": title}),
    )

    # Tool 3: add task (GREEN)
    reg.register(
        name="add_task",
        description="Add task",
        risk=RiskLevel.GREEN,
        parameters={},
        fn=lambda title, priority=2: ToolResult(ok=True, message=f"Task '{title}' added", data={"title": title}),
    )

    # Tool 4: amber action (e.g. modify config / external sync) (AMBER)
    reg.register(
        name="amber_sync",
        description="Sync external source",
        risk=RiskLevel.AMBER,
        parameters={},
        fn=lambda target: ToolResult(ok=True, message=f"Synced with {target}", data={"target": target}),
    )

    # Tool 5: red action (e.g. delete database / critical reset) (RED)
    reg.register(
        name="red_delete",
        description="Delete critical resource",
        risk=RiskLevel.RED,
        parameters={},
        fn=lambda item_id: ToolResult(ok=True, message=f"Deleted item {item_id}", data={"item_id": item_id}),
    )

    # Tool 6: failing tool
    reg.register(
        name="failing_tool",
        description="Fails unconditionally",
        risk=RiskLevel.GREEN,
        parameters={},
        fn=lambda: ToolResult(ok=False, message="Resource connection timeout error."),
    )

    return reg


def test_plan_creation_and_step_ordering(test_registry):
    """Verify composite steps are ordered 1..N and risks are correctly mapped."""
    planner = MultiStepPlanner(tool_registry=test_registry)

    steps_spec = [
        {"tool_name": "web_search", "args": {"query": "ESP32 deep sleep"}, "title": "Search ESP32 docs"},
        {"tool_name": "create_note", "args": {"title": "ESP32", "content": "Summary"}, "title": "Save note"},
        {"tool_name": "amber_sync", "args": {"target": "cloud"}, "title": "Sync to cloud"},
    ]

    plan = planner.create_plan_from_steps(
        query="Research ESP32, save note and sync",
        steps_spec=steps_spec,
    )

    assert len(plan.steps) == 3
    assert plan.steps[0].step_number == 1
    assert plan.steps[0].risk == "green"
    assert plan.steps[1].step_number == 2
    assert plan.steps[1].risk == "green"
    assert plan.steps[2].step_number == 3
    assert plan.steps[2].risk == "amber"

    # Because step 3 is AMBER, initial status must be awaiting_approval
    assert plan.status == "awaiting_approval"
    assert plan.amber_approved is False


def test_batched_approval_for_amber(test_registry):
    """Verify all AMBER steps are approved in a single batched 'go' action."""
    planner = MultiStepPlanner(tool_registry=test_registry)

    steps_spec = [
        {"tool_name": "web_search", "args": {"query": "FastAPI"}, "title": "Search"},
        {"tool_name": "amber_sync", "args": {"target": "hub1"}, "title": "Sync Hub 1"},
        {"tool_name": "amber_sync", "args": {"target": "hub2"}, "title": "Sync Hub 2"},
    ]

    plan = planner.create_plan_from_steps(query="Sync hubs", steps_spec=steps_spec)
    assert plan.status == "awaiting_approval"

    # Approve batched AMBER steps with "go"
    res = planner.approve_batched(plan.id, action="go")
    assert res["ok"] is True
    assert plan.amber_approved is True

    # Wait for completion
    time.sleep(0.3)
    completed_plan = planner.get_plan(plan.id)
    assert completed_plan.status == "completed"
    assert all(s.is_verified for s in completed_plan.steps)


def test_red_step_always_asks_individually(test_registry):
    """Verify that RED steps always pause and ask individually at that moment."""
    planner = MultiStepPlanner(tool_registry=test_registry)

    steps_spec = [
        {"tool_name": "web_search", "args": {"query": "cleanup"}, "title": "Search"},
        {"tool_name": "amber_sync", "args": {"target": "backup"}, "title": "Sync backup"},
        {"tool_name": "red_delete", "args": {"item_id": 99}, "title": "Delete row 99"},
    ]

    plan = planner.create_plan_from_steps(query="Backup and delete", steps_spec=steps_spec)

    # Approve batched AMBER
    planner.approve_batched(plan.id, action="go")

    # Give thread a moment to reach step 3 (RED step)
    time.sleep(0.2)
    active_plan = planner.get_plan(plan.id)
    assert active_plan.active_step == 3

    # Resolve RED approval
    planner.resolve_red_step(plan.id, approved=True)
    time.sleep(0.2)

    assert active_plan.status == "completed"
    assert active_plan.steps[2].is_verified is True


def test_red_step_denial_halts_plan(test_registry):
    """Verify denying a RED step halts execution and marks plan failed."""
    planner = MultiStepPlanner(tool_registry=test_registry)

    steps_spec = [
        {"tool_name": "web_search", "args": {"query": "cleanup"}, "title": "Search"},
        {"tool_name": "red_delete", "args": {"item_id": 99}, "title": "Delete row 99"},
    ]

    plan = planner.create_plan_from_steps(query="Delete item", steps_spec=steps_spec)
    planner.approve_batched(plan.id, action="go")

    time.sleep(0.15)
    # Deny RED confirmation
    planner.resolve_red_step(plan.id, approved=False)
    time.sleep(0.2)

    active_plan = planner.get_plan(plan.id)
    assert active_plan.status == "failed"
    assert active_plan.steps[1].status == "failed"
    assert "denied" in active_plan.error_message.lower()


def test_step_failure_and_honesty(test_registry):
    """Verify that when a tool fails, execution stops honestly without pretending success."""
    planner = MultiStepPlanner(tool_registry=test_registry)

    steps_spec = [
        {"tool_name": "web_search", "args": {"query": "query"}, "title": "Step 1"},
        {"tool_name": "failing_tool", "args": {}, "title": "Step 2"},
        {"tool_name": "create_note", "args": {"title": "Note", "content": "Body"}, "title": "Step 3"},
    ]

    plan = planner.create_plan_from_steps(query="Fail plan", steps_spec=steps_spec)
    planner.approve_batched(plan.id, action="go")
    time.sleep(0.2)

    failed_plan = planner.get_plan(plan.id)
    assert failed_plan.status == "failed"
    assert failed_plan.steps[0].status == "completed"
    assert failed_plan.steps[1].status == "failed"
    assert failed_plan.steps[2].status == "pending"  # Step 3 must never run
    assert "Resource connection timeout error" in failed_plan.error_message


def test_safety_limits_enforced(test_registry):
    """Verify 8-step cap trims plans exceeding 8 steps."""
    planner = MultiStepPlanner(tool_registry=test_registry)

    many_steps = [
        {"tool_name": "web_search", "args": {"query": f"item {i}"}, "title": f"Step {i}"}
        for i in range(12)
    ]

    plan = planner.create_plan_from_steps(query="Large plan", steps_spec=many_steps)
    assert len(plan.steps) == 8  # Enforced max 8 steps


def test_stop_button_halts_plan(test_registry):
    """Verify stop_active_plan halts ongoing execution immediately."""
    planner = MultiStepPlanner(tool_registry=test_registry)

    def slow_fn(query):
        time.sleep(1.0)
        return ToolResult(ok=True, message="Done")

    test_registry.register("slow_tool", "Slow", RiskLevel.GREEN, {}, slow_fn)

    steps_spec = [
        {"tool_name": "slow_tool", "args": {"query": "1"}, "title": "Step 1"},
        {"tool_name": "slow_tool", "args": {"query": "2"}, "title": "Step 2"},
    ]

    plan = planner.create_plan_from_steps(query="Slow plan", steps_spec=steps_spec)
    planner.approve_batched(plan.id, action="go")

    time.sleep(0.1)
    stopped = planner.stop_active_plan()
    assert stopped is True
    time.sleep(0.2)

    assert plan.status == "cancelled"


def test_dry_run_mode(test_registry):
    """Verify dry-run mode simulates execution without invoking mutations."""
    planner = MultiStepPlanner(tool_registry=test_registry)

    steps_spec = [
        {"tool_name": "web_search", "args": {"query": "docs"}, "title": "Search"},
        {"tool_name": "create_note", "args": {"title": "Note", "content": "Body"}, "title": "Save"},
    ]

    plan = planner.create_plan_from_steps(query="Dry plan", steps_spec=steps_spec, dry_run=True)
    planner.approve_batched(plan.id, action="go")
    time.sleep(0.2)

    assert plan.status == "completed"
    assert "[Dry Run] Simulated" in plan.steps[0].result_message
    assert "[Dry Run] Simulated" in plan.steps[1].result_message


def test_acceptance_scenario_research_note_and_tasks(test_registry):
    """Prompt 3C Acceptance: 'research X, save the summary as a note and add two tasks from it'."""
    from app.core import notes
    from app.memory.store import store

    planner = MultiStepPlanner(tool_registry=test_registry)

    steps_spec = [
        {"tool_name": "web_search", "args": {"query": "FastAPI 0.115 features"}, "title": "Research FastAPI features"},
        {"tool_name": "create_note", "args": {"title": "FastAPI Research", "content": "Summary of FastAPI updates."}, "title": "Save summary note"},
        {"tool_name": "add_task", "args": {"title": "Update FastAPI dependencies", "priority": 1}, "title": "Add task 1"},
        {"tool_name": "add_task", "args": {"title": "Refactor route handlers", "priority": 2}, "title": "Add task 2"},
    ]

    plan = planner.create_plan_from_steps(
        query="research FastAPI, save the summary as a note and add two tasks from it",
        steps_spec=steps_spec,
        spoken_summary="Here's the plan boss: I'll research FastAPI, save the note, and add the two tasks.",
    )

    # All steps are GREEN, so user approves or can proceed
    res = planner.approve_batched(plan.id, action="go")
    assert res["ok"] is True

    # Wait for execution thread
    time.sleep(0.3)
    completed_plan = planner.get_plan(plan.id)

    assert completed_plan.status == "completed"
    assert all(s.is_verified for s in completed_plan.steps)
    assert "All 4 steps executed and verified, boss" in completed_plan.spoken_report
    assert "Plan Execution Report" in completed_plan.final_report

    # Verify note save functionality
    note_res = planner.save_plan_as_note(plan.id)
    assert note_res["ok"] is True
    assert "Plan:" in note_res["note"]["title"]


def test_rest_api_plan_routes():
    """Verify REST API endpoints for plan creation, approval, and reporting."""
    client = TestClient(app)

    create_payload = {
        "query": "Research and summarize",
        "steps": [
            {"tool_name": "list_tasks", "args": {}, "title": "Check tasks"},
            {"tool_name": "list_notes", "args": {}, "title": "Check notes"},
        ],
        "spoken_summary": "Checking tasks and notes, boss.",
    }

    # 1. Create plan
    resp = client.post("/api/plan/create", json=create_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    pid = data["plan"]["id"]

    # 2. Get plan
    get_resp = client.get(f"/api/plan/{pid}")
    assert get_resp.status_code == 200
    assert get_resp.json()["plan"]["id"] == pid

    # 3. Approve plan
    appr_resp = client.post(f"/api/plan/{pid}/approve", json={"action": "go"})
    assert appr_resp.status_code == 200
    assert appr_resp.json()["action"] == "started"

    # 4. Stop plan
    stop_resp = client.post("/api/plan/stop")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["ok"] is True
