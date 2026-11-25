"""tests/test_app_shell_6f.py

Prompt 6F Verification:
- Command palette modal, shortcuts cheat-sheet modal, last error modal.
- Soft UI sound controls, volume slider, and toggles.
- Keyboard shortcuts coverage (Space, M, Ctrl+K, 1-9, 0, Esc, ?).
- Accessibility attributes (aria-labels, focus-visible styling, tooltips, skeletons, empty states).
- Personality mode switching and voice profile routing.
"""

from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from app.web.server import app

client = TestClient(app)


def test_index_html_contains_modals_and_accessibility():
    """Verify index.html contains all 6F modals, accessibility attributes, and controls."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # 1. Command Palette Modal
    assert 'id="command-palette-modal"' in html
    assert 'id="palette-search-input"' in html
    assert 'id="palette-results-list"' in html

    # 2. Shortcuts Cheat-Sheet Modal
    assert 'id="shortcuts-cheat-sheet-modal"' in html
    assert 'Keyboard Shortcuts &amp; Hotkeys' in html or 'Keyboard Shortcuts & Hotkeys' in html
    assert 'Push-to-Talk' in html

    # 3. Last Error Details Modal
    assert 'id="last-error-modal"' in html
    assert 'id="last-error-content"' in html
    assert 'id="last-error-badge"' in html

    # 4. Sound Controls in Settings
    assert 'id="set-sound-toggle"' in html
    assert 'id="set-sound-volume"' in html
    assert 'id="set-sound-volume-val"' in html

    # 5. Sidebar Navigation Accessibility (1-9 & 0)
    for page in ["home", "chat", "voice", "personality", "memory", "reminders", "devices", "performance", "activity", "settings"]:
        assert f'data-page="{page}"' in html
        assert f'data-tooltip=' in html

    # 6. Shortcuts Help button in header
    assert 'id="shortcuts-help-btn"' in html
    assert 'id="palette-open-btn"' in html


def test_theme_css_has_accessibility_and_skeletons():
    """Verify theme.css includes focus-visible rings, tooltips, skeletons, and empty states."""
    css_path = Path("app/web/static/css/theme.css")
    assert css_path.exists()
    css = css_path.read_text(encoding="utf-8")

    # Focus-visible rings
    assert ":focus-visible" in css
    assert "outline-offset" in css

    # Tooltips
    assert "[data-tooltip]" in css
    assert "content: attr(data-tooltip)" in css

    # Loading skeletons
    assert ".skeleton" in css
    assert "skeletonShimmer" in css

    # Empty states
    assert ".empty-state" in css
    assert ".empty-state-icon" in css
    assert ".empty-state-text" in css

    # Kbd hotkey tags
    assert "kbd {" in css or "kbd{" in css

    # Reduced motion
    assert "prefers-reduced-motion" in css


def test_app_js_exports_and_handlers():
    """Verify app.js defines global handlers for sound, shortcuts, and command palette."""
    js_path = Path("app/web/static/js/app.js")
    assert js_path.exists()
    js = js_path.read_text(encoding="utf-8")

    # Sound chime functions
    assert "playListeningChime" in js
    assert "playDoneChime" in js
    assert "playErrorTone" in js
    assert "setSoundVolume" in js
    assert "setSoundEnabled" in js

    # Command palette and modal functions
    assert "openCommandPalette" in js
    assert "closeCommandPalette" in js
    assert "toggleShortcutsCheatSheet" in js
    assert "openLastErrorModal" in js
    assert "closeLastErrorModal" in js
    assert "setPersonalityMode" in js

    # Shortcuts handling
    assert 'e.key === "Escape"' in js or 'e.key === \'Escape\'' in js
    assert 'e.code === "Space"' in js
    assert 'e.key.toLowerCase() === "m"' in js
    assert 'e.key === "?"' in js


def test_api_endpoints_supporting_6f():
    """Verify backend endpoints powering the command palette and shortcuts."""
    # 1. Voice profiles for dynamic palette injection
    res_prof = client.get("/api/voice/profiles")
    assert res_prof.status_code == 200
    prof_data = res_prof.json()
    assert "profiles" in prof_data
    assert "active" in prof_data

    # 2. Personality mode switching from command palette
    res_mode = client.post("/api/personality", json={"mode": "jarvis"})
    assert res_mode.status_code == 200
    assert res_mode.json().get("ok") is True

    # 3. Settings model preferences
    res_set = client.get("/api/settings")
    assert res_set.status_code == 200
    assert "chat_model" in res_set.json()
