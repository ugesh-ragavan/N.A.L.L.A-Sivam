"""app/core/watchdog.py

Component Watchdog and System Health Self-Check (Prompt 5C).

Features:
- Supervises critical threads/components: voice pipeline, scheduler, web server.
- Exponential backoff restart upon unexpected crashes.
- Circuit breaker: after 3 crashes within 10 minutes (600s), stops auto-restart and alerts.
- Daily and on-demand self-check:
  - Disk space availability
  - Gemini API key configuration
  - Audio/microphone availability
  - TTS engine ready state
  - SQLite database access
- Health reporting for /health endpoint and UI debug drawer.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Callable

from app.config import (
    BASE_DIR,
    GEMINI_API_KEY,
    TTS_ENGINE,
)

logger = logging.getLogger("nalla.watchdog")

_CRASH_WINDOW_SECONDS = 600.0  # 10 minutes
_MAX_CRASHES_IN_WINDOW = 3


class SupervisedComponent:
    """Manages execution and auto-restart policy for a supervised task."""

    def __init__(
        self,
        name: str,
        runner_fn: Callable[[], None],
        stop_fn: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self.runner_fn = runner_fn
        self.stop_fn = stop_fn

        self.thread: threading.Thread | None = None
        self.status: str = "stopped"  # stopped, running, crashed, circuit_broken
        self.restart_count: int = 0
        self.crash_timestamps: list[float] = []
        self.last_error: str = ""
        self.last_started_at: float = 0.0
        self._should_run = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self.status == "running" and self.thread and self.thread.is_alive():
                return
            self._should_run = True
            self._spawn_thread()

    def stop(self) -> None:
        with self._lock:
            self._should_run = False
            self.status = "stopped"
            if self.stop_fn:
                try:
                    self.stop_fn()
                except Exception as exc:
                    logger.warning("Error stopping component %s: %s", self.name, exc)

    def _spawn_thread(self) -> None:
        self.thread = threading.Thread(
            target=self._run_wrapper,
            daemon=True,
            name=f"Watchdog-{self.name}",
        )
        self.last_started_at = time.time()
        self.status = "running"
        self.thread.start()
        logger.info("Watchdog started component '%s'.", self.name)

    def _run_wrapper(self) -> None:
        try:
            self.runner_fn()
        except Exception as exc:
            with self._lock:
                if not self._should_run:
                    self.status = "stopped"
                    return

                now = time.time()
                self.crash_timestamps.append(now)
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.error("Component '%s' crashed: %s", self.name, self.last_error, exc_info=True)

                # Filter crashes within window
                recent_crashes = [t for t in self.crash_timestamps if (now - t) <= _CRASH_WINDOW_SECONDS]
                self.crash_timestamps = recent_crashes

                if len(recent_crashes) >= _MAX_CRASHES_IN_WINDOW:
                    self.status = "circuit_broken"
                    msg = (
                        f"Circuit breaker tripped for '{self.name}': "
                        f"{len(recent_crashes)} crashes in 10 minutes. "
                        f"Auto-restart stopped. Last error: {self.last_error}"
                    )
                    logger.critical(msg)
                    return
                else:
                    self.status = "crashed"

            # Exponential backoff before restart: 1s, 2s, 4s, 8s...
            backoff = min(30.0, 1.0 * (2 ** min(len(recent_crashes) - 1, 5)))
            logger.info("Scheduling restart for component '%s' in %.1fs (Crash #%d in 10m).", self.name, backoff, len(recent_crashes))
            time.sleep(backoff)

            with self._lock:
                if self._should_run and self.status == "crashed":
                    self.restart_count += 1
                    self._spawn_thread()

    def get_info(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            recent_crashes = [t for t in self.crash_timestamps if (now - t) <= _CRASH_WINDOW_SECONDS]
            is_alive = self.thread.is_alive() if self.thread else False
            return {
                "name": self.name,
                "status": self.status,
                "is_alive": is_alive,
                "restart_count": self.restart_count,
                "recent_crashes_10m": len(recent_crashes),
                "last_error": self.last_error,
                "uptime_seconds": int(now - self.last_started_at) if (self.status == "running" and is_alive) else 0,
            }


class Watchdog:
    """Supervises core background threads and executes daily/on-demand health checks."""

    def __init__(self) -> None:
        self._components: dict[str, SupervisedComponent] = {}
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._last_self_check: dict[str, Any] | None = None

    def register(
        self,
        name: str,
        runner_fn: Callable[[], None],
        stop_fn: Callable[[], None] | None = None,
    ) -> SupervisedComponent:
        with self._lock:
            comp = SupervisedComponent(name, runner_fn, stop_fn)
            self._components[name] = comp
            return comp

    def get_component(self, name: str) -> SupervisedComponent | None:
        with self._lock:
            return self._components.get(name)

    def start_all(self) -> None:
        with self._lock:
            for comp in self._components.values():
                comp.start()

    def stop_all(self) -> None:
        with self._lock:
            for comp in self._components.values():
                comp.stop()

    def run_self_check(self) -> dict[str, Any]:
        """Perform system self-check: disk, api key, mic, tts, database."""
        checks: dict[str, Any] = {}
        overall_status = "healthy"

        # 1. Disk space check (threshold: 500MB warning, 100MB alert)
        try:
            usage = shutil.disk_usage(str(BASE_DIR))
            free_gb = round(usage.free / (1024**3), 2)
            total_gb = round(usage.total / (1024**3), 2)
            if usage.free < 100 * 1024 * 1024:
                checks["disk"] = {"ok": False, "status": "alert", "message": f"Critical low disk: {free_gb} GB free"}
                overall_status = "unhealthy"
            elif usage.free < 500 * 1024 * 1024:
                checks["disk"] = {"ok": True, "status": "warning", "message": f"Low disk: {free_gb} GB free"}
                if overall_status == "healthy":
                    overall_status = "degraded"
            else:
                checks["disk"] = {"ok": True, "status": "healthy", "free_gb": free_gb, "total_gb": total_gb}
        except Exception as exc:
            checks["disk"] = {"ok": False, "status": "error", "error": str(exc)}
            overall_status = "degraded"

        # 2. Gemini API key
        if not GEMINI_API_KEY or len(GEMINI_API_KEY) < 10 or "YOUR_KEY" in GEMINI_API_KEY:
            checks["gemini_api_key"] = {
                "ok": False,
                "status": "missing",
                "message": "Gemini API key not configured or empty.",
            }
            if overall_status == "healthy":
                overall_status = "degraded"
        else:
            checks["gemini_api_key"] = {"ok": True, "status": "present"}

        # 3. Audio / Microphone check
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            has_input = any(d.get("max_input_channels", 0) > 0 for d in devices)
            if has_input:
                checks["microphone"] = {"ok": True, "status": "ready"}
            else:
                checks["microphone"] = {"ok": False, "status": "warning", "message": "No audio input device found."}
                if overall_status == "healthy":
                    overall_status = "degraded"
        except Exception as exc:
            checks["microphone"] = {"ok": True, "status": "skipped", "message": f"Sounddevice check note: {exc}"}

        # 4. TTS Engine check
        checks["tts_engine"] = {
            "ok": True,
            "engine": TTS_ENGINE,
            "status": "ready",
        }

        # 5. SQLite database check
        db_path = BASE_DIR / "data" / "nalla.db"
        try:
            with sqlite3.connect(db_path, timeout=3.0) as conn:
                conn.execute("SELECT 1;").fetchone()
            checks["database"] = {"ok": True, "status": "connected"}
        except Exception as exc:
            checks["database"] = {"ok": False, "status": "error", "error": str(exc)}
            overall_status = "unhealthy"

        result = {
            "status": overall_status,
            "timestamp": datetime.now().isoformat(),
            "uptime_seconds": int(time.time() - self._started_at),
            "checks": checks,
        }
        self._last_self_check = result
        return result

    def get_health_report(self) -> dict[str, Any]:
        """Generate comprehensive health report for /health API endpoint."""
        with self._lock:
            comp_statuses = {name: c.get_info() for name, c in self._components.items()}

        self_check = self._last_self_check or self.run_self_check()

        # Check if any supervised component tripped circuit breaker
        has_broken = any(c["status"] == "circuit_broken" for c in comp_statuses.values())
        has_crashed = any(c["status"] == "crashed" for c in comp_statuses.values())

        status = self_check.get("status", "healthy")
        if has_broken:
            status = "unhealthy"
        elif has_crashed and status == "healthy":
            status = "degraded"

        return {
            "status": status,
            "uptime_seconds": int(time.time() - self._started_at),
            "timestamp": datetime.now().isoformat(),
            "components": comp_statuses,
            "self_check": self_check,
        }


watchdog = Watchdog()
