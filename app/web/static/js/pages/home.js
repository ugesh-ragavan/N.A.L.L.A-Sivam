/**
 * app/web/static/js/pages/home.js
 *
 * Home / Talk Screen HUD Controller (JARVIS Mode + Minimal Mode):
 *   • Live Chrono Clock (Time & Date corner readouts).
 *   • Real-time Telemetry (Gemini status, Active engine, Mic level, Latency, Memory count).
 *   • Typewriter effect for floating subtitles (last exchange only).
 *   • Full history drawer with toggle button.
 *   • 1.5-second technical boot sequence overlay on load (skippable with Space or Click).
 *   • Look setting synchronization: "hud" (default) vs "minimal".
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  let clockTimer = null;
  let typewriterTimeout = null;
  let bootSkipped = false;
  let totalExchanges = 0;

  function initHome() {
    setupChronoClock();
    setupControls();
    setupHistoryDrawer();
    setupBootSequence();
    syncLookMode();
    loadInitialGreeting();
    refreshStatusStrip();
  }

  // -------------------------------------------------------------------------
  // 1. Live Chrono Clock
  // -------------------------------------------------------------------------
  function setupChronoClock() {
    function updateClock() {
      const now = new Date();
      const timeStr = now.toTimeString().split(" ")[0]; // HH:MM:SS
      const days = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
      const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
      const dateStr = `${days[now.getDay()]} ${now.getDate()} ${months[now.getMonth()]} ${now.getFullYear()}`;

      const timeEl = document.getElementById("hud-val-time");
      const dateEl = document.getElementById("hud-val-date");
      if (timeEl) timeEl.innerText = timeStr;
      if (dateEl) dateEl.innerText = dateStr;
    }

    if (clockTimer) clearInterval(clockTimer);
    updateClock();
    clockTimer = setInterval(updateClock, 1000);
  }

  // -------------------------------------------------------------------------
  // 2. Look Mode (HUD vs Minimal)
  // -------------------------------------------------------------------------
  async function syncLookMode() {
    let mode = localStorage.getItem("nalla_talk_look") || "hud";
    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const s = await res.json();
        if (s.talk_look) {
          mode = s.talk_look.toLowerCase();
          localStorage.setItem("nalla_talk_look", mode);
        }
      }
    } catch (_) {}

    applyLookMode(mode);
  }

  function applyLookMode(mode) {
    const pageHome = document.getElementById("page-home");
    if (!pageHome) return;

    if (mode === "minimal") {
      pageHome.classList.remove("hud-look");
    } else {
      pageHome.classList.add("hud-look");
    }

    // Resize orb to fit the active mode
    if (window.NallaOrb && window.NallaOrb.resize) {
      window.NallaOrb.resize();
    }
  }

  // -------------------------------------------------------------------------
  // 3. Boot Sequence (1.5 s, skippable)
  // -------------------------------------------------------------------------
  function setupBootSequence() {
    const overlay = document.getElementById("hud-boot-overlay");
    const skipBtn = document.getElementById("hud-boot-skip-btn");
    const pageHome = document.getElementById("page-home");

    if (!overlay) return;

    // Only run if in HUD mode and hasn't booted this session
    const hasBooted = sessionStorage.getItem("nalla_hud_booted");
    if (hasBooted || !pageHome || !pageHome.classList.contains("hud-look")) {
      overlay.style.display = "none";
      return;
    }

    const bootLinesContainer = document.getElementById("hud-boot-lines");
    const lines = [
      "> INITIALIZING NEURAL INTERFACE (GEMINI CORE)... OK",
      "> CALIBRATING 24kHz AUDIO HARDWARE & VAD... OK",
      "> HOLO ARC REACTOR HUD ONLINE.",
      "> ALL SYSTEMS NOMINAL. Yes boss, sollunga!",
    ];

    let currentStep = 0;

    function finishBoot() {
      if (bootSkipped) return;
      bootSkipped = true;
      sessionStorage.setItem("nalla_hud_booted", "true");
      overlay.classList.add("fade-out");
      setTimeout(() => {
        overlay.style.display = "none";
      }, 400);

      // Trigger greeting typewriter
      displayAssistantSubtitle("Yes boss, sollunga! All systems nominal.");
    }

    if (skipBtn) {
      skipBtn.addEventListener("click", finishBoot);
    }

    window.addEventListener("keydown", function skipOnKey(e) {
      if (e.code === "Space" || e.code === "Escape" || e.code === "Enter") {
        if (!bootSkipped && overlay.style.display !== "none") {
          finishBoot();
          window.removeEventListener("keydown", skipOnKey);
        }
      }
    });

    const stepInterval = setInterval(() => {
      if (bootSkipped) {
        clearInterval(stepInterval);
        return;
      }
      if (currentStep < lines.length) {
        if (bootLinesContainer) {
          const div = document.createElement("div");
          div.className = "boot-line";
          div.style.color = currentStep === lines.length - 1 ? "#70d6ff" : "#93c5fd";
          div.style.fontWeight = currentStep === lines.length - 1 ? "700" : "normal";
          div.innerText = lines[currentStep];
          bootLinesContainer.appendChild(div);
        }
        currentStep++;
      } else {
        clearInterval(stepInterval);
        setTimeout(finishBoot, 350);
      }
    }, 280);
  }

  // -------------------------------------------------------------------------
  // 4. Typewriter Effect for Floating Subtitles (Smooth & Reflow-Safe)
  // -------------------------------------------------------------------------
  function displayUserSubtitle(text) {
    const userBox = document.getElementById("hud-sub-user");
    const userText = document.getElementById("hud-sub-user-text");
    if (userBox && userText) {
      userText.textContent = `"${text}"`;
      userBox.style.display = "block";
    }
  }

  function displayAssistantSubtitle(fullText) {
    const assistantTextEl = document.getElementById("hud-sub-assistant-text");
    if (!assistantTextEl) return;

    if (typewriterTimeout) {
      clearTimeout(typewriterTimeout);
      typewriterTimeout = null;
    }

    assistantTextEl.textContent = "";
    let charIndex = 0;
    const textLen = fullText.length;
    // Adapt typing speed: shorter messages type char-by-char, longer messages batch smoothly
    const step = textLen > 120 ? 3 : (textLen > 60 ? 2 : 1);
    const speed = 28;

    function typeNextBatch() {
      if (charIndex < textLen) {
        charIndex = Math.min(textLen, charIndex + step);
        assistantTextEl.textContent = fullText.slice(0, charIndex);
        typewriterTimeout = setTimeout(typeNextBatch, speed);
      }
    }

    typeNextBatch();
  }

  // -------------------------------------------------------------------------
  // 5. History Drawer
  // -------------------------------------------------------------------------
  function setupHistoryDrawer() {
    const toggleBtn = document.getElementById("hud-history-toggle-btn");
    const drawer = document.getElementById("talk-transcript-card");
    const closeBtn = document.getElementById("hud-drawer-close-btn");

    if (toggleBtn && drawer) {
      toggleBtn.addEventListener("click", () => {
        drawer.classList.toggle("show-drawer");
      });
    }

    if (closeBtn && drawer) {
      closeBtn.addEventListener("click", () => {
        drawer.classList.remove("show-drawer");
      });
    }
  }

  function incrementHistoryCount() {
    totalExchanges++;
    const badge = document.getElementById("hud-history-badge");
    if (badge) badge.innerText = totalExchanges;
  }

  // -------------------------------------------------------------------------
  // 6. Push-To-Talk & Voice Controls
  // -------------------------------------------------------------------------
  function setupControls() {
    const pttBtn = document.getElementById("orb-ptt-btn");
    if (pttBtn) {
      let isPointerDown = false;

      pttBtn.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        isPointerDown = true;
        pttBtn.classList.add("is-pressed");
        try {
          pttBtn.setPointerCapture(e.pointerId);
        } catch (_) {}
        if (window.NallaApp && window.NallaApp.sendPTTDown) {
          window.NallaApp.sendPTTDown();
        }
      });

      const handlePointerRelease = (e) => {
        if (isPointerDown) {
          isPointerDown = false;
          pttBtn.classList.remove("is-pressed");
          try {
            pttBtn.releasePointerCapture(e.pointerId);
          } catch (_) {}
          if (window.NallaApp && window.NallaApp.sendPTTUp) {
            window.NallaApp.sendPTTUp();
          }
        }
      };

      pttBtn.addEventListener("pointerup", handlePointerRelease);
      pttBtn.addEventListener("pointercancel", handlePointerRelease);
    }

    const muteBtn = document.getElementById("orb-mute-btn");
    if (muteBtn) {
      muteBtn.addEventListener("click", () => window.NallaApp.toggleMute());
    }

    const stopBtn = document.getElementById("orb-stop-btn");
    if (stopBtn) {
      stopBtn.addEventListener("click", () => window.NallaApp.interruptSpeech());
    }
  }

  async function loadInitialGreeting() {
    try {
      const res = await fetch("/api/personality");
      let greeting = "Yes boss, sollunga!";
      if (res.ok) {
        const data = await res.json();
        const mode = data.mode || "Friend";
        if (mode === "Coimbatore Butler") {
          greeting = "Vanakkamunga! Enna seiyanum?";
        } else if (mode === "Tech Lead") {
          greeting = "Systems nominal. Ready for commands.";
        } else if (mode === "Minimal") {
          greeting = "Online.";
        }
      }

      displayAssistantSubtitle(greeting);
    } catch (_) {}
  }

  // -------------------------------------------------------------------------
  // 7. Telemetry & Corner Readouts
  // -------------------------------------------------------------------------
  async function refreshStatusStrip() {
    try {
      const resSet = await fetch("/api/settings");
      if (resSet.ok) {
        const s = await resSet.json();
        const model = s.chat_model || s.model || "gemini-2.5-flash";
        const engine = s.tts_engine || "gemini-tts";
        const voice = s.tts_voice || "achird";

        const modelEl = document.getElementById("strip-model-val");
        if (modelEl) modelEl.innerText = model;
        const hudModelEl = document.getElementById("hud-val-model");
        if (hudModelEl) hudModelEl.innerText = model;

        const engineEl = document.getElementById("talk-engine-val");
        if (engineEl) engineEl.innerText = engine;
        const hudEngineEl = document.getElementById("hud-val-engine");
        if (hudEngineEl) hudEngineEl.innerText = `${engine} (${voice})`;
      }

      const resMem = await fetch("/api/memories");
      if (resMem.ok) {
        const mems = await resMem.json();
        const hudMemEl = document.getElementById("hud-val-mem");
        if (hudMemEl) hudMemEl.innerText = `${mems.length} FACTS`;
      }
    } catch (_) {}
  }

  function updateMetrics(metrics) {
    if (!metrics) return;
    const hudLatencyEl = document.getElementById("hud-val-latency");
    const latencyChip = document.getElementById("talk-latency-chip");
    const lastLatencyEl = document.getElementById("talk-last-latency");

    const totalMs = metrics.total_ms || metrics.tts_first_ms || null;
    if (totalMs !== null) {
      const formatted = `${Math.round(totalMs)}ms`;
      if (hudLatencyEl) hudLatencyEl.textContent = formatted;
      if (lastLatencyEl) lastLatencyEl.textContent = formatted;
      if (latencyChip) latencyChip.style.display = "inline-flex";
    }
  }

  function onTranscriptEvent(role, text) {
    incrementHistoryCount();
    if (role === "user") {
      displayUserSubtitle(text);
    } else if (role === "assistant") {
      displayAssistantSubtitle(text);
    }
  }

  window.NallaPages.home = {
    init: initHome,
    onActivate: refreshStatusStrip,
    updateMetrics,
    loadInitialGreeting,
    onTranscriptEvent,
    applyLookMode,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initHome);
  } else {
    initHome();
  }
})();
