"""app/core/planner.py

Multi-Step Autonomous Planner (Prompt 3C).

Guarantees & Safety Constraints (Identity Guard v2):
1. Composite requests needing 3 or more steps produce a structured plan:
   - 1-sentence spoken headline.
   - Visible numbered step list on screen with tool names and risk levels.
2. APPROVAL BATCHING:
   - GREEN steps run without asking.
   - AMBER steps are batched and approved ONCE together.
   - RED steps ALWAYS pause and ask individually at the exact moment they come up.
   - User can say "go", "cancel", or "change step <n>".
3. VERIFICATION & SAFETY:
   - Every step's result is explicitly verified (result.ok == True).
   - On step failure: try one alternative, otherwise stop honestly without fabricating success.
   - Hard caps: max 8 steps, max 180 seconds (3 minutes) overall timeout.
   - Global Stop button / Esc halts execution immediately.
4. Dry-run mode support (config.PLANNER_DRY_RUN).
5. Final report: 1-2 spoken sentences + structured screen report ("Done" only if all verified).
6. Plan and outcome logged in activity log, savable as a note on request.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from app import config
from app.brain.llm_provider import LLMProvider, Message
from app.brain.tools import ToolRegistry, ToolResult, registry
from app.core.approvals import RiskLevel

logger = logging.getLogger("nalla.planner")


@dataclass
class PlanStep:
    step_number: int
    title: str
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    risk: str = "green"  # green | amber | red
    status: str = "pending"  # pending | running | completed | failed | skipped
    result_message: str = ""
    result_data: dict[str, Any] = field(default_factory=dict)
    is_verified: bool = False
    alternative_tried: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    id: str
    query: str
    spoken_summary: str
    steps: list[PlanStep]
    status: str = "awaiting_approval"  # awaiting_approval | running | completed | failed | cancelled
    amber_approved: bool = False
    dry_run: bool = False
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    final_report: str = ""
    spoken_report: str = ""
    error_message: str | None = None
    active_step: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "spoken_summary": self.spoken_summary,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
            "amber_approved": self.amber_approved,
            "dry_run": self.dry_run,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "final_report": self.final_report,
            "spoken_report": self.spoken_report,
            "error_message": self.error_message,
            "active_step": self.active_step,
            "green_count": sum(1 for s in self.steps if s.risk == "green"),
            "amber_count": sum(1 for s in self.steps if s.risk == "amber"),
            "red_count": sum(1 for s in self.steps if s.risk == "red"),
        }


class MultiStepPlanner:
    """Manages multi-step plan generation, batched approval, verified execution, and reporting."""

    def __init__(self, tool_registry: ToolRegistry = registry) -> None:
        self._registry = tool_registry
        self._plans: dict[str, Plan] = {}
        self._active_plan: Plan | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._red_approval_event = threading.Event()
        self._red_approved: bool = False
        self._pending_red_step: PlanStep | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as exc:
                logger.debug("Planner listener failed: %s", exc)

    def get_plan(self, plan_id: str) -> Plan | None:
        with self._lock:
            return self._plans.get(plan_id)

    def get_active_plan(self) -> Plan | None:
        with self._lock:
            return self._active_plan

    def create_plan_from_steps(
        self,
        query: str,
        steps_spec: list[dict[str, Any]],
        spoken_summary: str | None = None,
        dry_run: bool | None = None,
    ) -> Plan:
        """Create a Plan object from a list of step specifications (max 8 steps)."""
        max_steps = getattr(config, "PLANNER_MAX_STEPS", 8)
        if len(steps_spec) > max_steps:
            logger.warning("Plan steps (%d) exceeded limit of %d. Trimming.", len(steps_spec), max_steps)
            steps_spec = steps_spec[:max_steps]

        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        steps: list[PlanStep] = []

        for idx, spec in enumerate(steps_spec, start=1):
            tool_name = spec.get("tool_name", "")
            risk_obj = self._registry.get_risk(tool_name) if tool_name in self._registry._tools else RiskLevel.GREEN
            risk_val = spec.get("risk", risk_obj.value if hasattr(risk_obj, "value") else str(risk_obj)).lower()

            step = PlanStep(
                step_number=idx,
                title=spec.get("title", f"Step {idx}: {tool_name}"),
                tool_name=tool_name,
                args=spec.get("args", {}),
                risk=risk_val,
                status="pending",
            )
            steps.append(step)

        if not spoken_summary:
            spoken_summary = f"Here's the plan boss: I'll run {len(steps)} steps for '{query[:40]}'."

        is_dry_run = getattr(config, "PLANNER_DRY_RUN", False) if dry_run is None else dry_run
        has_amber = any(s.risk == "amber" for s in steps)
        has_red = any(s.risk == "red" for s in steps)

        initial_status = "awaiting_approval" if (has_amber or has_red) else "ready"
        plan = Plan(
            id=plan_id,
            query=query,
            spoken_summary=spoken_summary,
            steps=steps,
            status=initial_status,
            amber_approved=not has_amber,
            dry_run=is_dry_run,
        )

        with self._lock:
            self._plans[plan_id] = plan
            self._active_plan = plan

        self._broadcast({"type": "plan_created", "plan": plan.to_dict()})
        return plan

    def approve_batched(self, plan_id: str, action: str = "go", modifier: str | None = None) -> dict[str, Any]:
        """User approves batched AMBER steps ('go'), cancels ('cancel'), or modifies a step."""
        plan = self.get_plan(plan_id)
        if not plan:
            return {"ok": False, "error": f"Plan {plan_id} not found."}

        action_clean = action.lower().strip()

        if action_clean in ("cancel", "abort", "stop"):
            plan.status = "cancelled"
            plan.spoken_report = "Plan cancelled by user, boss."
            self._broadcast({"type": "plan_cancelled", "plan": plan.to_dict()})
            return {"ok": True, "action": "cancelled", "plan": plan.to_dict()}

        if action_clean.startswith("change step") or action_clean.startswith("skip step"):
            # Step modification support
            try:
                parts = action_clean.split()
                step_num = int(parts[2])
                target_step = next((s for s in plan.steps if s.step_number == step_num), None)
                if target_step:
                    if action_clean.startswith("skip step"):
                        target_step.status = "skipped"
                        target_step.is_verified = True
                        target_step.result_message = "Skipped by user instruction."
                    else:
                        new_instr = " ".join(parts[3:])
                        target_step.title += f" (Modified: {new_instr})"
                    self._broadcast({"type": "plan_modified", "plan": plan.to_dict()})
                    return {"ok": True, "action": "modified", "plan": plan.to_dict()}
            except Exception as exc:
                return {"ok": False, "error": f"Could not parse step change: {exc}"}

        if action_clean in ("go", "yes", "proceed", "approve"):
            plan.amber_approved = True
            plan.status = "running"
            self._broadcast({"type": "plan_approved", "plan": plan.to_dict()})
            # Start background execution
            threading.Thread(target=self.execute_plan, args=(plan_id,), daemon=True).start()
            return {"ok": True, "action": "started", "plan": plan.to_dict()}

        return {"ok": False, "error": f"Unknown approval action: '{action}'"}

    def resolve_red_step(self, plan_id: str, approved: bool) -> bool:
        """Resolve individual confirmation for a RED step."""
        if not self._pending_red_step:
            return False
        self._red_approved = approved
        self._red_approval_event.set()
        logger.info("Plan %s RED step #%d resolved: %s",
                    plan_id, self._pending_red_step.step_number, "APPROVED" if approved else "DENIED")
        return True

    def stop_active_plan(self) -> bool:
        """Global Stop / Esc: cancel ongoing execution immediately."""
        self._stop_event.set()
        with self._lock:
            if self._active_plan and self._active_plan.status == "running":
                self._active_plan.status = "cancelled"
                self._active_plan.spoken_report = "Execution stopped, boss."
                self._broadcast({"type": "plan_stopped", "plan": self._active_plan.to_dict()})
                logger.info("Plan %s stopped by user.", self._active_plan.id)
                return True
        return False

    def execute_plan(self, plan_id: str) -> Plan:
        """Execute all steps with verification, safety caps, and individual RED approvals."""
        plan = self.get_plan(plan_id)
        if not plan:
            raise ValueError(f"Plan {plan_id} not found.")

        self._stop_event.clear()
        plan.status = "running"
        start_time = time.time()
        timeout_seconds = getattr(config, "PLANNER_TIMEOUT_SECONDS", 180)

        for step in plan.steps:
            # 1. Stop button / cancellation check
            if self._stop_event.is_set():
                plan.status = "cancelled"
                plan.spoken_report = "Plan stopped, boss."
                break

            # 2. Timeout check (max 3 minutes)
            if time.time() - start_time > timeout_seconds:
                plan.status = "failed"
                plan.error_message = f"Plan exceeded safety timeout ({timeout_seconds}s)."
                plan.spoken_report = "Plan timed out before all steps completed, boss."
                break

            # If already skipped
            if step.status == "skipped":
                continue

            plan.active_step = step.step_number
            step.status = "running"
            self._broadcast({"type": "plan_progress", "step": step.to_dict(), "plan": plan.to_dict()})

            # 3. RED Step Gate: RED steps ALWAYS ask individually at the moment they occur
            if step.risk == "red":
                logger.info("Step #%d is RED risk. Pausing for individual approval.", step.step_number)
                self._pending_red_step = step
                self._red_approval_event.clear()
                self._red_approved = False

                self._broadcast({
                    "type": "plan_red_approval_needed",
                    "plan_id": plan.id,
                    "step": step.to_dict(),
                    "message": f"Step #{step.step_number} is high risk ({step.title}). Do you want to proceed?",
                })

                # Wait up to 60s for individual user OK
                approved = self._red_approval_event.wait(timeout=60.0)
                self._pending_red_step = None
                if not approved or not self._red_approved:
                    step.status = "failed"
                    step.result_message = "RED step confirmation denied or timed out."
                    plan.status = "failed"
                    plan.error_message = f"Step {step.step_number} ({step.title}) was denied by user."
                    plan.spoken_report = f"RED step was not approved, boss. Stopping plan here."
                    break

            # 4. Dry Run check
            if plan.dry_run or getattr(config, "PLANNER_DRY_RUN", False):
                step.status = "completed"
                step.is_verified = True
                step.result_message = f"[Dry Run] Simulated execution of {step.tool_name}({step.args})"
                step.result_data = {"dry_run": True, "tool": step.tool_name, "args": step.args}
                self._broadcast({"type": "plan_progress", "step": step.to_dict(), "plan": plan.to_dict()})
                continue

            # 5. Real Execution & Verification
            result = self._execute_single_step(step)
            if result.ok:
                step.status = "completed"
                step.is_verified = True
                step.result_message = result.message
                step.result_data = result.data
            else:
                # Try one alternative or fallback retry
                logger.warning("Step #%d failed (%s). Attempting alternative...", step.step_number, result.message)
                alt_result = self._try_step_alternative(step)
                if alt_result and alt_result.ok:
                    step.status = "completed"
                    step.is_verified = True
                    step.alternative_tried = True
                    step.result_message = f"{alt_result.message} (resolved via alternative)"
                    step.result_data = alt_result.data
                else:
                    step.status = "failed"
                    step.is_verified = False
                    step.result_message = result.message
                    plan.status = "failed"
                    plan.error_message = f"Step {step.step_number} failed: {result.message}"
                    plan.spoken_report = f"Hit a snag on step {step.step_number}, boss: {result.message[:50]}."
                    break

            self._broadcast({"type": "plan_progress", "step": step.to_dict(), "plan": plan.to_dict()})

        # 6. Final Report Generation
        all_completed = all(s.is_verified for s in plan.steps if s.status != "skipped")
        plan.completed_at = time.time()

        if all_completed and plan.status != "cancelled":
            plan.status = "completed"
            plan.spoken_report = f"All {len(plan.steps)} steps executed and verified, boss. Done!"

            # Build structured screen report
            report_lines = [
                f"### Plan Execution Report: {plan.query}",
                f"**Status:** COMPLETED (All {len(plan.steps)} steps verified)",
                f"**Total Execution Time:** {round(plan.completed_at - start_time, 2)}s",
                "",
                "#### Step Details:",
            ]
            for s in plan.steps:
                flag = "✅" if s.is_verified else "❌"
                report_lines.append(f"{s.step_number}. {flag} **{s.title}** (`{s.tool_name}` [{s.risk.upper()}])")
                report_lines.append(f"   - *Result:* {s.result_message}")
            plan.final_report = "\n".join(report_lines)
        elif plan.status != "cancelled":
            plan.status = "failed"
            plan.final_report = f"### Plan Failed\n**Error:** {plan.error_message}\nStopped at step #{plan.active_step}."

        self._log_plan_activity(plan)
        self._broadcast({"type": "plan_completed", "plan": plan.to_dict()})
        return plan

    def _execute_single_step(self, step: PlanStep) -> ToolResult:
        """Call registered tool function and return ToolResult."""
        try:
            entry = self._registry.get(step.tool_name)
            if entry is None:
                return ToolResult(ok=False, message=f"Tool '{step.tool_name}' is not registered.")
            res = entry.fn(**step.args)
            if not isinstance(res, ToolResult):
                return ToolResult(ok=True, message=str(res), data={"raw": res})
            return res
        except Exception as exc:
            return ToolResult(ok=False, message=f"Tool execution exception: {exc}")

    def _try_step_alternative(self, step: PlanStep) -> ToolResult | None:
        """Attempt one fallback alternative when a tool step fails."""
        step.alternative_tried = True
        # If task failed due to missing required field, retry with clean default
        if step.tool_name == "add_task" and "title" not in step.args:
            step.args["title"] = "Follow up task"
            return self._execute_single_step(step)
        if step.tool_name == "create_note" and "content" not in step.args:
            step.args["content"] = "Auto-generated note content."
            return self._execute_single_step(step)
        return None

    def _log_plan_activity(self, plan: Plan) -> None:
        """Persist plan execution summary to SQLite memory store."""
        try:
            from app.memory.store import store
            store.save_memory(
                text=f"Plan executed: {plan.query}. Status: {plan.status}. {plan.spoken_report}",
                category="decision",
                importance=2,
                source="planner",
            )
        except Exception as exc:
            logger.debug("Plan activity logging skipped: %s", exc)

    def save_plan_as_note(self, plan_id: str) -> dict[str, Any]:
        """Save the plan's execution report as a note in data/notes/."""
        plan = self.get_plan(plan_id)
        if not plan:
            return {"ok": False, "error": f"Plan {plan_id} not found."}

        from app.core.notes import create_note
        title = f"Plan: {plan.query[:50]}"
        content = plan.final_report or f"Status: {plan.status}\n{plan.spoken_report}"
        res = create_note(title=title, content=content)
        return {"ok": True, "note": res}


# Global singleton planner instance
planner = MultiStepPlanner()
