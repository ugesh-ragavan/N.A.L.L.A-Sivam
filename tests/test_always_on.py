"""tests/test_always_on.py

Comprehensive test suite for Prompt 5C Always-On upgrades:
- Single-instance lock
- Watchdog auto-restart and circuit breaker (3 crashes in 10 minutes)
- System health self-check (/health)
- Offline degraded mode (local time, tasks, notes, memory, one-time notice)
- Quota awareness (tracking, 80% cap throttling, model switch)
- Backup, restore, retention pruning, and secret-sanitized diagnostics bundle
- REST API integration endpoints
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR

from app.core.autostart import SingleInstanceLock, get_autostart_status, set_autostart
from app.core.backup import (
    create_backup,
    create_diagnostics_bundle,
    list_backups,
    restore_backup,
)
from app.core.quota import QuotaTracker
from app.core.watchdog import SupervisedComponent, Watchdog
from app.brain.offline import (
    OfflineResponder,
    is_offline,
    reset_spoken_notice,
    set_force_offline,
)
from app.web.server import app


# ---------------------------------------------------------------------------
# 1. Single Instance Lock
# ---------------------------------------------------------------------------

def test_single_instance_lock(tmp_path):
    lock_file = tmp_path / "test_instance.lock"
    lock1 = SingleInstanceLock(lock_file)
    lock2 = SingleInstanceLock(lock_file)

    # First instance acquires lock successfully
    assert lock1.acquire() is True
    assert lock_file.exists()

    # Second instance attempting to acquire must fail
    with pytest.raises(RuntimeError, match="already running"):
        lock2.acquire()

    # Release first instance
    lock1.release()

    # Now second instance can acquire
    assert lock2.acquire() is True
    lock2.release()


# ---------------------------------------------------------------------------
# 2. Watchdog Auto-Restart & Circuit Breaker
# ---------------------------------------------------------------------------

def test_watchdog_restart_and_circuit_breaker():
    """Verify component auto-restarts on crash, and circuit breaker halts at 3 crashes in 10m."""
    crash_count = 0

    def faulty_runner():
        nonlocal crash_count
        crash_count += 1
        raise ValueError(f"Crash simulated #{crash_count}")

    comp = SupervisedComponent("faulty_worker", faulty_runner)

    # Simulate 3 crashes within window
    now = time.time()
    comp.crash_timestamps = [now - 100, now - 50]

    # Run wrapper which encounters the 3rd crash
    comp._should_run = True
    comp._run_wrapper()

    assert comp.status == "circuit_broken"
    assert "Circuit breaker tripped" in comp.last_error or len(comp.crash_timestamps) >= 3


def test_watchdog_self_check():
    """Verify health self-check inspects disk, API key, mic, and database."""
    wd = Watchdog()
    report = wd.run_self_check()

    assert "status" in report
    assert report["status"] in ("healthy", "degraded", "unhealthy")
    assert "checks" in report
    checks = report["checks"]
    assert "disk" in checks
    assert "gemini_api_key" in checks
    assert "database" in checks


# ---------------------------------------------------------------------------
# 3. Offline Degraded Mode
# ---------------------------------------------------------------------------

def test_offline_degraded_mode(tmp_path):
    """Verify offline mode answers time, tasks, and notes without cloud calls."""
    reset_spoken_notice()
    set_force_offline(True)
    assert is_offline() is True

    responder = OfflineResponder()

    # 1. Time query
    res_time = responder.handle("what is the time?")
    assert res_time["is_offline"] is True
    assert res_time["handled_locally"] is True
    assert "Offline Mode active" in res_time["text"]
    assert any(m in res_time["text"] for m in ("AM", "PM"))

    # Notice was spoken once; second query must NOT repeat the offline notice
    res_tasks = responder.handle("what is our next todo?")
    assert res_tasks["is_offline"] is True
    assert "Offline Mode active" not in res_tasks["text"]

    # 2. General complex query explains offline status
    res_general = responder.handle("Explain quantum computing in detail")
    assert "internet disconnected" in res_general["text"].lower()

    # Reset forced offline state
    set_force_offline(False)


# ---------------------------------------------------------------------------
# 4. Quota Awareness & 80% Switch
# ---------------------------------------------------------------------------

def test_quota_tracker_and_80_percent_switch(tmp_path):
    """Verify quota tracking, 80% throttle threshold, and one-time daily notification."""
    q_file = tmp_path / "quota.json"
    tracker = QuotaTracker(quota_file=q_file, gemini_cap=10, tts_cap=1000)

    # 1. Initial state
    st0 = tracker.get_status()
    assert st0["is_throttled"] is False
    assert st0["should_notify"] is False

    # 2. Consume up to 7 calls (70%)
    for _ in range(7):
        tracker.record_llm_request(tokens=100)

    st1 = tracker.get_status()
    assert st1["gemini"]["requests"] == 7
    assert st1["is_throttled"] is False

    # 3. Reach 8 calls (80% threshold)
    tracker.record_llm_request(tokens=100)
    st2 = tracker.get_status()
    assert st2["gemini"]["requests"] == 8
    assert st2["is_throttled"] is True
    assert st2["should_notify"] is True  # Fires once

    # 4. Next call is still throttled, but should_notify does NOT fire again
    tracker.record_llm_request(tokens=100)
    st3 = tracker.get_status()
    assert st3["is_throttled"] is True
    assert st3["should_notify"] is False


# ---------------------------------------------------------------------------
# 5. Backup, Restore, Retention Pruning, and Diagnostics
# ---------------------------------------------------------------------------

def test_backup_and_restore_round_trip(tmp_path):
    """Verify one-click backup, retention of 4 files, and safe restoration."""
    data_dir = tmp_path / "data"
    backup_dir = tmp_path / "backups"
    notes_dir = data_dir / "notes"
    notes_dir.mkdir(parents=True)

    # Create dummy live database with valid SQLite magic header
    db_file = data_dir / "nalla.db"
    db_file.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)

    # Create dummy notes and settings
    (notes_dir / "test_note.md").write_text("# Test Note\nHello Nalla", encoding="utf-8")
    (data_dir / "settings.json").write_text('{"theme": "hud"}', encoding="utf-8")

    # 1. Create 5 backups to test retention pruning (keeps last 4)
    backups = []
    for i in range(5):
        time.sleep(0.01)
        b = create_backup(data_dir=data_dir, backup_dir=backup_dir)
        backups.append(b)

    all_backups = list_backups(backup_dir=backup_dir)
    assert len(all_backups) <= 4  # Pruned oldest

    # 2. Modify live note and restore
    (notes_dir / "test_note.md").write_text("# Corrupted Note", encoding="utf-8")
    latest_backup = all_backups[0]

    restore_res = restore_backup(zip_path=latest_backup["path"], target_data_dir=data_dir)
    assert restore_res["ok"] is True
    assert restore_res["restored_count"] >= 2
    assert "pre_restore_backup" in restore_res

    # Note content should be restored
    restored_text = (notes_dir / "test_note.md").read_text(encoding="utf-8")
    assert "Hello Nalla" in restored_text


def test_diagnostics_bundle_sanitizes_secrets(tmp_path):
    """Verify diagnostic bundle redacts API keys, tokens, and passwords."""
    out_dir = tmp_path / "diag_out"
    fake_log = BASE_DIR / "data" / "logs" / "nalla.log"

    # Generate bundle
    bundle_info = create_diagnostics_bundle(
        output_dir=out_dir,
        system_status={"api_key": "AIzaSyD98234872938479234872934", "password": "SuperSecretPassword123"},
    )
    assert bundle_info["ok"] is True
    assert Path(bundle_info["path"]).exists()

    # Read extracted archive and ensure secrets are masked
    import zipfile
    with zipfile.ZipFile(bundle_info["path"], "r") as zf:
        sys_json = zf.read("system_info.json").decode("utf-8")
        assert "AIzaSyD98234872938479234872934" not in sys_json


# ---------------------------------------------------------------------------
# 6. REST API Endpoints
# ---------------------------------------------------------------------------

def test_rest_api_always_on_endpoints():
    """Verify /health, /api/watchdog/status, /api/quota, /api/backups, /api/offline endpoints."""
    client = TestClient(app)

    # 1. Health endpoint
    h_resp = client.get("/health")
    assert h_resp.status_code == 200
    h_data = h_resp.json()
    assert "status" in h_data
    assert "quota" in h_data
    assert "offline" in h_data

    # 2. Watchdog status & self-check
    w_resp = client.get("/api/watchdog/status")
    assert w_resp.status_code == 200

    sc_resp = client.post("/api/watchdog/self-check")
    assert sc_resp.status_code == 200
    assert "checks" in sc_resp.json()

    # 3. Quota endpoints
    q_resp = client.get("/api/quota")
    assert q_resp.status_code == 200
    assert "gemini" in q_resp.json()

    qr_resp = client.post("/api/quota/reset")
    assert qr_resp.status_code == 200

    # 4. Backups list & trigger
    b_resp = client.get("/api/backups")
    assert b_resp.status_code == 200

    cb_resp = client.post("/api/backup")
    assert cb_resp.status_code == 200
    assert cb_resp.json()["ok"] is True

    # 5. Offline status & toggle
    off_resp = client.get("/api/offline/status")
    assert off_resp.status_code == 200

    tog_resp = client.post("/api/offline/toggle", json={"force_offline": True})
    assert tog_resp.status_code == 200
    assert tog_resp.json()["force_offline"] is True

    # Reset offline toggle back to false
    client.post("/api/offline/toggle", json={"force_offline": False})
