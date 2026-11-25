# Parked Dashboard Pages & Components

This directory preserves dashboard modules and views that were removed from the active 3-screen interface as part of **Prompt B (Minimal Dashboard)**.

No code has been deleted. Everything here remains functional and can be restored at any time.

---

## Parked Files

1. **`pages/devices.js`**
   - **Original Purpose:** IoT device status, relay switches, sensor gauges (Phase 5 hardware).
   - **Reason Parked:** Hardware / IoT development is currently on hold.
   - **How to Restore:**
     1. Copy `pages/devices.js` back to `app/web/static/js/pages/devices.js`.
     2. Add the `<script src="/static/js/pages/devices.js"></script>` tag to `index.html`.
     3. Add a navigation link or tab targeting `#devices`.
     4. Add `<section id="page-devices" class="page-view">` back to `index.html`.
     5. The backend endpoints (`GET /api/devices`, `POST /api/devices/{id}/toggle`) remain active and functional in `app/web/server.py`.

2. **`pages/performance.js`**
   - **Original Purpose:** Dedicated full-page telemetry waterfall and latency metrics.
   - **Reason Parked:** Replaced by the slide-over **Debug Drawer** (`D` hotkey or drawer toggle button) on all screens, which displays the last 10 turns with latency breakdown (`release_to_first_audio_ms`, STT, LLM, TTS), fallback events, and quota counters.
   - **How to Restore:**
     1. Copy `pages/performance.js` back to `app/web/static/js/pages/performance.js`.
     2. Add the script tag to `index.html`.
     3. Add a page section `#page-performance` and navigation button.

3. **`pages/activity.js`**
   - **Original Purpose:** Dedicated event history log and timeline.
   - **Reason Parked:** Streamlined into the **Debug Drawer** logs tab and real-time toast/error recording.
   - **How to Restore:**
     1. Copy `pages/activity.js` back to `app/web/static/js/pages/activity.js`.
     2. Add the script tag to `index.html`.
     3. The backend endpoint `GET /api/activity` remains active in `app/web/server.py`.

4. **Multi-Accent Themes & Particle Effects**
   - **Original Purpose:** Four accent color variations (Cyan, Violet, Amber, Green) and animated cyber grid overlays.
   - **Reason Parked:** Replaced by a clean, high-contrast Dark theme (default) and Light theme with reduced visual noise, respecting `prefers-reduced-motion`.
   - **How to Restore:**
     1. CSS variables for all palettes remain defined in `theme.css`.
     2. Set `document.body.dataset.theme = 'cyan' | 'violet' | 'amber' | 'green'`.

---

## Active Architecture (Prompt B: 3 Screens)

1. **TALK** (`#talk` / default):
   - Small calm Orb centerpiece (IDLE, LISTENING, THINKING, SPEAKING).
   - Status chips: Gemini status, Mic status, Active voice engine.
   - 20-message scrollable conversation transcript.
   - Big circular HOLD TO TALK button (with Spacebar hint).
   - Text input box (`/` shortcut to focus) + Send button.
   - Stop button (`Esc` shortcut).

2. **MEMORY** (`#memory`):
   - Two sub-tabs: **Memories** & **Reminders**.
   - Search filter, inline edit, delete with confirmation, add form, JSON export.

3. **SETTINGS** (`#settings`):
   - Grouped sections with search bar filter:
     - **Voice**: Engine, English voice, Tamil voice, speed, preview button, link to Voice Lab.
     - **Personality**: Sarcasm, Warmth, Tamil mix sliders, mode dropdown, Advanced persona text editor, locked Identity Guard cards.
     - **Talk Mode**: Walkie-talkie mode, Tap-to-toggle, Global hotkey settings.
     - **Models**: Chat LLM model, STT model.
     - **API Key**: Masked API key input with reveal/save.
     - **Data**: JSON export and import.

4. **DEBUG DRAWER** (`D` hotkey or icon toggle):
   - Slide-over panel containing:
     - Last 10 turns latency waterfall (`release_to_first_audio_ms`, STT, LLM, TTS).
     - System errors and recent activity logs.
     - Quota counters for LLM and TTS.
     - Engine fallback event telemetry.
