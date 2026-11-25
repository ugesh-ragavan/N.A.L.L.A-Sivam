"""app/brain/offline.py

Degraded Offline Mode (Prompt 5C).

When Gemini or network connectivity is unavailable:
- Handles time and date, tasks, reminders, notes, and memory lookup locally via templates.
- Replies in NALLA SIVAM persona (dry wit, caring, Coimbatore engineer-friend, honest AI).
- Speaks one degraded notice per session without repeating on every turn.
- Clearly distinguishes between what works locally (tasks, notes, facts, time) and what needs internet.
"""

from __future__ import annotations

import logging
import re
import socket
from datetime import datetime
from typing import Any

from app.memory.db import get_connection
from app.memory.memory_v2 import memory_v2

logger = logging.getLogger("nalla.offline")

_FORCE_OFFLINE = False
_SPOKEN_OFFLINE_NOTICE_GIVEN = False
_LAST_ONLINE_CHECK_TIME = 0.0
_CACHED_ONLINE_STATUS = True


def set_force_offline(value: bool) -> None:
    """Manually force offline mode for testing or user preference."""
    global _FORCE_OFFLINE
    _FORCE_OFFLINE = bool(value)
    logger.info("Force offline mode set to: %s", _FORCE_OFFLINE)


def is_force_offline() -> bool:
    """Check if offline mode is explicitly forced."""
    return _FORCE_OFFLINE


def is_online(timeout: float = 1.0) -> bool:
    """Check if network connectivity is available (cached for 10 seconds)."""
    global _LAST_ONLINE_CHECK_TIME, _CACHED_ONLINE_STATUS

    if _FORCE_OFFLINE:
        return False

    now = datetime.now().timestamp()
    if (now - _LAST_ONLINE_CHECK_TIME) < 10.0:
        return _CACHED_ONLINE_STATUS

    # Quick socket probe to reliable public DNS (8.8.8.8) or Cloudflare (1.1.1.1)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("8.8.8.8", 53))
        sock.close()
        _CACHED_ONLINE_STATUS = True
    except Exception:
        _CACHED_ONLINE_STATUS = False

    _LAST_ONLINE_CHECK_TIME = now
    return _CACHED_ONLINE_STATUS


def is_offline() -> bool:
    """Return True if system is offline or forced offline."""
    return not is_online()


def reset_spoken_notice() -> None:
    """Reset the one-time spoken notice flag for a new session."""
    global _SPOKEN_OFFLINE_NOTICE_GIVEN
    _SPOKEN_OFFLINE_NOTICE_GIVEN = False


