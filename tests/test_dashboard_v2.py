"""tests/test_dashboard_v2.py

Tests for the redesigned Control Center Dashboard:
  • Static files serving (CSS, JS, Orb, Page modules).
  • Chat endpoint POST /api/chat.
  • Persona raw editor endpoints (GET & PUT /api/persona/raw, POST /api/persona/revert).
  • Ephemeral personality test POST /api/personality/test.
  • IoT devices endpoints (GET /api/devices, POST /api/devices/{id}/toggle).
  • Activity log endpoint GET /api/activity.
  • Reminder snooze endpoint POST /api/reminders/{id}/snooze.
  • Danger zone memory clear POST /api/memories/clear.
  • Model settings update POST /api/settings/models.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.web.server import app
from app.memory.store import store

client = TestClient(app)


def test_dashboard_html_and_static_assets():
    # Home dashboard page
    res = client.get("/")
    assert res.status_code == 200
    assert "NALLA" in res.text
    assert "orb-canvas" in res.text

    # Static CSS
    res_css = client.get("/static/css/theme.css")
    assert res_css.status_code == 200
    assert "--accent" in res_css.text

    # Static JS
    res_orb = client.get("/static/js/orb.js")
    assert res_orb.status_code == 200
    assert "NallaOrb" in res_orb.text

    res_app = client.get("/static/js/app.js")
    assert res_app.status_code == 200
    assert "NallaApp" in res_app.text


def test_api_chat():
    res = client.post("/api/chat", json={"message": "hello test", "session_id": "test_web"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "reply" in data
    assert isinstance(data["reply"], str)


def test_persona_raw_editor_and_revert():
    # Read current
    get_res = client.get("/api/persona/raw")
    assert get_res.status_code == 200
    orig_text = get_res.json()["text"]
    assert "NALLA SIVAM" in orig_text

    # Put update
    temp_text = orig_text + "\n<!-- test marker -->"
    put_res = client.put("/api/persona/raw", json={"text": temp_text})
    assert put_res.status_code == 200

    # Revert back
    put_back = client.put("/api/persona/raw", json={"text": orig_text})
    assert put_back.status_code == 200

    revert_res = client.post("/api/persona/revert")
    assert revert_res.status_code == 200


def test_api_personality_test(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr("app.brain.gemini_provider.GeminiProvider.generate", lambda *a, **kw: MagicMock(text="Mocked live test reply"))
    res = client.post("/api/personality/test", json={
        "message": "Nalla!",
        "sarcasm": 9,
        "warmth": 2,
        "tamil_mix": 50,
        "verbosity": "short",
        "mode": "Friend",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "reply" in data
    assert "Sarcasm: 9/10" in data["prompt_line"]


def test_devices_and_toggle():
    res = client.get("/api/devices")
    assert res.status_code == 200
    data = res.json()
    assert "devices" in data
    assert len(data["devices"]) > 0

    first_dev = data["devices"][0]
    dev_id = first_dev["id"]
    orig_state = first_dev["state"]

    toggle_res = client.post(f"/api/devices/{dev_id}/toggle")
    assert toggle_res.status_code == 200
    new_state = toggle_res.json()["state"]
    assert new_state != orig_state


def test_activity_log():
    res = client.get("/api/activity")
    assert res.status_code == 200
    events = res.json()["events"]
    assert isinstance(events, list)
    assert len(events) > 0


def test_reminder_snooze():
    # Create test reminder
    rid = store.save_reminder("Test snooze item", "2026-11-21T12:00:00")
    res = client.post(f"/api/reminders/{rid}/snooze", json={"minutes": 30})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert "12:30:00" in res.json()["new_due_at"]


def test_memory_clear_danger_zone():
    # Must reject without exact confirmation phrase
    bad_res = client.post("/api/memories/clear", json={"confirmation": "wrong"})
    assert bad_res.status_code == 400

    # Add a memory to clear
    store.save_memory("Wipeable test memory", category="fact", importance=1)
    ok_res = client.post("/api/memories/clear", json={"confirmation": "DELETE"})
    assert ok_res.status_code == 200
    assert ok_res.json()["ok"] is True


def test_model_settings_update():
    res = client.post("/api/settings/models", json={
        "chat_model": "gemini-2.0-flash-lite",
        "command_model": "gemini-2.0-flash-lite",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["chat_model"] == "gemini-2.0-flash-lite"


def test_hud_look_and_settings_persistence():
    # Verify index HTML contains HUD elements
    res_index = client.get("/")
    assert res_index.status_code == 200
    html = res_index.text
    assert "hud-look" in html
    assert "hud-boot-overlay" in html
    assert "hud-corner-readout" in html
    assert "hud-subtitles-container" in html
    assert "set-talk-look" in html

    # Verify settings API returns talk_look
    get_res = client.get("/api/settings")
    assert get_res.status_code == 200
    settings_data = get_res.json()
    assert "talk_look" in settings_data

    # Test toggling talk_look via POST /api/settings
    post_res = client.post("/api/settings", json={"talk_look": "minimal"})
    assert post_res.status_code == 200
    assert post_res.json()["talk_look"] == "minimal"

    # Reset back to default hud
    reset_res = client.post("/api/settings", json={"talk_look": "hud"})
    assert reset_res.status_code == 200
    assert reset_res.json()["talk_look"] == "hud"

