"""app/core/scheduler.py

Persistent SQLite job scheduler for proactive presence events and scheduled reminders.
Runs jobs at configured times/intervals and routes them through the ProactivePolicyEngine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app import config
from app.brain.context_builder import get_current_time
from app.core.presence import presence
from app.core.proactive import ProactiveEvent, proactive_engine
from app.memory.db import get_connection
from app.memory.store import store as default_store

logger = logging.getLogger("nalla.scheduler")


def _compute_next_daily_run(time_str: str, now: datetime) -> datetime:
    """Compute the next occurrence of a HH:MM time string."""
    try:
        h, m = map(int, time_str.split(":"))
    except Exception:
        h, m = 8, 30

    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


class PersistentScheduler:
    """Manages persistent jobs in SQLite and evaluates due events."""

    def __init__(self, db_conn_factory=get_connection) -> None:
        self._get_conn = db_conn_factory
        self._job_handlers: dict[str, Callable[[dict[str, Any]], str | None]] = {}
        self._running = False
        self._loop_task: asyncio.Task | None = None

    def register_handler(self, category: str, handler: Callable[[dict[str, Any]], str | None]) -> None:
        """Register a generator/handler function for a job category."""
        self._job_handlers[category] = handler

    def add_job(
        self,
        name: str,
        job_type: str,  # daily | interval | date
        schedule_expr: str,  # "08:30" for daily, "90" (mins) for interval, ISO string for date
        category: str,
        payload: dict[str, Any] | None = None,
        job_id: str | None = None,
        simulated_now: datetime | None = None,
    ) -> str:
        """Add or replace a persistent scheduled job in SQLite."""
        now = simulated_now or get_current_time()
        jid = job_id or str(uuid.uuid4())[:8]

        # Calculate next_run_at
        if job_type == "daily":
            next_run = _compute_next_daily_run(schedule_expr, now)
        elif job_type == "interval":
            mins = int(schedule_expr)
            next_run = now + timedelta(minutes=mins)
        elif job_type == "date":
            next_run = datetime.fromisoformat(schedule_expr)
        else:
            next_run = now

        payload_json = json.dumps(payload or {})
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO scheduled_jobs (id, name, job_type, schedule_expr, category, payload, next_run_at, created_at, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        job_type = excluded.job_type,
                        schedule_expr = excluded.schedule_expr,
                        category = excluded.category,
                        payload = excluded.payload,
                        next_run_at = excluded.next_run_at
                    """,
                    (jid, name, job_type, schedule_expr, category, payload_json, next_run.isoformat(), now.isoformat()),
                )
            logger.info("Job scheduled: '%s' [%s] -> next run at %s", name, category, next_run.isoformat())
            return jid
        finally:
            conn.close()

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        conn = self._get_conn()
        try:
            with conn:
                cursor = conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
                return cursor.rowcount > 0
        finally:
            conn.close()

    def list_jobs(self) -> list[dict[str, Any]]:
        """List all registered scheduled jobs."""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM scheduled_jobs ORDER BY next_run_at ASC").fetchall()
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "job_type": r["job_type"],
                    "schedule_expr": r["schedule_expr"],
                    "category": r["category"],
                    "payload": json.loads(r["payload"] or "{}"),
                    "next_run_at": r["next_run_at"],
                    "last_run_at": r["last_run_at"],
                    "enabled": bool(r["enabled"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_due_jobs(self, simulated_now: datetime | None = None) -> list[dict[str, Any]]:
        """Fetch all enabled jobs whose next_run_at is <= current time."""
        now = simulated_now or get_current_time()
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE enabled = 1 AND next_run_at <= ?
                ORDER BY next_run_at ASC
                """,
                (now.isoformat(),),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "job_type": r["job_type"],
                    "schedule_expr": r["schedule_expr"],
                    "category": r["category"],
                    "payload": json.loads(r["payload"] or "{}"),
                    "next_run_at": r["next_run_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def mark_job_executed(self, job_id: str, simulated_now: datetime | None = None) -> None:
        """Update last_run_at and advance next_run_at for repeating jobs."""
        now = simulated_now or get_current_time()
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return

            job_type = row["job_type"]
            schedule_expr = row["schedule_expr"]
            next_run_iso: str | None = None

            if job_type == "daily":
                next_run = _compute_next_daily_run(schedule_expr, now)
                next_run_iso = next_run.isoformat()
            elif job_type == "interval":
                mins = int(schedule_expr)
                next_run = now + timedelta(minutes=mins)
                next_run_iso = next_run.isoformat()
            else:
                # One-off date job: disable after execution
                pass

            with conn:
                if next_run_iso:
                    conn.execute(
                        "UPDATE scheduled_jobs SET last_run_at = ?, next_run_at = ? WHERE id = ?",
                        (now.isoformat(), next_run_iso, job_id),
                    )
                else:
                    conn.execute(
                        "UPDATE scheduled_jobs SET last_run_at = ?, enabled = 0 WHERE id = ?",
                        (now.isoformat(), job_id),
                    )
        finally:
            conn.close()

    def process_due_jobs(self, simulated_now: datetime | None = None) -> list[Any]:
        """Evaluate due scheduler jobs and due memory reminders through ProactivePolicyEngine."""
        now = simulated_now or get_current_time()
        results = []

        # 1. Process due scheduled jobs
        due_jobs = self.get_due_jobs(simulated_now=now)
        for job in due_jobs:
            cat = job["category"]
            handler = self._job_handlers.get(cat)

            def make_resolver(h=handler, j=job):
                if h:
                    return lambda: h(j["payload"]) or ""
                return lambda: f"Scheduled {j['name']}"

            # Set a 2-hour expiration window for daily briefings/wrap-ups
            expires = now + timedelta(hours=2) if job["job_type"] == "daily" else None

            event = ProactiveEvent(
                id=job["id"],
                category=cat,
                priority=2,
                text_or_generator=make_resolver(),
                expires=expires,
                metadata={"job_name": job["name"]},
            )

            decision = proactive_engine.evaluate_event(event, simulated_now=now)
            self.mark_job_executed(job["id"], simulated_now=now)
            results.append(decision)

        # 2. Check due reminders from MemoryStore
        try:
            due_reminders = default_store.get_due_reminders(simulated_now=now)
            for r in due_reminders:
                default_store.mark_reminder_done(r["id"])
                reminder_event = ProactiveEvent(
                    id=f"rem_{r['id']}",
                    category="reminder",
                    priority=1,  # User reminders are Priority 1 and break through quiet hours
                    text_or_generator=f"Reminder: {r['text']}",
                    metadata={"reminder_id": r["id"]},
                )
                decision = proactive_engine.evaluate_event(reminder_event, simulated_now=now)
                results.append(decision)
        except Exception as exc:
            logger.debug("Error processing due reminders in scheduler: %s", exc)

        return results

    def ensure_default_presence_jobs(self) -> None:
        """Seed default daily briefing, evening wrap-up, and check-in jobs if not present."""
        morning_time = getattr(config, "MORNING_BRIEFING_TIME", "08:30")
        evening_time = getattr(config, "EVENING_WRAP_UP_TIME", "21:30")
        check_in_mins = str(getattr(config, "CHECK_IN_INTERVAL_MINUTES", 90))

        self.add_job(
            job_id="job_morning_briefing",
            name="Morning Briefing",
            job_type="daily",
            schedule_expr=morning_time,
            category="briefing",
        )
        self.add_job(
            job_id="job_evening_wrap_up",
            name="Evening Wrap-Up",
            job_type="daily",
            schedule_expr=evening_time,
            category="wrap_up",
        )
        self.add_job(
            job_id="job_smart_check_in",
            name="Smart Check-In",
            job_type="interval",
            schedule_expr=check_in_mins,
            category="check_in",
        )


# Global singleton scheduler
scheduler = PersistentScheduler()
