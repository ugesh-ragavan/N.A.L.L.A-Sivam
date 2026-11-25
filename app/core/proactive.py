"""app/core/proactive.py

Proactive Speech Policy Engine.
Enforces Identity Guard v2: quiet hours, Do Not Disturb, per-category cooldowns,
hourly/daily rate caps (max 2/hr, 8/day), conversation-idle gating, and decision logging.
"""

from __future__ import annotations

import collections
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app import config
from app.brain.context_builder import get_current_time
from app.core.presence import presence
from app.memory.db import get_connection

logger = logging.getLogger("nalla.proactive")


@dataclass
class ProactiveEvent:
    """A scheduled or triggered proactive utterance candidate."""

    category: str  # briefing | check_in | welcome_back | wrap_up | reminder
    text_or_generator: str | Callable[[], str]
    priority: int = 2  # 1 = user reminder (highest), 2 = routine proactive
    earliest: datetime | None = None
    expires: datetime | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    metadata: dict[str, Any] = field(default_factory=dict)

    def resolve_text(self) -> str:
        """Resolve the final text string."""
        if callable(self.text_or_generator):
            return self.text_or_generator()
        return str(self.text_or_generator)


@dataclass
class PolicyDecision:
    """The result of evaluating a proactive event against policy rules."""

    allowed: bool
    action: str  # spoke | skipped | expired
    reason: str  # ok | quiet_hours | dnd_active | conversation_active | cap_exceeded | cooldown | etc.
    event_id: str
    category: str
    content: str
    timestamp: str


