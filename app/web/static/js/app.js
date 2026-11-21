/**
 * app/web/static/js/app.js
 *
 * Global Application Shell & Router for NALLA SIVAM Control Center.
 * Handles:
 *   • Navigation across 10 modular views.
 *   • Theme switcher (Cyan, Violet, Amber, Green) via CSS variables.
 *   • Global WebSocket (/ws/voice) with auto-reconnection banner.
 *   • Web Audio API synthesized chimes for notifications & approvals.
 *   • Command Palette (Ctrl+K) with fuzzy action execution.
 *   • Keyboard shortcuts (Space = PTT, M = mute, 1-9 = pages).
 *   • Approval modal with risk tiers (AMBER / RED).
 */

(function () {
  "use strict";

  const PAGE_IDS = [
    "home",
    "chat",
    "voice",
    "personality",
    "memory",
    "reminders",
    "devices",
    "performance",
    "activity",
    "settings",
  ];

  let activePage = "home";
  let voiceWebSocket = null;
  let isMuted = false;
  let audioContext = null;
  let soundEnabled = true;

  // ------------------------------------------------------------------------
  // Web Audio Chimes (Zero external sound dependencies)
  // ------------------------------------------------------------------------
  function getAudioContext() {
    if (!audioContext) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) audioContext = new AudioCtx();
    }
    if (audioContext && audioContext.state === "suspended") {
      audioContext.resume();
    }
    return audioContext;
  }

  function playTone(freq, type, duration, delay = 0) {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioContext();
      if (!ctx) return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, ctx.currentTime + delay);

      gain.gain.setValueAtTime(0.001, ctx.currentTime + delay);
      gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + delay + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + duration);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start(ctx.currentTime + delay);
      osc.stop(ctx.currentTime + delay + duration + 0.05);
    } catch (e) {
      // Audio autoplay policy fallback
    }
  }

  function playChime(kind = "info") {
    if (kind === "success") {
      playTone(523.25, "sine", 0.1, 0);       // C5
      playTone(659.25, "sine", 0.15, 0.08);   // E5
    } else if (kind === "alert") {
      playTone(440.0, "triangle", 0.12, 0);    // A4
      playTone(349.23, "triangle", 0.16, 0.1); // F4
    } else if (kind === "approval") {
      playTone(587.33, "sine", 0.1, 0);        // D5
      playTone(880.0, "sine", 0.2, 0.1);       // A5
    } else {
      playTone(600.0, "sine", 0.08, 0);
    }
  }

  // ------------------------------------------------------------------------
  // Router / Page Navigation
  // ------------------------------------------------------------------------
  function switchPage(pageId) {
    if (!PAGE_IDS.includes(pageId)) pageId = "home";
    activePage = pageId;

    // Update nav buttons
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.page === pageId);
    });

    // Update page views
    document.querySelectorAll(".page-view").forEach((view) => {
      view.classList.toggle("active", view.id === `page-${pageId}`);
    });

    window.location.hash = pageId;

    // Trigger page specific activations
    if (window.NallaPages && window.NallaPages[pageId] && window.NallaPages[pageId].onActivate) {
      window.NallaPages[pageId].onActivate();
    }
  }

  // ------------------------------------------------------------------------
  // Theme Switcher (Cyan, Violet, Amber, Green)
  // ------------------------------------------------------------------------
  function setTheme(themeName) {
    const valid = ["cyan", "violet", "amber", "green"];
    const theme = valid.includes(themeName) ? themeName : "cyan";
    document.body.dataset.theme = theme;
    localStorage.setItem("nalla_theme", theme);

    document.querySelectorAll(".theme-dot").forEach((dot) => {
      dot.classList.toggle("active", dot.dataset.themeChoice === theme);
    });
  }

  // ------------------------------------------------------------------------
  // WebSocket Client (/ws/voice)
  // ------------------------------------------------------------------------
  function initVoiceWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${location.host}/ws/voice`;
    const banner = document.getElementById("reconnect-banner");

    voiceWebSocket = new WebSocket(wsUrl);

    voiceWebSocket.onopen = () => {
      if (banner) banner.classList.remove("show");
      updateOnlineStatus(true);
    };

    voiceWebSocket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleVoiceEvent(data);
      } catch (err) {
        console.error("Invalid WS message", err);
      }
    };

    voiceWebSocket.onclose = () => {
      if (banner) banner.classList.add("show");
      updateOnlineStatus(false);
      setTimeout(initVoiceWebSocket, 2000);
    };

    voiceWebSocket.onerror = () => {
      if (banner) banner.classList.add("show");
      updateOnlineStatus(false);
    };
  }

  function handleVoiceEvent(data) {
    if (data.type === "state_change") {
      updateVoiceStateUI(data.state);
      if (data.muted !== undefined) {
        updateMuteUI(data.muted);
      }
    } else if (data.type === "transcript") {
      appendTranscriptLine(data.role, data.text);
    } else if (data.type === "metrics") {
      if (window.NallaPages && window.NallaPages.home && window.NallaPages.home.updateMetrics) {
        window.NallaPages.home.updateMetrics(data.metrics);
      }
    } else if (data.type === "mute_change") {
      updateMuteUI(data.muted);
    } else if (data.type === "approval_request") {
      showApprovalModal(data);
    } else if (data.type === "chime") {
      if (window.NallaVoiceStudio && window.NallaVoiceStudio.playChime) {
        window.NallaVoiceStudio.playChime(data.sound, data.volume || 80);
      }
    } else if (data.type === "notice") {
      if (window.NallaVoiceStudio && window.NallaVoiceStudio.showNotice) {
        window.NallaVoiceStudio.showNotice(data.message);
      }
      showToast(data.message, data.level === "warning" ? "alert" : "info");
    }
  }

  function updateVoiceStateUI(state) {
    const stateEl = document.getElementById("orb-state-label");
    if (stateEl) stateEl.innerText = state;
    if (window.NallaOrb) {
      window.NallaOrb.setState(state);
    }
  }

  function updateMuteUI(muted) {
    isMuted = muted;
    const muteBtn = document.getElementById("orb-mute-btn");
    const micStatus = document.getElementById("strip-mic-status");
    if (muteBtn) {
      muteBtn.innerText = muted ? "🔇 Unmute" : "🎙️ Mute";
      muteBtn.classList.toggle("btn-red", muted);
      muteBtn.classList.toggle("btn-outline", !muted);
    }
    if (micStatus) {
      micStatus.innerText = muted ? "MUTED" : "LIVE";
      micStatus.style.color = muted ? "var(--red)" : "var(--green)";
    }
  }

  function updateOnlineStatus(online) {
    const dot = document.getElementById("conn-dot");
    const text = document.getElementById("conn-text");
    if (dot) dot.style.background = online ? "var(--green)" : "var(--red)";
    if (text) text.innerText = online ? "ONLINE" : "CONNECTING...";
  }

  function appendTranscriptLine(role, text) {
    const box = document.getElementById("live-transcript-content");
    if (!box) return;
    const isUser = role === "user";
    const line = document.createElement("div");
    line.style.marginBottom = "4px";
    line.innerHTML = `<span class="${isUser ? "role-user" : "role-nalla"}">${isUser ? "You" : "Nalla"}:</span> ${escapeHtml(text)}`;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }

  // ------------------------------------------------------------------------
  // Command Palette (Ctrl+K)
  // ------------------------------------------------------------------------
  const PALETTE_COMMANDS = [
    { title: "Home: Centerpiece Orb & Live Status", action: () => switchPage("home"), category: "Navigation" },
    { title: "Chat: Conversation & Tool Inspector", action: () => switchPage("chat"), category: "Navigation" },
    { title: "Voice Studio: Audio Tuning & Preview", action: () => switchPage("voice"), category: "Navigation" },
    { title: "Personality Studio: Sliders & Testing", action: () => switchPage("personality"), category: "Navigation" },
    { title: "Memory: Durable Facts & Preferences", action: () => switchPage("memory"), category: "Navigation" },
    { title: "Reminders & Scheduled Tasks", action: () => switchPage("reminders"), category: "Navigation" },
    { title: "Devices: Hardware & Sensors (Phase 5)", action: () => switchPage("devices"), category: "Navigation" },
    { title: "Performance: Latency Waterfall Telemetry", action: () => switchPage("performance"), category: "Navigation" },
    { title: "Activity Log: Timeline Event History", action: () => switchPage("activity"), category: "Navigation" },
    { title: "Settings: API Keys & Preferences", action: () => switchPage("settings"), category: "Navigation" },
    { title: "Voice: Push-To-Talk (Start Listening)", action: triggerPTT, category: "Voice" },
    { title: "Voice: Toggle Microphone Mute", action: toggleMute, category: "Voice" },
    { title: "Voice: Interrupt / Stop Playback", action: interruptSpeech, category: "Voice" },
    { title: "Voice: Play Speaker Test", action: testSpeaker, category: "Voice" },
    { title: "Voice: Run Microphone Energy Test", action: testMic, category: "Voice" },
    { title: "Theme: Switch to Cyan Accent", action: () => setTheme("cyan"), category: "Theme" },
    { title: "Theme: Switch to Violet Accent", action: () => setTheme("violet"), category: "Theme" },
    { title: "Theme: Switch to Amber Accent", action: () => setTheme("amber"), category: "Theme" },
    { title: "Theme: Switch to Green Accent", action: () => setTheme("green"), category: "Theme" },
  ];

  let selectedPaletteIndex = 0;
  let filteredCommands = [...PALETTE_COMMANDS];

  function openCommandPalette() {
    const modal = document.getElementById("command-palette-modal");
    const input = document.getElementById("palette-search-input");
    if (!modal) return;
    modal.classList.add("open");
    if (input) {
      input.value = "";
      input.focus();
    }
    renderPaletteList(PALETTE_COMMANDS);
  }

  function closeCommandPalette() {
    const modal = document.getElementById("command-palette-modal");
    if (modal) modal.classList.remove("open");
  }

  function renderPaletteList(cmds) {
    filteredCommands = cmds;
    selectedPaletteIndex = 0;
    const list = document.getElementById("palette-results-list");
    if (!list) return;

    if (cmds.length === 0) {
      list.innerHTML = `<li style="padding:14px 18px; color:var(--text-dim); text-align:center;">No matching actions found.</li>`;
      return;
    }

    list.innerHTML = cmds.map((cmd, i) => `
      <li class="palette-item ${i === 0 ? "active" : ""}" data-idx="${i}">
        <span>${escapeHtml(cmd.title)}</span>
        <span class="badge badge-accent">${escapeHtml(cmd.category)}</span>
      </li>
    `).join("");

    list.querySelectorAll(".palette-item").forEach((el) => {
      el.addEventListener("click", () => {
        const idx = parseInt(el.dataset.idx, 10);
        executePaletteAction(idx);
      });
    });
  }

  function executePaletteAction(index) {
    if (filteredCommands[index]) {
      closeCommandPalette();
      filteredCommands[index].action();
    }
  }

  // ------------------------------------------------------------------------
  // Global Actions
  // ------------------------------------------------------------------------
  async function triggerPTT() {
    try {
      await fetch("/api/voice/trigger", { method: "POST" });
      showToast("Listening triggered...", "info");
    } catch (e) {
      showToast("Error triggering voice.", "alert");
    }
  }

  async function toggleMute() {
    try {
      await fetch("/api/voice/mute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ muted: !isMuted }),
      });
      showToast(isMuted ? "Microphone active" : "Microphone muted", "info");
    } catch (e) {
      showToast("Error toggling mute.", "alert");
    }
  }

  async function interruptSpeech() {
    try {
      await fetch("/api/voice/interrupt", { method: "POST" });
      showToast("Interrupted speech playback.", "info");
    } catch (e) {
      showToast("Error interrupting speech.", "alert");
    }
  }

  async function testSpeaker() {
    try {
      await fetch("/api/voice/test-speaker", { method: "POST" });
      showToast("Playing speaker test audio...", "info");
    } catch (e) {
      showToast("Error testing speaker.", "alert");
    }
  }

  async function testMic() {
    showToast("Recording 1.5s to test mic...", "info");
    try {
      const res = await fetch("/api/voice/test-mic", { method: "POST" });
      const data = await res.json();
      showToast(data.message, data.ok ? "success" : "alert");
    } catch (e) {
      showToast("Error testing microphone.", "alert");
    }
  }

  // ------------------------------------------------------------------------
  // Approval Modal (AMBER / RED)
  // ------------------------------------------------------------------------
  function showApprovalModal(data) {
    const modal = document.getElementById("approval-modal");
    if (!modal) return;

    playChime("approval");

    const descEl = document.getElementById("approval-desc");
    const riskEl = document.getElementById("approval-risk-badge");
    const approveBtn = document.getElementById("approval-confirm-btn");
    const denyBtn = document.getElementById("approval-deny-btn");

    if (descEl) descEl.innerText = data.description || "Action confirmation";
    if (riskEl) {
      const isRed = (data.risk || "").toLowerCase() === "red";
      riskEl.innerText = isRed ? "🔴 RED RISK" : "⚠️ AMBER RISK";
      riskEl.className = `badge ${isRed ? "badge-red" : "badge-amber"}`;
    }

    modal.classList.add("open");

    approveBtn.onclick = () => {
      modal.classList.remove("open");
      if (data.id) sendApprovalResponse(data.id, true);
      showToast("Action approved.", "success");
    };

    denyBtn.onclick = () => {
      modal.classList.remove("open");
      if (data.id) sendApprovalResponse(data.id, false);
      showToast("Action denied.", "alert");
    };
  }

  async function sendApprovalResponse(id, approved) {
    try {
      await fetch(`/api/approvals/${id}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved }),
      });
    } catch (e) {
      // Stub endpoint
    }
  }

  // ------------------------------------------------------------------------
  // Toast System
  // ------------------------------------------------------------------------
  function showToast(message, kind = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    playChime(kind);

    const toast = document.createElement("div");
    toast.className = "toast";
    if (kind === "success") toast.style.borderColor = "var(--green)";
    if (kind === "alert") toast.style.borderColor = "var(--red)";

    toast.innerText = message;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateX(50px)";
      toast.style.transition = "all 0.25s";
      setTimeout(() => toast.remove(), 250);
    }, 3200);
  }

  // ------------------------------------------------------------------------
  // Helper Utilities
  // ------------------------------------------------------------------------
  function escapeHtml(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.innerText = str;
    return div.innerHTML;
  }

  function updateClock() {
    const clockEl = document.getElementById("header-clock");
    if (!clockEl) return;
    const now = new Date();
    clockEl.innerText = now.toLocaleTimeString([], { hour12: false });
  }

  // ------------------------------------------------------------------------
  // Keyboard Shortcuts
  // ------------------------------------------------------------------------
  function initKeyboardShortcuts() {
    document.addEventListener("keydown", (e) => {
      const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : "";
      const isInput = activeTag === "input" || activeTag === "textarea" || activeTag === "select";

      // Ctrl+K: Command Palette
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openCommandPalette();
        return;
      }

      // Esc: Close Command Palette or Modals
      if (e.key === "Escape") {
        closeCommandPalette();
        const approval = document.getElementById("approval-modal");
        if (approval) approval.classList.remove("open");
        return;
      }

      // If typing in an input, do not trigger single key hotkeys
      if (isInput) return;

      // Space: Push-To-Talk
      if (e.code === "Space") {
        e.preventDefault();
        triggerPTT();
      }

      // M: Mic Mute
      if (e.key.toLowerCase() === "m") {
        e.preventDefault();
        toggleMute();
      }

      // Number keys 1-9 & 0 for page navigation
      if (e.key >= "1" && e.key <= "9") {
        const pageIdx = parseInt(e.key, 10) - 1;
        if (PAGE_IDS[pageIdx]) {
          switchPage(PAGE_IDS[pageIdx]);
        }
      } else if (e.key === "0") {
        switchPage("settings");
      }
    });

    // Palette search input events
    const paletteInput = document.getElementById("palette-search-input");
    if (paletteInput) {
      paletteInput.addEventListener("input", (e) => {
        const q = e.target.value.toLowerCase().trim();
        const matches = PALETTE_COMMANDS.filter((cmd) =>
          cmd.title.toLowerCase().includes(q) || cmd.category.toLowerCase().includes(q)
        );
        renderPaletteList(matches);
      });

      paletteInput.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          selectedPaletteIndex = (selectedPaletteIndex + 1) % filteredCommands.length;
          highlightPaletteItem();
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          selectedPaletteIndex = (selectedPaletteIndex - 1 + filteredCommands.length) % filteredCommands.length;
          highlightPaletteItem();
        } else if (e.key === "Enter") {
          e.preventDefault();
          executePaletteAction(selectedPaletteIndex);
        }
      });
    }

    function highlightPaletteItem() {
      document.querySelectorAll(".palette-item").forEach((el, i) => {
        el.classList.toggle("active", i === selectedPaletteIndex);
      });
    }
  }

  // ------------------------------------------------------------------------
  // App Bootstrapper
  // ------------------------------------------------------------------------
  function bootApp() {
    // 1. Restore theme
    const savedTheme = localStorage.getItem("nalla_theme") || "cyan";
    setTheme(savedTheme);

    // 2. Set up theme picker clicks
    document.querySelectorAll(".theme-dot").forEach((dot) => {
      dot.addEventListener("click", () => setTheme(dot.dataset.themeChoice));
    });

    // 3. Set up sidebar navigation
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchPage(btn.dataset.page));
    });

    // 4. Set up Command palette close backdrop
    const paletteModal = document.getElementById("command-palette-modal");
    if (paletteModal) {
      paletteModal.addEventListener("click", (e) => {
        if (e.target === paletteModal) closeCommandPalette();
      });
    }

    // 5. Restore page from hash or default home
    const hash = window.location.hash.replace("#", "");
    switchPage(PAGE_IDS.includes(hash) ? hash : "home");

    // 6. Start Clock
    updateClock();
    setInterval(updateClock, 1000);

    // 7. Initialize Canvas Orb
    const orbCanvas = document.getElementById("orb-canvas");
    if (orbCanvas && window.NallaOrb) {
      window.NallaOrb.init(orbCanvas);
    }

    // 8. Connect WebSocket
    initVoiceWebSocket();

    // 9. Attach shortcuts
    initKeyboardShortcuts();

    // 10. Playful initial greeting
    setTimeout(() => {
      showToast("Yes boss, sollunga! Nalla Sivam online.", "success");
    }, 400);
  }

  // DOM Content Loaded
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootApp);
  } else {
    bootApp();
  }

  // Expose global app API
  window.NallaApp = {
    switchPage,
    setTheme,
    showToast,
    triggerPTT,
    toggleMute,
    interruptSpeech,
    testSpeaker,
    testMic,
    showApprovalModal,
    escapeHtml,
    playChime,
    setSoundEnabled: (enabled) => { soundEnabled = enabled; },
  };
})();
