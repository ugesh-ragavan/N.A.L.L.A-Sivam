"""app/core/presence.py

Tracks user presence, interaction timestamps, away/back state, and OS idle status.
Supports Windows OS idle detection via ctypes (GetLastInputInfo) and cross-platform fallbacks.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from app import config
from app.brain.context_builder import get_current_time

logger = logging.getLogger("nalla.presence")


def _get_windows_idle_seconds() -> float | None:
    """Return idle seconds since last user keyboard/mouse input on Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("dwTime", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = kernel32.GetTickCount() - lii.dwTime
            return max(0.0, millis / 1000.0)
    except Exception as exc:
        logger.debug("Failed to query GetLastInputInfo: %s", exc)
    return None


def _normalize_dt(dt: datetime) -> datetime:
    """Ensure datetime is offset-aware UTC for safe subtraction."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class PresenceTracker:
    """Singleton tracker for session state, user activity, and proactive snoozes."""

    def __init__(self) -> None:
        self._session_start_time = _normalize_dt(get_current_time())
        self._last_interaction_time = _normalize_dt(get_current_time())
        self._last_turn_completed_time: datetime | None = None
        self._is_snoozed_for_session = False
        self._category_snoozes: dict[str, datetime] = {}
        self._audio_muted = False
        self._was_away = False

    def record_interaction(self, simulated_now: datetime | None = None) -> None:
        """Call whenever the user interacts (speaks, clicks, chats)."""
        now = _normalize_dt(simulated_now or get_current_time())
        away_threshold = getattr(config, "AWAY_THRESHOLD_MINUTES", 90) * 60
        gap = (now - self._last_interaction_time).total_seconds()

        # If user was away and came back
        if gap > away_threshold:
            self._was_away = True
            logger.info("User returned after %.1f minutes away.", gap / 60)

        self._last_interaction_time = now
        self._last_turn_completed_time = now

    def mark_turn_active(self, simulated_now: datetime | None = None) -> None:
        """Mark that an active conversation turn is happening right now."""
        now = _normalize_dt(simulated_now or get_current_time())
        self._last_interaction_time = now
        self._last_turn_completed_time = now

    def get_os_idle_seconds(self) -> float | None:
        """Return system idle seconds if on Windows, else None."""
        return _get_windows_idle_seconds()

    def is_user_away(self, simulated_now: datetime | None = None) -> bool:
        """Return True if user hasn't interacted for > AWAY_THRESHOLD_MINUTES."""
        now = _normalize_dt(simulated_now or get_current_time())
        gap = (now - self._last_interaction_time).total_seconds()
        threshold_s = getattr(config, "AWAY_THRESHOLD_MINUTES", 90) * 60
        return gap >= threshold_s

    def minutes_away(self, simulated_now: datetime | None = None) -> float:
        """Return minutes elapsed since the last user interaction."""
        now = _normalize_dt(simulated_now or get_current_time())
        gap = (now - self._last_interaction_time).total_seconds()
        return max(0.0, gap / 60.0)

    def pop_was_away(self) -> bool:
        """Check and clear the welcome-back flag."""
        was = self._was_away
        self._was_away = False
        return was

    def is_conversation_idle(self, window_seconds: float = 60.0, simulated_now: datetime | None = None) -> bool:
        """Return True if no conversation turn occurred within the last window_seconds."""
        now = _normalize_dt(simulated_now or get_current_time())
        if self._last_turn_completed_time is None:
            return True
        elapsed = (now - self._last_turn_completed_time).total_seconds()
        return elapsed >= window_seconds

    def snooze_session(self) -> None:
        """User said 'not now' or 'stop': mute proactive speech for rest of session."""
        self._is_snoozed_for_session = True
        logger.info("Proactive speech snoozed for the current session.")

    def snooze_category(self, category: str, minutes: int = 30, simulated_now: datetime | None = None) -> None:
        """Snooze a specific proactive category (e.g. check_in for 30 minutes)."""
        now = _normalize_dt(simulated_now or get_current_time())
        from datetime import timedelta
        self._category_snoozes[category] = now + timedelta(minutes=minutes)
        logger.info("Category '%s' snoozed for %d minutes.", category, minutes)

    def is_session_snoozed(self) -> bool:
        return self._is_snoozed_for_session

    def is_category_snoozed(self, category: str, simulated_now: datetime | None = None) -> bool:
        now = _normalize_dt(simulated_now or get_current_time())
        expiry = self._category_snoozes.get(category)
        if expiry and now < expiry:
            return True
        return False

    def set_audio_muted(self, muted: bool) -> None:
        self._audio_muted = muted

    def is_audio_muted(self) -> bool:
        return self._audio_muted

    def get_status(self, simulated_now: datetime | None = None) -> dict[str, Any]:
        now = simulated_now or get_current_time()
        return {
            "session_start": self._session_start_time.isoformat(),
            "last_interaction": self._last_interaction_time.isoformat(),
            "minutes_away": round(self.minutes_away(now), 1),
            "is_away": self.is_user_away(now),
            "is_conversation_idle": self.is_conversation_idle(simulated_now=now),
            "is_session_snoozed": self._is_snoozed_for_session,
            "os_idle_seconds": self.get_os_idle_seconds(),
            "audio_muted": self._audio_muted,
        }


# Global singleton tracker
presence = PresenceTracker()