class OfflineResponder:
    """Generates authentic in-character local responses without cloud LLM dependencies."""

    def handle(self, user_text: str) -> dict[str, Any]:
        """Process user text through local fast-path in degraded offline mode."""
        global _SPOKEN_OFFLINE_NOTICE_GIVEN
        raw = user_text.strip()
        lower = raw.lower()

        # Check notice preamble
        notice_prefix = ""
        if not _SPOKEN_OFFLINE_NOTICE_GIVEN:
            notice_prefix = "[Offline Mode active] Machan, internet disconnect aayirukku. Local-ah ellam paathudalam. "
            _SPOKEN_OFFLINE_NOTICE_GIVEN = True

        # 1. Time / Date
        if any(p in lower for p in ("time", "date", "clock", "what time", "what day", "mani enna", "ethana mani")):
            now = datetime.now()
            time_str = now.strftime("%I:%M %p")
            date_str = now.strftime("%A, %d %B %Y")
            if "date" in lower or "day" in lower:
                reply = f"Innaiku {date_str}, time {time_str} boss. System clock-la irundhu solren."
            else:
                reply = f"Time ippo {time_str}, boss. System clock-la irundhu solren."
            return self._build_result(reply, notice_prefix, "time")

        # 2. Next Todo / Tasks
        if any(p in lower for p in ("todo", "task", "what's next", "what is next", "next work", "velai")):
            reply = self._handle_tasks(lower, raw)
            return self._build_result(reply, notice_prefix, "tasks")

        # 3. Reminders
        if "reminder" in lower:
            reply = self._handle_reminders()
            return self._build_result(reply, notice_prefix, "reminders")

        # 4. Notes
        if "note" in lower:
            reply = self._handle_notes(lower, raw)
            return self._build_result(reply, notice_prefix, "notes")

        # 5. Local Memory / People lookup
        if any(p in lower for p in ("who is", "who's", "what is", "remember", "yaaru", "theriyuma")):
            mem_reply = self._handle_memory_lookup(raw)
            if mem_reply:
                return self._build_result(mem_reply, notice_prefix, "memory")

        # 6. Stop / Mute / Control
        if any(w in lower for w in ("stop", "mute", "quiet", "shut up", "exit")):
            return self._build_result("Seri boss, silent mode.", notice_prefix, "control")

        # 7. General queries requiring internet
        fallback = (
            "Machan, internet disconnected / Gemini reach aagala. "
            "Local database-la time, tasks, notes, facts ellam ippo kooda work aagum. "
            "Deep reasoning and web search internet vandhadhum paathudalam boss."
        )
        return self._build_result(fallback, notice_prefix, "offline_fallback")

    def _handle_tasks(self, lower: str, raw: str) -> str:
        """Fetch tasks directly from SQLite."""
        try:
            with get_connection() as conn:
                # Check for "next" todo
                if "next" in lower:
                    row = conn.execute(
                        """SELECT title, priority, due_at FROM tasks 
                           WHERE status = 'open' 
                           ORDER BY priority ASC, due_at ASC, id ASC LIMIT 1"""
                    ).fetchone()
                    if row:
                        p_label = f"P{row['priority']}" if row['priority'] else "normal"
                        return f"Next up priority task: '{row['title']}' ({p_label}). Local SQLite-la irundhu eduthen boss, internet theva illa."
                    else:
                        return "Open tasks edhuvum illa boss. Ellam mudichitinga!"
                else:
                    # List all open tasks
                    rows = conn.execute(
                        """SELECT title, priority FROM tasks WHERE status = 'open' 
                           ORDER BY priority ASC LIMIT 5"""
                    ).fetchall()
                    if not rows:
                        return "Open tasks edhuvum illa boss. Ellam clean-ah irukku."
                    task_items = [f"{i+1}. {r['title']} (P{r['priority']})" for i, r in enumerate(rows)]
                    return f"Open tasks (local database):\n" + "\n".join(task_items)
        except Exception as exc:
            logger.error("Offline task query error: %s", exc)
            return "Local task database check panna mudiyala boss."

    def _handle_reminders(self) -> str:
        """Fetch pending reminders from SQLite."""
        try:
            with get_connection() as conn:
                rows = conn.execute(
                    """SELECT text, remind_at FROM reminders 
                       WHERE completed = 0 
                       ORDER BY remind_at ASC LIMIT 5"""
                ).fetchall()
                if not rows:
                    return "Upcoming reminders edhuvum illa boss."
                rem_items = [f"• {r['text']} (at {r['remind_at']})" for r in rows]
                return "Upcoming reminders:\n" + "\n".join(rem_items)
        except Exception as exc:
            logger.error("Offline reminders query error: %s", exc)
            return "Reminders read panna mudiyala boss."

    def _handle_notes(self, lower: str, raw: str) -> str:
        """List or read local notes."""
        from app.core.notes import notes_manager

        try:
            if "read note" in lower or "open note" in lower:
                title = re.sub(r"^(?:read|open)\s+note\s+", "", lower).strip()
                content = notes_manager.read_note(title)
                if content:
                    return f"Note '{title}':\n{content[:300]}..."
                return f"'{title}' nu note edhuvum kedaikala boss."
            else:
                notes = notes_manager.list_notes()
                if not notes:
                    return "Notes folder-la notes edhuvum illa boss."
                note_titles = [f"• {n['title']}" for n in notes[:5]]
                return "Local notes:\n" + "\n".join(note_titles)
        except Exception as exc:
            logger.error("Offline notes error: %s", exc)
            return "Notes access panna mudiyala boss."

    def _handle_memory_lookup(self, query: str) -> str | None:
        """Search local facts via SQLite hybrid memory."""
        try:
            facts = memory_v2.search_facts_hybrid(query, limit=3)
            if facts:
                lines = [f"• {f['entity_name']}: {f['text']}" for f in facts]
                return "Local memory-la irundhu kedaichadhu:\n" + "\n".join(lines)
        except Exception as exc:
            logger.error("Offline memory query error: %s", exc)
        return None

    def _build_result(self, text: str, notice: str, category: str) -> dict[str, Any]:
        full_text = f"{notice}{text}" if notice else text
        return {
            "text": full_text,
            "is_offline": True,
            "mode": "degraded_offline",
            "category": category,
            "handled_locally": True,
        }


offline_responder = OfflineResponder()
