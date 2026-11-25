/**
 * app/web/static/js/pages/devices.js
 *
 * ESP32 / ESP8266 IoT Devices Controller (Phase 5 ready stub):
 *   • Device cards with online/offline status dot and last-seen timestamp.
 *   • Sensor gauges and SVG sparklines for live telemetry.
 *   • Control toggles showing pending -> confirmed ACK states.
 *   • Command execution history table.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  let devicesData = [];

  function initDevices() {
    loadDevices();
  }

  async function loadDevices() {
    const container = document.getElementById("devices-cards-container");
    const historyBody = document.getElementById("devices-history-body");
    if (!container) return;

    try {
      const res = await fetch("/api/devices");
      const data = await res.json();
      devicesData = data.devices || [];

      // Render Device Cards
      if (devicesData.length === 0) {
        container.innerHTML = `
          <div class="empty-state" style="grid-column: 1/-1;">
            <div class="empty-state-icon">📡</div>
            <div class="empty-state-text">No IoT devices registered on the local network. ESP devices will appear here once connected.</div>
          </div>
        `;
      } else {
        container.innerHTML = devicesData.map((d) => {
        const isOnline = d.online;
        const isOn = d.state === "on";
        return `
          <div class="hud-card hud-bracket" style="padding:18px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
              <div>
                <div style="font-weight:600; color:var(--text-bright); font-size:1rem;">${window.NallaApp.escapeHtml(d.name)}</div>
                <div style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-dim);">ID: ${d.id} • ${d.last_seen}</div>
              </div>
              <div style="display:flex; align-items:center; gap:6px;">
                <span style="width:8px; height:8px; border-radius:50%; background:${isOnline ? "var(--green)" : "var(--red)"}; box-shadow:0 0 8px ${isOnline ? "var(--green)" : "var(--red)"};"></span>
                <span style="font-family:var(--font-mono); font-size:0.72rem; color:${isOnline ? "var(--green)" : "var(--red)"};">${isOnline ? "ONLINE" : "OFFLINE"}</span>
              </div>
            </div>

            ${d.type === "sensor" ? `
              <!-- Climate Sensor Telemetry & Sparkline -->
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:12px;">
                <div style="background:rgba(0,0,0,0.3); padding:10px; border-radius:var(--radius-sm); border:1px solid var(--border-subtle); text-align:center;">
                  <div style="font-size:0.72rem; color:var(--text-dim);">TEMPERATURE</div>
                  <div style="font-family:var(--font-mono); font-size:1.4rem; font-weight:700; color:var(--accent);">${d.temp}°C</div>
                </div>
                <div style="background:rgba(0,0,0,0.3); padding:10px; border-radius:var(--radius-sm); border:1px solid var(--border-subtle); text-align:center;">
                  <div style="font-size:0.72rem; color:var(--text-dim);">HUMIDITY</div>
                  <div style="font-family:var(--font-mono); font-size:1.4rem; font-weight:700; color:var(--green);">${d.humidity}%</div>
                </div>
              </div>
              <!-- Mini SVG Sparkline -->
              <div style="height:36px; width:100%; margin-bottom:12px;">
                <svg viewBox="0 0 200 36" style="width:100%; height:100%; overflow:visible;">
                  <path d="M 0,25 Q 30,10 60,20 T 120,8 T 160,22 T 200,14" fill="none" stroke="var(--accent)" stroke-width="2" />
                  <path d="M 0,25 Q 30,10 60,20 T 120,8 T 160,22 T 200,14 L 200,36 L 0,36 Z" fill="var(--accent-dim)" opacity="0.3" />
                </svg>
              </div>
            ` : `
              <!-- Relay / Switch Control -->
              <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.25); padding:12px 14px; border-radius:var(--radius-sm); border:1px solid var(--border-subtle); margin-bottom:14px;">
                <div>
                  <div style="font-size:0.8rem; color:var(--text-dim);">LOAD CONSUMPTION</div>
                  <div style="font-family:var(--font-mono); font-weight:600; color:var(--text-bright);">${isOn ? `${d.load_w || 40} W` : "0 W (Idle)"}</div>
                </div>
                <button class="btn btn-sm ${isOn ? "btn-green" : "btn-outline"}" id="btn-toggle-${d.id}" onclick="window.NallaPages.devices.toggle('${d.id}')">
                  ${isOn ? "⚡ ACTIVE (ON)" : "○ STANDBY (OFF)"}
                </button>
              </div>
            `}

            <div style="font-family:var(--font-mono); font-size:0.72rem; color:var(--text-dim); display:flex; justify-content:space-between;">
              <span>MQTT Topic: nalla/esp32/${d.id}</span>
              <span>QoS 1</span>
            </div>
          </div>
        `;
      }).join("");
      }

      // Render History Table
      if (historyBody && data.history) {
        historyBody.innerHTML = data.history.map((h) => `
          <tr>
            <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-dim);">${h.time}</td>
            <td style="font-weight:600; color:var(--text-bright);">${h.device}</td>
            <td><code>${h.command}</code></td>
            <td><span class="badge ${h.status === "ACK_CONFIRMED" ? "badge-green" : "badge-amber"}">${h.status}</span></td>
            <td style="font-family:var(--font-mono); font-size:0.75rem;">${h.latency_ms} ms</td>
          </tr>
        `).join("");
      }
    } catch (e) {
      console.error("Failed loading devices", e);
    }
  }

  async function toggleDevice(id) {
    const btn = document.getElementById(`btn-toggle-${id}`);
    if (btn) btn.innerText = "⏳ PENDING...";

    try {
      const res = await fetch(`/api/devices/${id}/toggle`, { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        window.NallaApp.showToast(`Device '${id}' toggled to ${data.state.toUpperCase()}.`, "success");
        loadDevices();
      } else {
        window.NallaApp.showToast("Failed to toggle device.", "alert");
        loadDevices();
      }
    } catch (e) {
      window.NallaApp.showToast("Network error.", "alert");
      loadDevices();
    }
  }

  window.NallaPages.devices = {
    init: initDevices,
    onActivate: loadDevices,
    toggle: toggleDevice,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDevices);
  } else {
    initDevices();
  }
})();
