"""app/core/quota.py

Quota Awareness and Daily Usage Tracker (Prompt 5C).

Tracks:
- Gemini LLM daily requests and estimated tokens.
- TTS daily characters and requests.
- Automatic daily reset at midnight.
- 80% threshold detection: triggers lightweight command model mode,
  shortens replies, and issues a one-time notification per day.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import (
    BASE_DIR,
    GEMINI_CHAT_MODEL,
    GEMINI_COMMAND_MODEL,
)

logger = logging.getLogger("nalla.quota")

_DEFAULT_QUOTA_FILE = BASE_DIR / "data" / "quota.json"

# Default caps (can be overridden via environment variables or runtime settings)
_DEFAULT_GEMINI_REQUEST_CAP = int(os.getenv("GEMINI_DAILY_REQUEST_CAP", "500"))
_DEFAULT_TTS_CHAR_CAP = int(os.getenv("TTS_DAILY_CHAR_CAP", "100000"))
_THROTTLE_THRESHOLD = 0.80  # 80% of daily cap


class QuotaTracker:
    """Thread-safe manager for daily LLM and TTS quota awareness."""

    def __init__(
        self,
        quota_file: Path | str | None = None,
        gemini_cap: int = _DEFAULT_GEMINI_REQUEST_CAP,
        tts_cap: int = _DEFAULT_TTS_CHAR_CAP,
    ) -> None:
        self.quota_file = Path(quota_file or _DEFAULT_QUOTA_FILE)
        self.gemini_cap = gemini_cap
        self.tts_cap = tts_cap

        self._lock = threading.Lock()
        self._date_str = date.today().isoformat()
        self._gemini_requests = 0
        self._gemini_tokens = 0
        self._tts_requests = 0
        self._tts_characters = 0
        self._warning_notified_today = False
        self._load()

    def _load(self) -> None:
        if not self.quota_file.exists():
            return
        try:
            data = json.loads(self.quota_file.read_text(encoding="utf-8"))
            saved_date = data.get("date", "")
            today_str = date.today().isoformat()
            if saved_date == today_str:
                self._date_str = today_str
                self._gemini_requests = int(data.get("gemini_requests", 0))
                self._gemini_tokens = int(data.get("gemini_tokens", 0))
                self._tts_requests = int(data.get("tts_requests", 0))
                self._tts_characters = int(data.get("tts_characters", 0))
                self._warning_notified_today = bool(data.get("warning_notified_today", False))
                self.gemini_cap = int(data.get("gemini_cap", self.gemini_cap))
                self.tts_cap = int(data.get("tts_cap", self.tts_cap))
            else:
                self._date_str = today_str
                self._gemini_requests = 0
                self._gemini_tokens = 0
                self._tts_requests = 0
                self._tts_characters = 0
                self._warning_notified_today = False
        except Exception as exc:
            logger.warning("Failed to load quota file %s: %s", self.quota_file, exc)

    def _save(self) -> None:
        try:
            self.quota_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "date": self._date_str,
                "gemini_requests": self._gemini_requests,
                "gemini_tokens": self._gemini_tokens,
                "gemini_cap": self.gemini_cap,
                "tts_requests": self._tts_requests,
                "tts_characters": self._tts_characters,
                "tts_cap": self.tts_cap,
                "warning_notified_today": self._warning_notified_today,
                "updated_at": datetime.now().isoformat(),
            }
            self.quota_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save quota file %s: %s", self.quota_file, exc)

    def _check_rollover(self) -> None:
        """Check if today is a new calendar day and reset if needed."""
        today_str = date.today().isoformat()
        if self._date_str != today_str:
            self._date_str = today_str
            self._gemini_requests = 0
            self._gemini_tokens = 0
            self._tts_requests = 0
            self._tts_characters = 0
            self._warning_notified_today = False
            self._save()

    def record_llm_request(self, tokens: int = 0) -> None:
        """Record a completed Gemini LLM turn."""
        with self._lock:
            self._check_rollover()
            self._gemini_requests += 1
            self._gemini_tokens += max(0, tokens)
            self._save()

    def record_tts_request(self, characters: int = 0) -> None:
        """Record a completed TTS generation."""
        with self._lock:
            self._check_rollover()
            self._tts_requests += 1
            self._tts_characters += max(0, characters)
            self._save()

    def get_status(self) -> dict[str, Any]:
        """Compute current quota consumption, throttling status, and model suggestions."""
        with self._lock:
            self._check_rollover()

            gemini_pct = (self._gemini_requests / self.gemini_cap) if self.gemini_cap > 0 else 0.0
            tts_pct = (self._tts_characters / self.tts_cap) if self.tts_cap > 0 else 0.0
            max_pct = max(gemini_pct, tts_pct)

            is_throttled = max_pct >= _THROTTLE_THRESHOLD

            # Check if user notification should trigger
            should_notify = False
            if is_throttled and not self._warning_notified_today:
                should_notify = True
                self._warning_notified_today = True
                self._save()

            active_model = GEMINI_COMMAND_MODEL if is_throttled else GEMINI_CHAT_MODEL

            return {
                "date": self._date_str,
                "gemini": {
                    "requests": self._gemini_requests,
                    "tokens": self._gemini_tokens,
                    "cap": self.gemini_cap,
                    "percent": round(gemini_pct * 100, 1),
                },
                "tts": {
                    "requests": self._tts_requests,
                    "characters": self._tts_characters,
                    "cap": self.tts_cap,
                    "percent": round(tts_pct * 100, 1),
                },
                "is_throttled": is_throttled,
                "should_notify": should_notify,
                "active_model": active_model,
                "warning_notified_today": self._warning_notified_today,
            }

    def reset(self) -> None:
        """Reset today's counters (for testing and administrative reset)."""
        with self._lock:
            self._gemini_requests = 0
            self._gemini_tokens = 0
            self._tts_requests = 0
            self._tts_characters = 0
            self._warning_notified_today = False
            self._save()


quota_tracker = QuotaTracker()
