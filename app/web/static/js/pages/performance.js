/**
 * app/web/static/js/pages/performance.js
 *
 * Performance Telemetry & Latency Waterfall:
 *   • Real-time turn breakdown: STT, Memory, Prompt, LLM 1st Token, LLM Total, Tools, TTS 1st Audio.
 *   • Averages metrics across all turns.
 *   • Requests today counter and provider status light.
 *   • Clear metrics history.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  function initPerformance() {
    const refreshBtn = document.getElementById("perf-refresh-btn");
    const clearBtn = document.getElementById("perf-clear-btn");

    if (refreshBtn) refreshBtn.addEventListener("click", loadPerfData);
    if (clearBtn) clearBtn.addEventListener("click", clearPerfData);

    loadPerfData();
  }

  async function loadPerfData() {
    const tbody = document.getElementById("perf-table-body");
    const summaryCard = document.getElementById("perf-averages-grid");
    if (!tbody) return;

    try {
      const res = await fetch("/api/perf");
      const data = await res.json();
      const records = data.records || [];

      // Update counters
      const counterEl = document.getElementById("perf-requests-today");
      if (counterEl) counterEl.innerText = `${records.length}`;

      if (records.length === 0) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:var(--text-dim); padding:32px;">No recorded turns yet. Speak or chat to generate telemetry.</td></tr>`;
        return;
      }

      // Compute averages
      const avg = (k) => {
        const v = records.map((r) => r[k] || 0).filter((x) => x > 0);
        return v.length ? Math.round(v.reduce((a, b) => a + b, 0) / v.length) : 0;
      };

      if (summaryCard) {
        summaryCard.innerHTML = `
          <div class="hud-card" style="padding:14px; text-align:center;">
            <div style="font-size:0.75rem; color:var(--text-dim);">AVG STT</div>
            <div style="font-family:var(--font-mono); font-size:1.4rem; font-weight:700; color:var(--accent);">${avg("stt_ms")} ms</div>
          </div>
          <div class="hud-card" style="padding:14px; text-align:center;">
            <div style="font-size:0.75rem; color:var(--text-dim);">AVG MEMORY</div>
            <div style="font-family:var(--font-mono); font-size:1.4rem; font-weight:700; color:var(--green);">${avg("memory_ms")} ms</div>
          </div>
          <div class="hud-card" style="padding:14px; text-align:center;">
            <div style="font-size:0.75rem; color:var(--text-dim);">AVG LLM 1ST</div>
            <div style="font-family:var(--font-mono); font-size:1.4rem; font-weight:700; color:var(--purple);">${avg("llm_first_ms")} ms</div>
          </div>
          <div class="hud-card" style="padding:14px; text-align:center;">
            <div style="font-size:0.75rem; color:var(--text-dim);">AVG TTS 1ST</div>
            <div style="font-family:var(--font-mono); font-size:1.4rem; font-weight:700; color:var(--amber);">${avg("tts_first_ms")} ms</div>
          </div>
          <div class="hud-card" style="padding:14px; text-align:center;">
            <div style="font-size:0.75rem; color:var(--text-dim);">AVG TOTAL TURN</div>
            <div style="font-family:var(--font-mono); font-size:1.4rem; font-weight:700; color:var(--text-bright);">${avg("total_ms")} ms</div>
          </div>
        `;
      }

      // Waterfall Table Rows (reverse chronological)
      tbody.innerHTML = [...records].reverse().map((r) => {
        const fp = r.fast_path ? `<span class="badge badge-green">FAST-PATH</span>` : `<span class="badge badge-accent">FULL LLM</span>`;
        const fmt = (v) => v > 0 ? `<span style="font-family:var(--font-mono);">${Math.round(v)} ms</span>` : `—`;

        return `
          <tr>
            <td style="font-family:var(--font-mono); font-size:0.8rem; color:var(--text-dim);">#${r.turn_id}</td>
            <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-dim);">${r.ts_human}</td>
            <td style="max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${window.NallaApp.escapeHtml(r.user_text)}">
              ${window.NallaApp.escapeHtml(r.user_text)}
            </td>
            <td>${fp}</td>
            <td>${fmt(r.stt_ms)}</td>
            <td>${fmt(r.memory_ms)}</td>
            <td>${fmt(r.llm_first_ms)}</td>
            <td>${fmt(r.tts_first_ms)}</td>
            <td style="font-weight:700; color:${r.total_ms > 2000 ? "var(--red)" : r.total_ms > 800 ? "var(--amber)" : "var(--green)"}; font-family:var(--font-mono);">
              ${Math.round(r.total_ms)} ms
            </td>
          </tr>
        `;
      }).join("");
    } catch (e) {
      console.error("Failed loading performance data", e);
    }
  }

  async function clearPerfData() {
    try {
      await fetch("/api/perf", { method: "DELETE" });
      window.NallaApp.showToast("Performance records cleared.", "info");
      loadPerfData();
    } catch (e) {
      window.NallaApp.showToast("Error clearing perf records.", "alert");
    }
  }

  window.NallaPages.performance = {
    init: initPerformance,
    onActivate: loadPerfData,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPerformance);
  } else {
    initPerformance();
  }
})();
