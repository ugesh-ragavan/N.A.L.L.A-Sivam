"""app/brain/fast_router.py

Local fast-path router — handles common intents WITHOUT calling the LLM.
Target latency: < 100 ms end-to-end (no network, no model load).

Matched intents
---------------
  greetings     — "Nalla", "hey nalla", "hi nalla"
  time / date   — "what time is it", "what's the date", "current time"
  mic mute      — "mute mic", "mute yourself"
  mic unmute    — "unmute", "unmute mic"
  stop          — "stop", "stop talking", "cancel"
  reminders     — "any reminders", "my reminders"

Returns None when no fast-path match, so caller falls through to the LLM.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

logger = logging.getLogger("nalla.fast_router")

# ---------------------------------------------------------------------------
# Compiled patterns — cheaper than re-compiling per call
# ---------------------------------------------------------------------------

_GREETING = re.compile(
    r"^(hey\s+)?nalla\s*[!\s]*$|^(yo|hello|hi)\s+nalla\s*$",
    re.IGNORECASE,
)

_TIME = re.compile(
    r"\b(what\s*(is|'?s)\s+the\s+time|current\s+time|what\s+time\s+is\s+it"
    r"|time\s+now|tell\s+me\s+the\s+time)\b",
    re.IGNORECASE,
)

_DATE = re.compile(
    r"\b(what\s*(is|'?s)\s+the\s+date|today['\s]+s?\s+date|what\s+day\s+is\s+(it|today)"
    r"|current\s+date|day\s+and\s+date)\b",
    re.IGNORECASE,
)

_MIC_MUTE = re.compile(
    r"\b(mute\s+(your\s+)?mic(rophone)?|stop\s+listening|be\s+quiet)\b",
    re.IGNORECASE,
)

_MIC_UNMUTE = re.compile(
    r"\b(unmute\s*(your\s+)?mic(rophone)?|start\s+listening\s+again|listen\s+again)\b",
    re.IGNORECASE,
)

_STOP = re.compile(
    r"^(stop|cancel|shut\s+up|stop\s+talking|that['\s]+s?\s+enough)\s*[.!]?\s*$",
    re.IGNORECASE,
)

_REMINDERS = re.compile(
    r"\b(any|my|show|list)\s+reminder(s)?\b|\breminder(s)?\s+(due|today|now)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def route(user_text: str, pipeline: "object | None" = None) -> str | None:
    """Try to handle *user_text* locally.

    Parameters
    ----------
    user_text:  Cleaned STT output.
    pipeline:   VoicePipeline instance (optional); used for mute/unmute.

    Returns
    -------
    A response string if handled locally, else None.
    """
    text = user_text.strip()
    if not text:
        return None

    # Greeting
    if _GREETING.match(text):
        logger.debug("Fast-path: greeting")
        return "Yes boss, sollunga!"

    # Stop
    if _STOP.match(text):
        logger.debug("Fast-path: stop")
        if pipeline is not None:
            try:
                pipeline.interrupt()  # type: ignore[attr-defined]
            except Exception:
                pass
        return "Seri boss, stopping."

    # Mute
    if _MIC_MUTE.search(text):
        logger.debug("Fast-path: mute mic")
        if pipeline is not None:
            try:
                pipeline.set_muted(True)  # type: ignore[attr-defined]
            except Exception:
                pass
        return "Mic muted, boss."

    # Unmute
    if _MIC_UNMUTE.search(text):
        logger.debug("Fast-path: unmute mic")
        if pipeline is not None:
            try:
                pipeline.set_muted(False)  # type: ignore[attr-defined]
            except Exception:
                pass
        return "Mic is live again, boss."

    # Time
    if _TIME.search(text):
        logger.debug("Fast-path: time")
        now = datetime.now().strftime("%I:%M %p")
        return f"It's {now}, boss."

    # Date
    if _DATE.search(text):
        logger.debug("Fast-path: date")
        today = datetime.now().strftime("%A, %d %B %Y")
        return f"Today is {today}, boss."

    # Reminders (local DB — still fast, no LLM)
    if _REMINDERS.search(text):
        logger.debug("Fast-path: reminders")
        try:
            from app.memory.store import store
            reminders = store.list_reminders(include_done=False)
            pending = [r for r in reminders if r.get("status") == "pending"]
            if not pending:
                return "No pending reminders, boss."
            items = "; ".join(
                f"#{r['id']}: {r['text']} at {r['due_at'][11:16]}"
                for r in pending[:3]
            )
            return f"You have {len(pending)} reminder(s): {items}."
        except Exception as exc:
            logger.debug("Fast-path reminders failed: %s", exc)
            return None

    return None
