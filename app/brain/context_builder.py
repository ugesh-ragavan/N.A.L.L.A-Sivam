"""app/brain/context_builder.py

Dynamic Context Block and Care Rules Tracker for NALLA SIVAM.

Builds a concise (< 300 token) context block injected into the system prompt:
  • Current local date, time, weekday, and time-of-day band (late night 00:30-05:00, morning, afternoon, evening).
  • Uses system timezone or override setting (never assumes a timezone).
  • Minutes since user's last message.
  • Next 3 open tasks and any reminders due in the next hour.
  • Top 3 relevant long-term memories.
  • Contextual Care Rules (late night sleep nudge, busy/deadline respect, stressed warmth, 2-hr chat break).
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]

from app import config
from app.memory.store import store

logger = logging.getLogger("nalla.brain.context_builder")

# Global in-memory care tracking state (survives turns, reset on explicit user intent or long idle)
_care_state = {
    "last_nudge_time": None,          # datetime of last late-night or break nudge
    "no_nag_mode": False,              # set True if user says "don't nag", "busy", "deadline"
    "session_chat_start": None,        # datetime when current continuous conversation started
    "last_interaction_time": None,     # datetime of last interaction
    "fast_path_late_night_used": False,# set True if fast-path time already gave late-night greeting
}


def reset_care_state() -> None:
    """Reset care state (e.g. for testing or new session)."""
    global _care_state
    _care_state["last_nudge_time"] = None
    _care_state["no_nag_mode"] = False
    _care_state["session_chat_start"] = None
    _care_state["last_interaction_time"] = None
    _care_state["fast_path_late_night_used"] = False


def record_nudge_given(simulated_now: datetime | None = None) -> None:
    """Record that a care nudge was presented to the user."""
    _care_state["last_nudge_time"] = simulated_now or get_current_time()


def get_care_state() -> dict[str, Any]:
    return dict(_care_state)


def get_current_time(simulated_now: datetime | None = None) -> datetime:
    """Return current datetime respecting config.USER_TIMEZONE override."""
    if simulated_now is not None:
        return simulated_now

    tz_str = getattr(config, "USER_TIMEZONE", "system")
    if tz_str and tz_str != "system":
        if tz_str.upper() == "UTC":
            return datetime.now(timezone.utc)
        if ZoneInfo is not None:
            try:
                return datetime.now(ZoneInfo(tz_str))
            except Exception as exc:
                logger.warning("Invalid timezone '%s', using system: %s", tz_str, exc)
    return datetime.now()


def _parse_time_str(val: str, default: time) -> time:
    try:
        parts = val.split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return default


def get_time_band(dt: datetime) -> str:
    """Determine time band: late_night, morning, afternoon, evening, night."""
    t = dt.time()
    ln_start = _parse_time_str(getattr(config, "LATE_NIGHT_START", "00:30"), time(0, 30))
    ln_end = _parse_time_str(getattr(config, "LATE_NIGHT_END", "05:00"), time(5, 0))

    if ln_start <= ln_end:
        if ln_start <= t <= ln_end:
            return "late_night"
    else:
        # Crosses midnight (e.g., 23:30 to 05:00)
        if t >= ln_start or t <= ln_end:
            return "late_night"

    if time(5, 0) <= t < time(12, 0):
        return "morning"
    if time(12, 0) <= t < time(17, 0):
        return "afternoon"
    if time(17, 0) <= t < time(21, 0):
        return "evening"
    return "night"


def check_no_nag_intent(text: str) -> bool:
    """Detect if user asked not to be nagged or indicated a strict deadline."""
    lower = text.lower()
    nag_cues = [
        "don't nag", "dont nag", "stop nagging", "stop advising",
        "i am busy", "i'm busy", "on a deadline", "urgent work",
        "leave me alone", "no more nudges", "just answer", "don't lecture",
    ]
    return any(cue in lower for cue in nag_cues)


def build_context_block(
    session_id: str,
    user_text: str = "",
    top_memories: list[str] | None = None,
    simulated_now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the < 300 token dynamic context block and care directives.

    Returns dict with:
      - 'block_text': str to insert into system prompt
      - 'time_band': str
      - 'nudge_eligible': bool
      - 'minutes_since_last': int | None
      - 'open_tasks': list[dict]
      - 'reminders': list[dict]
    """
    now = get_current_time(simulated_now)
    time_band = get_time_band(now)

    # Track continuous session chat duration
    if _care_state["session_chat_start"] is None:
        _care_state["session_chat_start"] = now
    _care_state["last_interaction_time"] = now

    # Check for "no nag" user instruction
    if check_no_nag_intent(user_text):
        _care_state["no_nag_mode"] = True
        logger.info("Care nudges suppressed for session: user requested no nagging/deadline.")

    # Minutes since user's last message in persistent storage
    last_msg_dt = store.get_last_message_timestamp(session_id)
    minutes_since_last: int | None = None
    if last_msg_dt:
        # Normalize timezones for delta calculation
        diff_seconds = (now.astimezone(timezone.utc) - last_msg_dt).total_seconds()
        minutes_since_last = max(0, int(diff_seconds // 60))

    # Next 3 open tasks
    open_tasks = store.list_tasks(status="open", limit=3)

    # Reminders due in the next 1 hour
    all_pending_reminders = store.list_reminders(include_done=False, limit=10)
    upcoming_reminders = []
    one_hour_ahead = now + timedelta(hours=1)
    for rem in all_pending_reminders:
        try:
            r_due = datetime.fromisoformat(rem["due_at"])
            if r_due.tzinfo is None:
                r_due = r_due.replace(tzinfo=now.tzinfo)
            if now <= r_due <= one_hour_ahead:
                upcoming_reminders.append(rem)
        except Exception:
            pass

    # Evaluate Nudge Cooldown
    nudge_eligible = False
    cooldown_min = getattr(config, "NUDGE_COOLDOWN_MINUTES", 60)
    care_enabled = getattr(config, "CARE_NUDGES", True) and not _care_state["no_nag_mode"]

    if care_enabled:
        last_nudge = _care_state["last_nudge_time"]
        if last_nudge is None:
            nudge_eligible = True
        else:
            elapsed_min = (now - last_nudge).total_seconds() / 60.0
            if elapsed_min >= cooldown_min:
                nudge_eligible = True

        # If fast-path already used late night nudge in this window, suppress immediate repetition
        if _care_state["fast_path_late_night_used"]:
            nudge_eligible = False

    # Check chat duration (> 2 hours straight)
    long_session_break = False
    session_start = _care_state["session_chat_start"]
    if session_start and care_enabled and nudge_eligible:
        session_hours = (now - session_start).total_seconds() / 3600.0
        if session_hours >= 2.0:
            long_session_break = True

    # Build concise text block
    date_str = now.strftime("%A, %Y-%m-%d %I:%M %p")
    lines = [
        "=== REAL-TIME CONTEXT ===",
        f"Local Time: {date_str} (Band: {time_band})",
    ]
    if minutes_since_last is not None:
        lines.append(f"Idle time: {minutes_since_last}m since last user turn")

    # Tasks section
    if open_tasks:
        task_strs = []
        for t in open_tasks:
            due_part = f" due:{t['due_at'][:16]}" if t.get("due_at") else ""
            task_strs.append(f"#{t['id']} (P{t['priority']}) {t['title']}{due_part}")
        lines.append("Next open tasks: " + " | ".join(task_strs))
    else:
        lines.append("Next open tasks: NONE (You must never invent a task if user asks)")

    if upcoming_reminders:
        rem_strs = [f"#{r['id']} at {r['due_at'][11:16]}: {r['text']}" for r in upcoming_reminders]
        lines.append("Reminders (next 60m): " + " | ".join(rem_strs))

    # Top long-term memories
    if top_memories:
        lines.append("Recalled facts:\n" + "\n".join(f"• {m}" for m in top_memories[:3]))

    # Care rules directives
    care_directives = []
    if not care_enabled:
        care_directives.append("CARE: User is focused/busy. Do NOT offer health advice or sleep nudges.")
    elif time_band == "late_night" and nudge_eligible:
        care_directives.append(
            "CARE RULE (Late Night): Fully answer the user's question first. Then add ONE short, "
            "caring nudge in your own voice to tell him to sleep (e.g. 'Tasks won't run away, but I want you to rest. Go sleep. Still, next up is...'). "
            "Keep sarcasm intact if not stressed, but warmth must be obvious."
        )
    elif long_session_break and nudge_eligible:
        care_directives.append(
            "CARE RULE (Long Session): User has been coding/chatting for 2+ hours. "
            "Add one light, brief suggestion to take a 5-minute break or stretch."
        )

    if care_directives:
        lines.append("Directives: " + " ".join(care_directives))
    lines.append("=========================")

    block_text = "\n".join(lines)
    return {
        "block_text": block_text,
        "time_band": time_band,
        "nudge_eligible": nudge_eligible,
        "minutes_since_last": minutes_since_last,
        "open_tasks": open_tasks,
        "reminders": upcoming_reminders,
        "now": now,
    }