def _normalize_dt(dt: datetime) -> datetime:
    """Ensure datetime is offset-aware UTC for safe comparisons."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ProactivePolicyEngine:
    """Enforces proactive delivery rules, cooldowns, rate caps, and quiet hours."""

    def __init__(self, db_conn_factory=get_connection) -> None:
        self._get_conn = db_conn_factory
        self._recent_decisions: collections.deque[PolicyDecision] = collections.deque(maxlen=20)
        self._last_spoke_per_category: dict[str, datetime] = {}
        self._delivery_callback: Callable[[str, str], Any] | None = None

    def set_delivery_callback(self, callback: Callable[[str, str], Any]) -> None:
        """Register a delivery handler (e.g. TTS speak + toast emitter)."""
        self._delivery_callback = callback

    def is_quiet_hours(self, now: datetime) -> bool:
        """Return True if current local time falls in configured quiet hours."""
        start_str = getattr(config, "PROACTIVE_QUIET_HOURS_START", "23:30")
        end_str = getattr(config, "PROACTIVE_QUIET_HOURS_END", "06:30")
        try:
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
        except Exception:
            sh, sm, eh, em = 23, 30, 6, 30

        cur_min = now.hour * 60 + now.minute
        start_min = sh * 60 + sm
        end_min = eh * 60 + em

        if start_min > end_min:
            # Crosses midnight (e.g. 23:30 to 06:30)
            return cur_min >= start_min or cur_min < end_min
        return start_min <= cur_min < end_min

    def _count_spoke_in_window(self, since: datetime) -> int:
        """Count how many proactive utterances were spoken since given timestamp."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) as count FROM proactive_logs
                WHERE action = 'spoke' AND category != 'reminder' AND timestamp >= ?
                """,
                (since.isoformat(),),
            ).fetchone()
            return row["count"] if row else 0
        except Exception as exc:
            logger.debug("Error querying spoke count: %s", exc)
            return 0
        finally:
            conn.close()

    def evaluate_event(
        self,
        event: ProactiveEvent,
        simulated_now: datetime | None = None,
    ) -> PolicyDecision:
        """Evaluate a proactive event against all policy constraints."""
        now = _normalize_dt(simulated_now or get_current_time())
        now_iso = now.isoformat()

        # 1. Master Proactive Enablement
        if not getattr(config, "PROACTIVE_ENABLED", True) and event.category != "reminder":
            return self._record_decision(False, "skipped", "proactive_disabled", event, now_iso)

        # 2. Per-category toggle in settings
        cat_toggles = {
            "briefing": "PROACTIVE_BRIEFING_ENABLED",
            "check_in": "PROACTIVE_CHECK_INS_ENABLED",
            "welcome_back": "PROACTIVE_WELCOME_BACK_ENABLED",
            "wrap_up": "PROACTIVE_WRAP_UP_ENABLED",
            "reminder": "PROACTIVE_REMINDERS_ENABLED",
        }
        toggle_attr = cat_toggles.get(event.category)
        if toggle_attr and not getattr(config, toggle_attr, True):
            return self._record_decision(False, "skipped", f"category_{event.category}_disabled", event, now_iso)

        # 3. Session or Category Snooze ("not now" / "stop")
        if presence.is_session_snoozed() and event.category != "reminder":
            return self._record_decision(False, "skipped", "session_snoozed", event, now_iso)
        if presence.is_category_snoozed(event.category, simulated_now=now) and event.category != "reminder":
            return self._record_decision(False, "skipped", "category_snoozed", event, now_iso)

        # 4. Expiration check
        if event.expires and now > _normalize_dt(event.expires):
            return self._record_decision(False, "expired", "event_expired", event, now_iso)

        # 5. Earliest window check
        if event.earliest and now < _normalize_dt(event.earliest):
            return self._record_decision(False, "skipped", "too_early", event, now_iso)

        # 6. Quiet Hours check (Only explicit user reminders may break through)
        if self.is_quiet_hours(now) and event.category != "reminder":
            return self._record_decision(False, "skipped", "quiet_hours", event, now_iso)

        # 7. Do Not Disturb check
        if getattr(config, "PROACTIVE_DND", False) and event.category != "reminder":
            return self._record_decision(False, "skipped", "dnd_active", event, now_iso)

        # 8. Active conversation turn check (< 60s)
        if not presence.is_conversation_idle(window_seconds=60.0, simulated_now=now):
            return self._record_decision(False, "skipped", "conversation_active", event, now_iso)

        # 9. Audio muted check
        if presence.is_audio_muted():
            return self._record_decision(False, "skipped", "audio_muted", event, now_iso)

        # 10. Per-category cooldown check
        cooldowns = {
            "welcome_back": timedelta(hours=3),
            "check_in": timedelta(minutes=getattr(config, "CHECK_IN_INTERVAL_MINUTES", 90)),
            "briefing": timedelta(hours=10),
            "wrap_up": timedelta(hours=10),
        }
        if event.category in cooldowns:
            last_spoke = self._last_spoke_per_category.get(event.category)
            if last_spoke and (now - _normalize_dt(last_spoke)) < cooldowns[event.category]:
                return self._record_decision(False, "skipped", "category_cooldown", event, now_iso)


        # 11. Rate Caps (Hourly: max 2/hr, Daily: max 8/day) — Reminders don't consume cap
        if event.category != "reminder":
            max_per_hour = getattr(config, "PROACTIVE_MAX_PER_HOUR", 2)
            one_hour_ago = now - timedelta(hours=1)
            spoke_last_hour = self._count_spoke_in_window(one_hour_ago)
            if spoke_last_hour >= max_per_hour:
                return self._record_decision(False, "skipped", "hourly_cap_exceeded", event, now_iso)

            max_per_day = getattr(config, "PROACTIVE_MAX_PER_DAY", 8)
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            spoke_today = self._count_spoke_in_window(start_of_day)
            if spoke_today >= max_per_day:
                return self._record_decision(False, "skipped", "daily_cap_exceeded", event, now_iso)

        # All policies satisfied — ALLOW delivery
        self._last_spoke_per_category[event.category] = now
        return self._record_decision(True, "spoke", "policy_satisfied", event, now_iso)

    def _record_decision(
        self,
        allowed: bool,
        action: str,
        reason: str,
        event: ProactiveEvent,
        timestamp: str,
    ) -> PolicyDecision:
        content = ""
        if allowed:
            try:
                content = event.resolve_text()
            except Exception as exc:
                logger.warning("Failed to resolve text for proactive event %s: %s", event.id, exc)
                content = "(Content resolution error)"

        decision = PolicyDecision(
            allowed=allowed,
            action=action,
            reason=reason,
            event_id=event.id,
            category=event.category,
            content=content[:200],
            timestamp=timestamp,
        )

        self._recent_decisions.appendleft(decision)

        # Persist decision log to database
        try:
            conn = self._get_conn()
            with conn:
                conn.execute(
                    """
                    INSERT INTO proactive_logs (event_id, category, action, reason, content, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event.id, event.category, action, reason, content[:500], timestamp),
                )
            conn.close()
        except Exception as exc:
            logger.debug("Failed to persist proactive log: %s", exc)

        logger.info(
            "Proactive Policy: [%s] Category '%s' -> %s (%s)",
            action.upper(),
            event.category,
            "ALLOWED" if allowed else "BLOCKED",
            reason,
        )

        # Trigger delivery callback if allowed
        if allowed and content and self._delivery_callback:
            try:
                self._delivery_callback(content, event.category)
            except Exception as exc:
                logger.error("Error executing delivery callback for %s: %s", event.id, exc)

        return decision

    def get_recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent policy decisions for UI / debug drawer."""
        return [
            {
                "event_id": d.event_id,
                "category": d.category,
                "action": d.action,
                "reason": d.reason,
                "allowed": d.allowed,
                "content": d.content,
                "timestamp": d.timestamp,
            }
            for d in list(self._recent_decisions)[:limit]
        ]


# Global singleton engine
proactive_engine = ProactivePolicyEngine()
