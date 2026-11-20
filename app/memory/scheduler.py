"""app/memory/scheduler.py

Background asyncio scheduler for due reminders.

Checks every `interval` seconds (default 30s) for reminders whose due_at has arrived,
marks them as done, and triggers a delivery callback or logs the event.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from app.memory.store import MemoryStore, store as default_store

logger = logging.getLogger("nalla.memory.scheduler")


async def reminders_loop(
    callback: Callable[[dict[str, Any]], Any] | None = None,
    interval: float = 30.0,
    store_instance: MemoryStore | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Check due reminders periodically.

    Parameters
    ----------
    callback: Async or sync function invoked for each due reminder.
    interval: Polling frequency in seconds (default 30).
    store_instance: MemoryStore to check (defaults to global store).
    stop_event: Optional asyncio.Event to terminate the loop cleanly.
    """
    store = store_instance or default_store
    logger.info("Reminders scheduler started (interval: %.1fs).", interval)

    while True:
        if stop_event and stop_event.is_set():
            logger.info("Reminders scheduler received stop signal.")
            break

        try:
            due_reminders = store.get_due_reminders()
            for reminder in due_reminders:
                store.mark_reminder_done(reminder["id"])
                logger.info(
                    "REMINDER DUE [#%d]: '%s' (due %s)",
                    reminder["id"],
                    reminder["text"],
                    reminder["due_at"],
                )
                if callback:
                    res = callback(reminder)
                    if asyncio.iscoroutine(res):
                        await res
        except Exception as exc:
            logger.exception("Error checking due reminders: %s", exc)

        try:
            if stop_event:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Reminders scheduler cancelled.")
            break
