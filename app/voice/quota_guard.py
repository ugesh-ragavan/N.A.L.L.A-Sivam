"""app/voice/quota_guard.py

Daily request counter and quota guard for Gemini TTS.

Tracks the number of Gemini TTS requests on a daily basis (resetting at midnight).
Provides cap enforcement, automatic fallback detection, and persistence.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import BASE_DIR

logger = logging.getLogger("nalla.voice.quota_guard")

_DEFAULT_QUOTA_FILE = BASE_DIR / "data" / "tts_quota.json"
_DEFAULT_DAILY_CAP = 50


class QuotaExceededError(RuntimeError):
    """Raised when Gemini TTS daily request limit is reached or 429 returned."""
    pass


class QuotaGuard:
    """Thread-safe daily quota manager for Gemini TTS."""

    def __init__(
        self,
        file_path: Path | str | None = None,
        cap: int = _DEFAULT_DAILY_CAP,
        state_file: Path | str | None = None,
    ) -> None:
        target = state_file or file_path or _DEFAULT_QUOTA_FILE
        self._path = Path(target)
        self._lock = threading.Lock()
        self._date_str = date.today().isoformat()
        self._count = 0
        self._cap = cap
        self._last_error = ""
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            saved_date = data.get("date", "")
            today_str = date.today().isoformat()
            if saved_date == today_str:
                self._date_str = today_str
                self._count = int(data.get("count", 0))
            else:
                # New day: start fresh
                self._date_str = today_str
                self._count = 0
            self._cap = int(data.get("cap", _DEFAULT_DAILY_CAP))
            self._last_error = str(data.get("last_error", ""))
        except Exception as exc:
            logger.warning("Failed to load TTS quota from %s: %s", self._path, exc)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "date": self._date_str,
                "count": self._count,
                "cap": self._cap,
                "last_error": self._last_error,
                "updated_at": datetime.now().isoformat(),
            }
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save TTS quota to %s: %s", self._path, exc)

    def check_and_increment(self) -> int:
        """Verify quota availability and increment counter.

        Raises QuotaExceededError if daily cap is reached.
        """
        with self._lock:
            today_str = date.today().isoformat()
            if self._date_str != today_str:
                self._date_str = today_str
                self._count = 0
                self._last_error = ""

            # 0 means unlimited
            if self._cap > 0 and self._count >= self._cap:
                msg = f"Gemini TTS daily request cap reached ({self._count}/{self._cap})."
                logger.warning(msg)
                raise QuotaExceededError(msg)

            self._count += 1
            self._save()
            logger.debug("Gemini TTS count incremented: %d/%d", self._count, self._cap)
            return self._count

    def record_request(self) -> bool:
        """Verify quota availability and increment counter. Returns True on success."""
        self.check_and_increment()
        return True

    def record_error_429(self, reason: str = "429 Quota Exhausted") -> None:
        """Record an explicit 429 rate-limit error from the Gemini API."""
        with self._lock:
            self._last_error = reason
            self._save()
            logger.warning("Recorded 429 quota error: %s", reason)

    def set_cap(self, cap: int) -> None:
        """Update the daily request cap (0 for unlimited)."""
        with self._lock:
            self._cap = max(0, int(cap))
            self._save()
            logger.info("Updated Gemini TTS daily cap to %d", self._cap)

    def get_status(self) -> dict[str, Any]:
        """Return the current daily quota metrics."""
        with self._lock:
            today_str = date.today().isoformat()
            if self._date_str != today_str:
                self._date_str = today_str
                self._count = 0
                self._last_error = ""
                self._save()

            available = (self._cap == 0) or (self._count < self._cap)
            return {
                "date": self._date_str,
                "count": self._count,
                "cap": self._cap,
                "available": available,
                "remaining": max(0, self._cap - self._count) if self._cap > 0 else "unlimited",
                "last_error": self._last_error,
            }

    def reset(self) -> None:
        """Reset today's count to 0."""
        with self._lock:
            self._count = 0
            self._last_error = ""
            self._date_str = date.today().isoformat()
            self._save()


# Module-level default singleton
quota_guard = QuotaGuard()
