/**
 * app/web/static/js/pages/home.js
 *
 * Home Page Controller:
 *   • Drives the Orb centerpiece and live speech transcripts.
 *   • Quick Action chips.
 *   • Status strip telemetry (Model, Mic, Memory count, Today's turn count).
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  function initHome() {
    // Quick action chips
    document.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const action = chip.dataset.action;
        if (action === "remember") {
          window.NallaApp.switchPage("memory");
        } else if (action === "time") {
          window.NallaApp.triggerPTT();
        } else if (action === "reminder") {
          window.NallaApp.switchPage("reminders");
        } else if (action === "chat") {
          window.NallaApp.switchPage("chat");
        }
      });
    });

    // PTT button
    const pttBtn = document.getElementById("orb-ptt-btn");
    if (pttBtn) {
      pttBtn.addEventListener("click", () => window.NallaApp.triggerPTT());
    }

    // Mute button
    const muteBtn = document.getElementById("orb-mute-btn");
    if (muteBtn) {
      muteBtn.addEventListener("click", () => window.NallaApp.toggleMute());
    }

    // Stop button
    const stopBtn = document.getElementById("orb-stop-btn");
    if (stopBtn) {
      stopBtn.addEventListener("click", () => window.NallaApp.interruptSpeech());
    }

    refreshStatusStrip();
  }

  async function refreshStatusStrip() {
    try {
      // 1. Settings (model)
      const resSet = await fetch("/api/settings");
      if (resSet.ok) {
        const s = await resSet.json();
        const modelEl = document.getElementById("strip-model-val");
        if (modelEl) modelEl.innerText = s.chat_model || s.model || "Gemini Flash";
      }

      // 2. Memory count
      const resMem = await fetch("/api/memories");
      if (resMem.ok) {
        const mems = await resMem.json();
        const memEl = document.getElementById("strip-mem-val");
        if (memEl) memEl.innerText = `${mems.length} items`;
      }

      // 3. Perf turn count
      const resPerf = await fetch("/api/perf");
      if (resPerf.ok) {
        const p = await resPerf.json();
        const turnEl = document.getElementById("strip-turns-val");
        if (turnEl) turnEl.innerText = `${(p.records || []).length} turns`;
      }
    } catch (e) {
      // Background telemetry fallback
    }
  }

  function updateMetrics(metrics) {
    refreshStatusStrip();
  }

  window.NallaPages.home = {
    init: initHome,
    onActivate: refreshStatusStrip,
    updateMetrics,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initHome);
  } else {
    initHome();
  }
})();
