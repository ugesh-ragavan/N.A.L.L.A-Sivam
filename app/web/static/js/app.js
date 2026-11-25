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
  let soundEnabled = localStorage.getItem("nalla_sound") !== "false";
  let soundVolume = parseFloat(localStorage.getItem("nalla_sound_volume") || "0.7");
  if (isNaN(soundVolume) || soundVolume < 0) soundVolume = 0.7;

  let lastErrorRecord = null;
  let previousVoiceState = "IDLE";

  // ------------------------------------------------------------------------
  // Web Audio Chimes (Synthesized, zero external sound files)
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

  function setSoundEnabled(enabled) {
    soundEnabled = !!enabled;
    localStorage.setItem("nalla_sound", soundEnabled ? "true" : "false");
    const toggle = document.getElementById("set-sound-toggle");
    if (toggle) toggle.checked = soundEnabled;
  }

  function setSoundVolume(vol) {
    soundVolume = Math.max(0, Math.min(1, parseFloat(vol)));
    localStorage.setItem("nalla_sound_volume", String(soundVolume));
    const slider = document.getElementById("set-sound-volume");
    const valText = document.getElementById("set-sound-volume-val");
    if (slider) slider.value = Math.round(soundVolume * 100);
    if (valText) valText.innerText = `${Math.round(soundVolume * 100)}%`;
  }

  function getSoundSettings() {
    return { enabled: soundEnabled, volume: soundVolume };
  }

  function playTone(freq, type, duration, delay = 0, gainMultiplier = 1.0) {
    if (!soundEnabled || soundVolume <= 0) return;
    try {
      const ctx = getAudioContext();
      if (!ctx) return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, ctx.currentTime + delay);

      const targetGain = 0.12 * soundVolume * gainMultiplier;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + delay);
      gain.gain.exponentialRampToValueAtTime(targetGain, ctx.currentTime + delay + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + delay + duration);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start(ctx.currentTime + delay);
      osc.stop(ctx.currentTime + delay + duration + 0.05);
    } catch (e) {
      // Audio autoplay policy fallback
    }
  }

  function playListeningChime() {
    // Rising harmonic ping: C5 (523.25 Hz) -> G5 (783.99 Hz)
    playTone(523.25, "sine", 0.09, 0, 0.9);
    playTone(783.99, "sine", 0.14, 0.07, 1.0);
  }

  function playDoneChime() {
    // Two-tone soft confirmation: F5 (698.46 Hz) -> A5 (880 Hz)
    playTone(698.46, "sine", 0.08, 0, 0.85);
    playTone(880.0, "sine", 0.16, 0.06, 1.0);
  }

  function playErrorTone() {
    // Soft low dual tone
    playTone(320.0, "triangle", 0.12, 0, 1.1);
    playTone(220.0, "sawtooth", 0.18, 0.09, 0.8);
  }

  function playApprovalChime() {
    playTone(587.33, "sine", 0.1, 0, 1.0); // D5
    playTone(880.0, "sine", 0.22, 0.09, 1.1); // A5
  }

  function playChime(kind = "info") {
    if (!soundEnabled) return;
    if (kind === "listening") {
      playListeningChime();
    } else if (kind === "done") {
      playDoneChime();
    } else if (kind === "alert" || kind === "error") {
      playErrorTone();
    } else if (kind === "approval") {
      playApprovalChime();
    } else if (kind === "success") {
      playTone(523.25, "sine", 0.09, 0, 0.9);
      playTone(659.25, "sine", 0.14, 0.08, 1.0);
    } else {
      playTone(600.0, "sine", 0.08, 0, 0.7);
    }
  }

  // ------------------------------------------------------------------------
  // Router / Page Navigation
  // ------------------------------------------------------------------------
  function switchPage(pageId) {
    if (pageId === "talk") pageId = "home";
    if (!PAGE_IDS.includes(pageId)) pageId = "home";
    activePage = pageId;

    // Update nav buttons (both sidebar and top minimal nav)
    document.querySelectorAll(".nav-btn, .nav-tab-pill").forEach((btn) => {
      const match = btn.dataset.page === pageId || (pageId === "home" && btn.dataset.page === "talk");
      btn.classList.toggle("active", match);
      if (btn.hasAttribute("aria-selected")) {
        btn.setAttribute("aria-selected", match ? "true" : "false");
      }
    });

    // Update page views
    document.querySelectorAll(".page-view").forEach((view) => {
      view.classList.toggle("active", view.id === `page-${pageId}`);
    });

    window.location.hash = pageId === "home" ? "talk" : pageId;

    // Trigger page specific activations
    if (window.NallaPages && window.NallaPages[pageId] && window.NallaPages[pageId].onActivate) {
      window.NallaPages[pageId].onActivate();
    }
    if (pageId === "memory" && window.NallaPages && window.NallaPages.reminders && window.NallaPages.reminders.onActivate) {
      window.NallaPages.reminders.onActivate();
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

    if (window.NallaOrb && window.NallaOrb.updateTheme) {
      window.NallaOrb.updateTheme();
    }
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
      const newState = data.state;
      if (newState === "LISTENING" && previousVoiceState !== "LISTENING") {
        playListeningChime();
      } else if (previousVoiceState === "SPEAKING" && newState === "IDLE") {
        playDoneChime();
      }
      previousVoiceState = newState;

      updateVoiceStateUI(newState, data.follow_up, data.timeout);
      if (data.muted !== undefined) {
        updateMuteUI(data.muted);
      }
    } else if (data.type === "transcript") {
      appendTranscriptLine(data.role, data.text);
    } else if (data.type === "metrics") {
      if (window.NallaPages && window.NallaPages.home && window.NallaPages.home.updateMetrics) {
        window.NallaPages.home.updateMetrics(data.metrics);
      }
      loadDebugTurns();
    } else if (data.type === "mute_change") {
      updateMuteUI(data.muted);
    } else if (data.type === "approval_request") {
      showApprovalModal(data);
    } else if (data.type === "error") {
      recordError(data.message || "Voice subsystem error", data.subsystem || "Voice Core");
      showToast(data.message || "Voice system error", "alert");
    } else if (data.type === "chime") {
      if (data.sound === "listening") playListeningChime();
      else if (data.sound === "done") playDoneChime();
      else if (window.NallaVoiceStudio && window.NallaVoiceStudio.playChime) {
        window.NallaVoiceStudio.playChime(data.sound, data.volume || 80);
      } else {
        playChime(data.sound);
      }
    } else if (data.type === "notice") {
      if (data.level === "warning" || data.level === "error") {
        recordError(data.message, "System Notice");
      } else {
        addDebugLog(data.message, "info");
      }
      if (window.NallaVoiceStudio && window.NallaVoiceStudio.showNotice) {
        window.NallaVoiceStudio.showNotice(data.message);
      }
      showToast(data.message, data.level === "warning" || data.level === "error" ? "alert" : "info");
    } else if (data.type === "tts_fallback") {
      const msg = data.notice || `TTS fallback: ${data.primary_engine} -> ${data.fallback_engine} (${data.reason})`;
      recordError(msg, "TTS Engine");
      addDebugFallback(data);
      if (window.NallaVoiceStudio && window.NallaVoiceStudio.showNotice) {
        window.NallaVoiceStudio.showNotice(msg);
      }
      showToast(msg, "alert");
    } else if (data.type === "ptt_hint") {
      showToast(data.message || "Hold Space to talk", "info");
      const timerEl = document.getElementById("ptt-hold-timer");
      if (timerEl) {
        timerEl.innerText = data.message || "Hold Space to talk";
        setTimeout(() => {
          if (!isSpaceHolding && !isPointerHolding && timerEl) {
            timerEl.innerText = "Hold Space or press & hold button";
          }
        }, 2500);
      }
    } else if (data.type === "screen_preview_request") {
      if (window.openScreenGateModal) {
        window.openScreenGateModal(data.capture_id, data.thumbnail, data.query, data.width, data.height);
      }
    } else if (data.type === "ptt_level") {
      const timerEl = document.getElementById("ptt-hold-timer");
      if (timerEl && (isSpaceHolding || isPointerHolding)) {
        timerEl.innerText = `🎙️ ${data.elapsed_s}s (Release to send) · RMS: ${data.rms}`;
      }
      const norm = Math.min(1.0, Math.max(0.15, (data.rms || 0) / 900.0));
      if (window.NallaOrb) {
        window.NallaOrb.setState("LISTENING", norm);
      }
      const hudMic = document.getElementById("hud-val-mic");
      if (hudMic) {
        hudMic.innerText = `LIVE · ${Math.round(norm * 100)}%`;
      }
    } else if (data.type === "speech_level") {
      const norm = Math.min(1.0, Math.max(0.35, (data.rms || 0) / 1200.0));
      if (window.NallaOrb) {
        window.NallaOrb.setState("SPEAKING", norm);
      }
    } else if (data.type === "click_sound") {
      playClickSound(data.action);
    }
  }

  let countdownTimerId = null;
  let chatRevertTimeout = null;

  function updateVoiceStateUI(state, followUp = false, timeout = 30) {
    const stateEl = document.getElementById("orb-state-label");
    if (stateEl) stateEl.innerText = state;

    const homePage = document.getElementById("page-home");
    if (homePage) {
      homePage.classList.toggle("is-listening", state === "LISTENING");
      homePage.classList.toggle("is-speaking", state === "SPEAKING");
      homePage.classList.toggle("is-thinking", state === "THINKING");
    }

    if (window.NallaOrb) {
      const defLevel = state === "SPEAKING" ? 0.75 : (state === "LISTENING" ? 0.4 : (state === "THINKING" ? 0.3 : 0.0));
      window.NallaOrb.setState(state, defLevel);
    }

    const countdownEl = document.getElementById("orb-countdown");
    if (countdownTimerId) {
      clearInterval(countdownTimerId);
      countdownTimerId = null;
    }

    if (state === "LISTENING" && followUp && countdownEl) {
      let remaining = Math.round(timeout || 30);
      countdownEl.innerText = `⏱️ ${remaining}s`;
      countdownEl.style.display = "inline-block";
      countdownTimerId = setInterval(() => {
        remaining -= 1;
        if (remaining <= 0) {
          clearInterval(countdownTimerId);
          countdownTimerId = null;
          countdownEl.style.display = "none";
        } else {
          countdownEl.innerText = `⏱️ ${remaining}s`;
        }
      }, 1000);
    } else if (countdownEl) {
      countdownEl.style.display = "none";
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
    if (window.NallaPages && window.NallaPages.home && window.NallaPages.home.onTranscriptEvent) {
      window.NallaPages.home.onTranscriptEvent(role, text);
    }
    const box = document.getElementById("live-transcript-content");
    if (!box) return;
    const isUser = role === "user";
    const line = document.createElement("div");
    line.className = "transcript-msg";
    line.style.marginBottom = "4px";
    line.innerHTML = `<span class="${isUser ? "role-user" : "role-nalla"}">${isUser ? "You" : "Nalla"}:</span> ${escapeHtml(text)}`;
    box.appendChild(line);

    // Prune so only last 20 messages are retained
    const msgs = box.querySelectorAll(".transcript-msg");
    if (msgs.length > 20) {
      for (let i = 0; i < msgs.length - 20; i++) {
        msgs[i].remove();
      }
    }
    box.scrollTop = box.scrollHeight;
  }

  // ------------------------------------------------------------------------
  // Command Palette (Ctrl+K) & Global System Modals
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
    
    { title: "Reminders: Create / Quick Add New Reminder", action: () => {
        switchPage("reminders");
        setTimeout(() => {
          const inp = document.getElementById("rem-text-input");
          if (inp) { inp.focus(); inp.select(); }
        }, 120);
      }, category: "Actions" },

    { title: "Personality: Switch Mode to 'Friend'", action: () => setPersonalityMode("friend"), category: "Personality" },
    { title: "Personality: Switch Mode to 'Jarvis'", action: () => setPersonalityMode("jarvis"), category: "Personality" },
    { title: "Personality: Switch Mode to 'Focus'", action: () => setPersonalityMode("focus"), category: "Personality" },
    { title: "Personality: Switch Mode to 'Engineer'", action: () => setPersonalityMode("engineer"), category: "Personality" },

    { title: "Theme: Switch to Cyan Accent", action: () => setTheme("cyan"), category: "Theme" },
    { title: "Theme: Switch to Violet Accent", action: () => setTheme("violet"), category: "Theme" },
    { title: "Theme: Switch to Amber Accent", action: () => setTheme("amber"), category: "Theme" },
    { title: "Theme: Switch to Green Accent", action: () => setTheme("green"), category: "Theme" },

    { title: "System: Open Last Error Log", action: openLastErrorModal, category: "System" },
    { title: "Help: Show Keyboard Shortcuts Cheat-sheet", action: () => toggleShortcutsCheatSheet(true), category: "Help" },
    { title: "Action: Trigger AMBER Approval Test", action: () => { switchPage("chat"); fetch("/api/test/amber", {method:"POST"}).catch(()=>{}); }, category: "Actions" },
  ];

  let selectedPaletteIndex = 0;
  let currentPalettePool = [...PALETTE_COMMANDS];
  let filteredCommands = [...PALETTE_COMMANDS];

  async function openCommandPalette() {
    const modal = document.getElementById("command-palette-modal");
    const input = document.getElementById("palette-search-input");
    if (!modal) return;
    modal.classList.add("open");
    if (input) {
      input.value = "";
      input.focus();
    }
    currentPalettePool = await getDynamicPaletteCommands();
    renderPaletteList(currentPalettePool);
  }

  function closeCommandPalette() {
    const modal = document.getElementById("command-palette-modal");
    if (modal) modal.classList.remove("open");
  }

  function recordError(message, source = "System") {
    lastErrorRecord = {
      timestamp: new Date().toLocaleTimeString(),
      message: String(message),
      source: source,
    };
    addDebugLog(`[${source}] ${message}`, "error");
  }

  function openLastErrorModal() {
    const modal = document.getElementById("last-error-modal");
    const content = document.getElementById("last-error-content");
    const timeEl = document.getElementById("last-error-time");
    const badgeEl = document.getElementById("last-error-badge");
    if (!modal) return;

    if (lastErrorRecord) {
      if (content) content.innerText = `[${lastErrorRecord.source}] ${lastErrorRecord.message}`;
      if (timeEl) timeEl.innerText = `Occurred at: ${lastErrorRecord.timestamp}`;
      if (badgeEl) {
        badgeEl.innerText = lastErrorRecord.source.toUpperCase();
        badgeEl.className = "badge badge-red";
      }
    } else {
      if (content) content.innerText = "No errors recorded in this session. All systems operational.";
      if (timeEl) timeEl.innerText = `Checked at: ${new Date().toLocaleTimeString()}`;
      if (badgeEl) {
        badgeEl.innerText = "ALL CLEAR";
        badgeEl.className = "badge badge-green";
      }
    }
    modal.classList.add("open");
  }

  function closeLastErrorModal() {
    const modal = document.getElementById("last-error-modal");
    if (modal) modal.classList.remove("open");
  }

  function toggleShortcutsCheatSheet(forceState) {
    const modal = document.getElementById("shortcuts-cheat-sheet-modal");
    if (!modal) return;
    if (typeof forceState === "boolean") {
      modal.classList.toggle("open", forceState);
    } else {
      modal.classList.toggle("open");
    }
  }

  async function setPersonalityMode(mode) {
    try {
      const res = await fetch("/api/personality", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: mode.toLowerCase() }),
      });
      if (res.ok) {
        showToast(`Switched mode to '${mode}'`, "success");
        playChime("success");
        if (window.NallaPages && window.NallaPages.personality && window.NallaPages.personality.loadPersonality) {
          window.NallaPages.personality.loadPersonality();
        }
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(`Failed to switch mode: ${err.detail || mode}`, "alert");
      }
    } catch (err) {
      showToast(`Error switching mode: ${err.message}`, "alert");
    }
  }

  async function getDynamicPaletteCommands() {
    const cmds = [...PALETTE_COMMANDS];
    try {
      const res = await fetch("/api/voice/profiles");
      if (res.ok) {
        const data = await res.json();
        const profiles = data.profiles || {};
        const activeName = data.active || "";
        Object.keys(profiles).forEach((pName) => {
          const isActive = pName === activeName;
          cmds.push({
            title: `Voice Profile: Switch to '${pName}'${isActive ? " (Active)" : ""}`,
            action: () => switchVoiceProfile(pName),
            category: "Voice Profile",
          });
        });
      }
    } catch (e) {
      // Use static pool fallback
    }
    return cmds;
  }

  async function switchVoiceProfile(name) {
    try {
      const res = await fetch("/api/voice/profiles/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (res.ok) {
        showToast(`Voice Profile switched to: ${name}`, "success");
        playChime("success");
        if (window.NallaVoiceStudio && window.NallaVoiceStudio.reloadProfiles) {
          window.NallaVoiceStudio.reloadProfiles();
        }
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(`Failed to switch profile: ${err.detail || name}`, "alert");
      }
    } catch (err) {
      showToast(`Error switching profile: ${err.message}`, "alert");
    }
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
  // Walkie-Talkie & PTT Actions (Prompt A)
  // ------------------------------------------------------------------------
  let isSpaceHolding = false;
  let isPointerHolding = false;

  function playClickSound(type = "down") {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      if (!audioContext) audioContext = new AudioCtx();
      if (audioContext.state === "suspended") audioContext.resume();

      const osc = audioContext.createOscillator();
      const gain = audioContext.createGain();
      const now = audioContext.currentTime;

      if (type === "down") {
        osc.frequency.setValueAtTime(340, now);
        osc.frequency.exponentialRampToValueAtTime(520, now + 0.025);
        gain.gain.setValueAtTime(0.06, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.035);
        osc.start(now);
        osc.stop(now + 0.035);
      } else {
        osc.frequency.setValueAtTime(520, now);
        osc.frequency.exponentialRampToValueAtTime(280, now + 0.025);
        gain.gain.setValueAtTime(0.06, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.035);
        osc.start(now);
        osc.stop(now + 0.035);
      }
      osc.connect(gain);
      gain.connect(audioContext.destination);
    } catch (_) {}
  }

  function setButtonHoldingVisual(isHolding) {
    const pttBtn = document.getElementById("orb-ptt-btn");
    const labelEl = document.getElementById("ptt-btn-label");
    const timerEl = document.getElementById("ptt-hold-timer");
    if (pttBtn) {
      if (isHolding) {
        pttBtn.classList.add("holding");
        pttBtn.style.transform = "scale(0.96)";
        pttBtn.style.boxShadow = "0 0 24px var(--accent)";
        if (labelEl) labelEl.innerText = "LISTENING... (Release to send)";
        if (timerEl) timerEl.innerText = "🎙️ 0.0s (Release to send)";
      } else {
        pttBtn.classList.remove("holding");
        pttBtn.style.transform = "none";
        pttBtn.style.boxShadow = "none";
        if (labelEl) labelEl.innerText = "HOLD TO TALK";
      }
    }
  }

  function sendPTTDown() {
    isPointerHolding = true;
    updateVoiceStateUI("LISTENING", false, 30);
    if (voiceWebSocket && voiceWebSocket.readyState === WebSocket.OPEN) {
      voiceWebSocket.send(JSON.stringify({ type: "ptt_down" }));
    } else {
      fetch("/api/voice/ptt/down", { method: "POST" }).catch(() => {});
    }
    setButtonHoldingVisual(true);
  }

  function sendPTTUp() {
    isPointerHolding = false;
    isSpaceHolding = false;
    updateVoiceStateUI("THINKING");
    if (voiceWebSocket && voiceWebSocket.readyState === WebSocket.OPEN) {
      voiceWebSocket.send(JSON.stringify({ type: "ptt_up" }));
    } else {
      fetch("/api/voice/ptt/up", { method: "POST" }).catch(() => {});
    }
    setButtonHoldingVisual(false);
  }

  function sendPTTCancel() {
    isPointerHolding = false;
    isSpaceHolding = false;
    updateVoiceStateUI("IDLE");
    if (voiceWebSocket && voiceWebSocket.readyState === WebSocket.OPEN) {
      voiceWebSocket.send(JSON.stringify({ type: "ptt_cancel" }));
    } else {
      fetch("/api/voice/ptt/cancel", { method: "POST" }).catch(() => {});
    }
    setButtonHoldingVisual(false);
    const timerEl = document.getElementById("ptt-hold-timer");
    if (timerEl) timerEl.innerText = "Hold Space or press & hold button";
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
  // Debug Drawer & Telemetry (Prompt B)
  // ------------------------------------------------------------------------
  let isDebugDrawerOpen = false;
  let debugLogs = [];
  let debugFallbacks = [];

  function toggleDebugDrawer(forceState) {
    const drawer = document.getElementById("debug-drawer");
    const backdrop = document.getElementById("debug-drawer-backdrop");
    if (!drawer) return;

    if (typeof forceState === "boolean") {
      isDebugDrawerOpen = forceState;
    } else {
      isDebugDrawerOpen = !isDebugDrawerOpen;
    }

    drawer.classList.toggle("open", isDebugDrawerOpen);
    if (backdrop) backdrop.classList.toggle("open", isDebugDrawerOpen);

    if (isDebugDrawerOpen) {
      refreshDebugDrawer();
    }
  }

  async function refreshDebugDrawer() {
    await Promise.all([
      loadDebugTurns(),
      loadDebugQuotas(),
      loadDebugHealth(),
    ]);
  }

  async function loadDebugTurns() {
    try {
      const res = await fetch("/api/perf");
      if (!res.ok) return;
      const data = await res.json();
      const records = (data.records || []).slice(-10).reverse();
      renderDebugTurns(records, data.averages || {});
    } catch (e) {
      console.warn("Failed to load debug turns", e);
    }
  }

  function renderDebugTurns(records, averages) {
    const list = document.getElementById("dd-turns-waterfall-list");
    const avgEl = document.getElementById("dd-avg-latency");
    const lastEl = document.getElementById("dd-last-release-audio");
    if (avgEl && averages && averages.total_latency_ms !== undefined) {
      avgEl.innerText = `${Math.round(averages.total_latency_ms)} ms`;
    }

    if (!list) return;
    if (records.length === 0) {
      list.innerHTML = `<div style="color:var(--text-dim); font-size:0.8rem; text-align:center; padding:20px;">No voice turns recorded yet. Hold Space and speak to generate telemetry.</div>`;
      return;
    }

    if (lastEl) {
      const first = records[0];
      const relAudio = first.release_to_first_audio_ms || first.total_latency_ms || 0;
      lastEl.innerText = `${Math.round(relAudio)} ms`;
      const talkChip = document.getElementById("talk-latency-chip");
      const talkLat = document.getElementById("talk-last-latency");
      if (talkChip && talkLat) {
        talkChip.style.display = "inline-flex";
        talkLat.innerText = `${Math.round(relAudio)} ms`;
      }
    }

    list.innerHTML = records.map((r, i) => {
      const total = Math.max(1, r.total_latency_ms || 1);
      const sttPct = Math.min(100, Math.round(((r.stt_latency_ms || 0) / total) * 100));
      const llmPct = Math.min(100, Math.round(((r.llm_first_token_ms || 0) / total) * 100));
      const ttsPct = Math.min(100, Math.round(((r.tts_first_audio_ms || 0) / total) * 100));
      const relAudio = r.release_to_first_audio_ms ? `${Math.round(r.release_to_first_audio_ms)}ms (Release->Audio)` : `${Math.round(r.total_latency_ms || 0)}ms Total`;

      return `
        <div class="dd-turn-item">
          <div class="dd-turn-header">
            <span style="font-weight:700; color:var(--text-bright);">#${r.turn_id || (i + 1)} · ${escapeHtml(r.mode || "voice")}</span>
            <span style="color:var(--accent);">${escapeHtml(relAudio)}</span>
          </div>
          <div style="font-size:0.82rem; color:var(--text-main); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
            "${escapeHtml(r.user_utterance || "(voice input)")}"
          </div>
          <div class="dd-waterfall-bar" title="STT: ${Math.round(r.stt_latency_ms || 0)}ms | LLM: ${Math.round(r.llm_first_token_ms || 0)}ms | TTS: ${Math.round(r.tts_first_audio_ms || 0)}ms">
            <div class="dd-wf-stt" style="width:${sttPct}%;"></div>
            <div class="dd-wf-llm" style="width:${llmPct}%;"></div>
            <div class="dd-wf-tts" style="width:${ttsPct}%;"></div>
          </div>
          <div style="display:flex; justify-content:space-between; font-size:0.72rem; color:var(--text-dim); font-family:var(--font-mono);">
            <span>STT: ${Math.round(r.stt_latency_ms || 0)}ms</span>
            <span>LLM: ${Math.round(r.llm_first_token_ms || 0)}ms</span>
            <span>TTS: ${Math.round(r.tts_first_audio_ms || 0)}ms</span>
          </div>
          ${Array.isArray(r.tts_engines) && r.tts_engines.length > 0 ? `
            <div style="display:flex; flex-wrap:wrap; gap:4px; margin-top:5px;">
              ${r.tts_engines.map(e => `
                <span style="font-size:0.68rem; padding:2px 6px; border-radius:3px; background:${e.fallback ? 'rgba(255,100,100,0.15)' : 'rgba(0,255,200,0.1)'}; border:1px solid ${e.fallback ? 'rgba(255,100,100,0.4)' : 'rgba(0,255,200,0.25)'}; color:var(--text-bright);">
                  ${escapeHtml(e.lang || 'en')}: <strong>${escapeHtml(e.engine || '')}</strong> [${escapeHtml(e.model || '')} · ${escapeHtml(e.voice || '')}]
                  ${e.fallback ? `<span style="color:#ff6b6b; font-weight:700; margin-left:4px;">⚠️ FALLBACK from ${escapeHtml(e.fallback_from || 'primary')}</span>` : ""}
                </span>
              `).join("")}
            </div>
          ` : ""}
        </div>
      `;
    }).join("");
  }

  async function loadDebugQuotas() {
    try {
      const [resQuota, resPerf, resSettings] = await Promise.all([
        fetch("/api/voice/quota").catch(() => null),
        fetch("/api/perf").catch(() => null),
        fetch("/api/settings").catch(() => null),
      ]);

      if (resQuota && resQuota.ok) {
        const q = await resQuota.json();
        const countEl = document.getElementById("dd-tts-quota-count");
        const capEl = document.getElementById("dd-tts-quota-cap");
        if (countEl) countEl.innerText = q.daily_requests || 0;
        if (capEl) capEl.innerText = q.daily_cap || 50;
      }

      if (resPerf && resPerf.ok) {
        const p = await resPerf.json();
        const totalEl = document.getElementById("dd-total-turns-count");
        if (totalEl) totalEl.innerText = (p.records || []).length;
      }

      if (resSettings && resSettings.ok) {
        const s = await resSettings.json();
        const llmEl = document.getElementById("dd-llm-provider-name");
        if (llmEl) llmEl.innerText = s.chat_model || "gemini-2.5-flash";
      }
    } catch (_) {}
  }

  function addDebugLog(msg, level = "info") {
    const time = new Date().toLocaleTimeString();
    debugLogs.unshift({ time, msg, level });
    if (debugLogs.length > 50) debugLogs.pop();
    renderDebugLogs();
  }

  function renderDebugLogs() {
    const container = document.getElementById("dd-logs-container");
    if (!container) return;
    if (debugLogs.length === 0) {
      container.innerHTML = `<div style="color:var(--text-dim); font-size:0.8rem; padding:12px;">No errors or notices recorded in this session.</div>`;
      return;
    }
    container.innerHTML = debugLogs.map((l) => `
      <div class="dd-log-entry ${l.level}">
        <span style="color:var(--text-dim); margin-right:6px;">[${l.time}]</span>
        <span>${escapeHtml(l.msg)}</span>
      </div>
    `).join("");
  }

  function addDebugFallback(eventData) {
    const time = new Date().toLocaleTimeString();
    debugFallbacks.unshift({ time, ...eventData });
    if (debugFallbacks.length > 30) debugFallbacks.pop();
    renderDebugFallbacks();
  }

  function renderDebugFallbacks() {
    const container = document.getElementById("dd-fallbacks-container");
    if (!container) return;
    if (debugFallbacks.length === 0) {
      container.innerHTML = `<div style="color:var(--text-dim); font-size:0.8rem; padding:12px;">No fallback events recorded. Primary engines running normally.</div>`;
      return;
    }
    container.innerHTML = debugFallbacks.map((f) => `
      <div class="dd-log-entry warning">
        <span style="color:var(--text-dim); margin-right:6px;">[${f.time}]</span>
        <strong>${escapeHtml(f.primary_engine || "Engine")} ➔ ${escapeHtml(f.fallback_engine || "Fallback")}</strong>: ${escapeHtml(f.reason || f.notice || "Fallback triggered")}
      </div>
    `).join("");
  }

  function initDebugDrawerEvents() {
    const toggleBtn = document.getElementById("debug-drawer-toggle-btn");
    const closeBtn = document.getElementById("debug-drawer-close");
    const backdrop = document.getElementById("debug-drawer-backdrop");
    const clearLogsBtn = document.getElementById("dd-clear-logs-btn");

    if (toggleBtn) toggleBtn.addEventListener("click", () => toggleDebugDrawer());
    if (closeBtn) closeBtn.addEventListener("click", () => toggleDebugDrawer(false));
    if (backdrop) backdrop.addEventListener("click", () => toggleDebugDrawer(false));
    if (clearLogsBtn) clearLogsBtn.addEventListener("click", () => {
      debugLogs = [];
      renderDebugLogs();
    });

    document.querySelectorAll(".dd-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        const target = tab.dataset.ddTab;
        document.querySelectorAll(".dd-tab").forEach((t) => {
          t.classList.toggle("active", t === tab);
          t.setAttribute("aria-selected", t === tab ? "true" : "false");
        });
        document.querySelectorAll(".dd-view").forEach((v) => {
          v.classList.toggle("active", v.id === `dd-view-${target}`);
        });
        if (target === "context") {
          loadShortTermContext();
        }
        if (target === "health") {
          loadDebugHealth();
        }
      });
    });

    // Short-Term Memory debug controls
    const stmToggle = document.getElementById("dd-stm-toggle");
    const forgetLastBtn = document.getElementById("dd-forget-last-btn");
    const clearStmBtn = document.getElementById("dd-clear-stm-btn");

    if (stmToggle) {
      stmToggle.addEventListener("change", async (e) => {
        try {
          await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ short_term_enabled: e.target.checked }),
          });
          showToast(`Short-term memory ${e.target.checked ? "enabled" : "disabled"}.`, "info");
          loadShortTermContext();
        } catch (err) {
          // ignore
        }
      });
    }

    if (forgetLastBtn) {
      forgetLastBtn.addEventListener("click", async () => {
        try {
          const res = await fetch("/api/short-term/last-exchange", { method: "DELETE" });
          if (res.ok) {
            showToast("Forgot last exchange.", "info");
            loadShortTermContext();
          }
        } catch (err) {
          // ignore
        }
      });
    }

    if (clearStmBtn) {
      clearStmBtn.addEventListener("click", async () => {
        if (!confirm("Clear all short-term conversation memory for this session?")) return;
        try {
          const res = await fetch("/api/short-term/clear", { method: "DELETE" });
          if (res.ok) {
            showToast("Short-term memory cleared.", "info");
            loadShortTermContext();
          }
        } catch (err) {
          // ignore
        }
      });
    }

    // Health, Backups & Diagnostics debug controls
    const runCheckBtn = document.getElementById("dd-run-self-check-btn");
    const offToggleBtn = document.getElementById("dd-offline-toggle-btn");
    const createBackupBtn = document.getElementById("dd-create-backup-btn");
    const exportDiagBtn = document.getElementById("dd-export-diag-btn");

    if (runCheckBtn) {
      runCheckBtn.addEventListener("click", async () => {
        try {
          runCheckBtn.disabled = true;
          runCheckBtn.innerText = "Checking...";
          await fetch("/api/watchdog/self-check", { method: "POST" });
          await loadDebugHealth();
          showToast("Self-check completed.", "success");
        } catch (e) {
          showToast("Self-check error: " + e.message, "error");
        } finally {
          runCheckBtn.disabled = false;
          runCheckBtn.innerText = "Run Self-Check";
        }
      });
    }

    if (offToggleBtn) {
      offToggleBtn.addEventListener("click", async () => {
        try {
          const curRes = await fetch("/api/offline/status");
          const curData = await curRes.json();
          const target = !curData.force_offline;
          const setRes = await fetch("/api/offline/toggle", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ force_offline: target }),
          });
          const setData = await setRes.json();
          showToast(`Degraded offline mode ${setData.force_offline ? "forced ON" : "set to AUTO"}.`, "info");
          await loadDebugHealth();
        } catch (e) {
          showToast("Offline toggle error: " + e.message, "error");
        }
      });
    }

    if (createBackupBtn) {
      createBackupBtn.addEventListener("click", async () => {
        try {
          createBackupBtn.disabled = true;
          createBackupBtn.innerText = "Backing up...";
          const res = await fetch("/api/backup", { method: "POST" });
          const data = await res.json();
          if (res.ok) {
            showToast(`Backup created: ${data.filename} (${Math.round(data.size_bytes / 1024)} KB)`, "success");
            await loadBackupsList();
          } else {
            showToast(`Backup failed: ${data.detail || "Error"}`, "error");
          }
        } catch (e) {
          showToast("Backup error: " + e.message, "error");
        } finally {
          createBackupBtn.disabled = false;
          createBackupBtn.innerText = "Backup Now";
        }
      });
    }

    if (exportDiagBtn) {
      exportDiagBtn.addEventListener("click", async () => {
        try {
          exportDiagBtn.disabled = true;
          exportDiagBtn.innerText = "Exporting...";
          const res = await fetch("/api/diagnostics/export", { method: "POST" });
          const data = await res.json();
          if (res.ok) {
            showToast(`Diagnostics bundle ready: ${data.filename}`, "success");
            window.location.href = `/api/backups/download/${encodeURIComponent(data.filename)}`;
          } else {
            showToast(`Diagnostics export failed: ${data.detail || "Error"}`, "error");
          }
        } catch (e) {
          showToast("Export error: " + e.message, "error");
        } finally {
          exportDiagBtn.disabled = false;
          exportDiagBtn.innerText = "Export Diagnostics";
        }
      });
    }
  }

  async function loadDebugHealth() {
    try {
      const res = await fetch("/health");
      if (!res.ok) return;
      const data = await res.json();

      const statusEl = document.getElementById("dd-health-status");
      if (statusEl) {
        statusEl.innerText = (data.status || "HEALTHY").toUpperCase();
        statusEl.style.color = data.status === "healthy" ? "var(--green)" : (data.status === "degraded" ? "var(--amber)" : "var(--red)");
      }

      const offBanner = document.getElementById("offline-banner");
      if (offBanner) {
        offBanner.style.display = data.offline ? "block" : "none";
      }

      const diskEl = document.getElementById("dd-disk-free");
      if (diskEl && data.self_check && data.self_check.checks && data.self_check.checks.disk) {
        diskEl.innerText = `${data.self_check.checks.disk.free_gb || 0} GB`;
      }

      const apiKeyEl = document.getElementById("dd-apikey-status");
      if (apiKeyEl && data.self_check && data.self_check.checks && data.self_check.checks.gemini_api_key) {
        apiKeyEl.innerText = data.self_check.checks.gemini_api_key.ok ? "READY" : "MISSING";
        apiKeyEl.style.color = data.self_check.checks.gemini_api_key.ok ? "var(--green)" : "var(--red)";
      }

      const micEl = document.getElementById("dd-mic-status");
      if (micEl && data.self_check && data.self_check.checks && data.self_check.checks.microphone) {
        micEl.innerText = data.self_check.checks.microphone.ok ? "READY" : "WARNING";
      }

      const compsEl = document.getElementById("dd-watchdog-components");
      if (compsEl) {
        const comps = data.components || {};
        const entries = Object.entries(comps);
        if (entries.length === 0) {
          compsEl.innerHTML = `<div style="font-size:0.8rem; color:var(--text-dim); padding:4px 0;">All core threads active.</div>`;
        } else {
          compsEl.innerHTML = entries.map(([name, info]) => `
            <div style="display:flex; justify-content:space-between; align-items:center; background:var(--bg-card); padding:6px 10px; border-radius:4px; font-size:0.8rem; border:1px solid var(--border-subtle);">
              <span><strong>${escapeHtml(name)}</strong> (${escapeHtml(info.status)})</span>
              <span style="color:${info.status === 'running' ? 'var(--green)' : 'var(--red)'}; font-family:var(--font-mono); font-size:0.75rem;">
                ${info.uptime_seconds ? `${info.uptime_seconds}s up` : ''} ${info.recent_crashes_10m ? `(${info.recent_crashes_10m} crashes)` : ''}
              </span>
            </div>
          `).join("");
        }
      }

      loadBackupsList();
    } catch (e) {
      console.warn("Failed loading debug health:", e);
    }
  }

  async function loadBackupsList() {
    const listEl = document.getElementById("dd-backups-list");
    if (!listEl) return;
    try {
      const res = await fetch("/api/backups");
      if (!res.ok) return;
      const data = await res.json();
      const backups = data.backups || [];
      if (backups.length === 0) {
        listEl.innerHTML = `<div style="color:var(--text-dim); text-align:center; padding:10px;">No backup archives found.</div>`;
        return;
      }
      listEl.innerHTML = backups.map(b => `
        <div style="display:flex; justify-content:space-between; align-items:center; background:var(--bg-card); padding:6px 8px; border-radius:4px; border:1px solid var(--border-subtle);">
          <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:200px;">
            <a href="/api/backups/download/${encodeURIComponent(b.filename)}" download style="color:var(--accent); font-weight:600; text-decoration:none;">${escapeHtml(b.filename)}</a>
            <div style="font-size:0.7rem; color:var(--text-dim);">${Math.round(b.size_bytes / 1024)} KB · ${escapeHtml(b.created_at.slice(0, 19).replace('T', ' '))}</div>
          </div>
          <button class="btn btn-xs btn-outline dd-restore-btn" data-file="${escapeHtml(b.filename)}">Restore</button>
        </div>
      `).join("");

      document.querySelectorAll(".dd-restore-btn").forEach(btn => {
        btn.onclick = async () => {
          const fn = btn.dataset.file;
          if (!confirm(`Restore from backup '${fn}'? Current live state will be snapshotted before restore.`)) return;
          try {
            const resp = await fetch("/api/restore", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ filename: fn })
            });
            const rData = await resp.json();
            if (resp.ok) {
              showToast(`Restored ${rData.restored_count} files from ${fn}.`, "success");
            } else {
              showToast(`Restore failed: ${rData.detail || "Error"}`, "error");
            }
          } catch (err) {
            showToast(`Restore error: ${err.message}`, "error");
          }
        };
      });
    } catch (e) {
      console.warn("Failed loading backups list:", e);
    }
  }

  async function loadShortTermContext() {
    try {
      const res = await fetch("/api/short-term/context?session_id=web");
      if (!res.ok) return;
      const data = await res.json();

      const toggle = document.getElementById("dd-stm-toggle");
      if (toggle && data.short_term_enabled !== undefined) {
        toggle.checked = data.short_term_enabled;
      }

      const preview = document.getElementById("dd-context-block-preview");
      if (preview) {
        preview.innerText = data.context_block || "(No context generated yet.)";
      }

      const countSpan = document.getElementById("dd-stm-msg-count");
      if (countSpan) countSpan.innerText = String(data.message_count || 0);

      const list = document.getElementById("dd-stm-messages-list");
      if (!list) return;

      const msgs = data.messages || [];
      if (msgs.length === 0) {
        list.innerHTML = `<div style="color:var(--text-dim); text-align:center; padding:12px;">No active session messages in memory.</div>`;
        return;
      }

      list.innerHTML = msgs.map((m) => {
        const isUser = m.role === "user";
        return `
          <div style="background:var(--bg-card); border:1px solid var(--border-subtle); padding:8px 10px; border-radius:var(--radius-sm);">
            <div style="font-size:0.7rem; font-family:var(--font-mono); color:${isUser ? "var(--cyan)" : "var(--accent)"}; font-weight:600; margin-bottom:2px;">
              ${isUser ? "YOU" : "NALLA"} [${m.created_at ? m.created_at.slice(11, 19) : ""}]
            </div>
            <div style="color:var(--text-bright);">${escapeHtml(m.text)}</div>
          </div>
        `;
      }).join("");
    } catch (err) {
      console.warn("Failed loading short-term context:", err);
    }
  }

  // ------------------------------------------------------------------------
  // Talk Screen Chat Input Bar (/ to focus)
  // ------------------------------------------------------------------------
  function initTalkChatInput() {
    const input = document.getElementById("chat-user-input");
    const sendBtn = document.getElementById("chat-send-btn");
    const clearBtn = document.getElementById("talk-clear-transcript-btn");

    async function sendChatMessage() {
      if (!input) return;
      const text = input.value.trim();
      if (!text) return;
      input.value = "";

      appendTranscriptLine("user", text);
      updateVoiceStateUI("THINKING");

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
        });
        if (res.ok) {
          const data = await res.json();
          if (data.reply) {
            appendTranscriptLine("nalla", data.reply);
            updateVoiceStateUI("SPEAKING");
            if (chatRevertTimeout) clearTimeout(chatRevertTimeout);
            const speakDuration = Math.min(6000, Math.max(2200, data.reply.length * 45));
            chatRevertTimeout = setTimeout(() => {
              updateVoiceStateUI("IDLE");
            }, speakDuration);
          } else {
            updateVoiceStateUI("IDLE");
          }
        } else {
          updateVoiceStateUI("IDLE");
          showToast("Error sending message to Nalla", "alert");
        }
      } catch (err) {
        updateVoiceStateUI("IDLE");
        showToast(`Chat error: ${err.message}`, "alert");
      }
    }

    if (sendBtn) {
      sendBtn.addEventListener("click", sendChatMessage);
    }
    if (input) {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          sendChatMessage();
        }
      });
    }
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        const box = document.getElementById("live-transcript-content");
        if (box) {
          box.innerHTML = `<div class="transcript-msg system-msg" style="color:var(--text-dim); font-size:0.8rem;">[Transcript cleared. Ready for speech or text interaction.]</div>`;
        }
      });
    }
  }

  // ------------------------------------------------------------------------
  // Memory Subtabs (Facts, Entities, Inbox, Journal, Review, Tasks, Reminders)
  // ------------------------------------------------------------------------
  function initMemorySubtabs() {
    const tabBtns = document.querySelectorAll(".memory-stage .subnav-btn");
    tabBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetId = btn.getAttribute("data-target");
        if (!targetId) return;

        tabBtns.forEach((b) => {
          b.classList.remove("active");
          b.setAttribute("aria-selected", "false");
        });
        btn.classList.add("active");
        btn.setAttribute("aria-selected", "true");

        document.querySelectorAll(".memory-stage .subtab-content").forEach((view) => {
          view.classList.remove("active");
        });
        const targetView = document.getElementById(targetId);
        if (targetView) targetView.classList.add("active");

        if (window.NallaPages && window.NallaPages.memory) {
          if (targetId === "subtab-view-entities") window.NallaPages.memory.loadEntities();
          else if (targetId === "subtab-view-inbox") window.NallaPages.memory.loadCandidates();
          else if (targetId === "subtab-view-journal") window.NallaPages.memory.loadJournals();
          else if (targetId === "subtab-view-review") window.NallaPages.memory.loadWeeklyReview();
        }
      });
    });
  }

  // ------------------------------------------------------------------------
  // Settings Search & Theme Toggle
  // ------------------------------------------------------------------------
  function initSettingsSearch() {
    const searchInput = document.getElementById("settings-search-input");
    if (!searchInput) return;

    searchInput.addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase().trim();
      document.querySelectorAll(".settings-section-card").forEach((card) => {
        if (!q) {
          card.style.display = "flex";
          return;
        }
        const text = card.innerText.toLowerCase();
        const keywords = (card.dataset.keywords || "").toLowerCase();
        const match = text.includes(q) || keywords.includes(q);
        card.style.display = match ? "flex" : "none";
      });
    });
  }

  function initThemeToggle() {
    const btn = document.getElementById("theme-toggle-btn");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const current = document.body.dataset.theme;
      const nextTheme = current === "light" ? "cyan" : "light";
      setTheme(nextTheme);
      showToast(`Switched to ${nextTheme === "light" ? "Light" : "Dark"} theme.`, "info");
    });
  }

  // ------------------------------------------------------------------------
  // Keyboard Shortcuts
  // ------------------------------------------------------------------------
  function initKeyboardShortcuts() {
    document.addEventListener("keydown", (e) => {
      const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : "";
      const isInput =
        activeTag === "input" ||
        activeTag === "textarea" ||
        activeTag === "select" ||
        (document.activeElement && document.activeElement.isContentEditable);

      // Ctrl+K: Command Palette
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        const palette = document.getElementById("command-palette-modal");
        if (palette && palette.classList.contains("open")) {
          closeCommandPalette();
        } else {
          openCommandPalette();
        }
        return;
      }

      // Esc: Stop speaking immediately & Close Debug Drawer / Modals / Cancel PTT
      if (e.key === "Escape") {
        if (isDebugDrawerOpen) {
          toggleDebugDrawer(false);
          return;
        }
        if (isSpaceHolding || isPointerHolding) {
          isSpaceHolding = false;
          isPointerHolding = false;
          sendPTTCancel();
        }
        interruptSpeech();
        closeCommandPalette();
        toggleShortcutsCheatSheet(false);
        closeLastErrorModal();
        const approval = document.getElementById("approval-modal");
        if (approval) approval.classList.remove("open");
        return;
      }

      // If typing in an input, do not trigger single key hotkeys
      if (isInput) return;

      // D or d: Toggle Debug Drawer
      if (e.key.toLowerCase() === "d" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        toggleDebugDrawer();
        return;
      }

      // /: Focus the text box
      if (e.key === "/" && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        switchPage("home");
        const chatInp = document.getElementById("chat-user-input");
        if (chatInp) {
          chatInp.focus();
          chatInp.select();
        }
        return;
      }

      // ?: Toggle Shortcuts Cheat-Sheet
      if (e.key === "?" || (e.shiftKey && e.key === "/")) {
        e.preventDefault();
        toggleShortcutsCheatSheet();
        return;
      }

      // Space: Hold to talk (Walkie-talkie mode)
      if (e.code === "Space") {
        e.preventDefault();
        if (e.repeat) return;
        if (!isSpaceHolding) {
          isSpaceHolding = true;
          sendPTTDown();
        }
        return;
      }

      // M: Mic Mute
      if (e.key.toLowerCase() === "m") {
        e.preventDefault();
        toggleMute();
      }

      // Number keys 1-3 for top navigation
      if (e.key === "1") switchPage("home");
      else if (e.key === "2") switchPage("memory");
      else if (e.key === "3" || e.key === "0") switchPage("settings");
    });

    document.addEventListener("keyup", (e) => {
      const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : "";
      const isInput =
        activeTag === "input" ||
        activeTag === "textarea" ||
        activeTag === "select" ||
        (document.activeElement && document.activeElement.isContentEditable);

      if (e.code === "Space") {
        if (!isInput) e.preventDefault();
        if (isSpaceHolding) {
          isSpaceHolding = false;
          sendPTTUp();
        }
      }
    });

    // Window blur or tab hidden automatically releases/cancels PTT
    window.addEventListener("blur", () => {
      if (isSpaceHolding || isPointerHolding) {
        isSpaceHolding = false;
        isPointerHolding = false;
        sendPTTCancel();
      }
    });

    document.addEventListener("visibilitychange", () => {
      if (document.hidden && (isSpaceHolding || isPointerHolding)) {
        isSpaceHolding = false;
        isPointerHolding = false;
        sendPTTCancel();
      }
    });

    // Palette search input events
    const paletteInput = document.getElementById("palette-search-input");
    if (paletteInput) {
      paletteInput.addEventListener("input", (e) => {
        const q = e.target.value.toLowerCase().trim();
        const matches = currentPalettePool.filter((cmd) =>
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
      dot.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setTheme(dot.dataset.themeChoice);
        }
      });
    });

    // 3. Set up sidebar navigation
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchPage(btn.dataset.page));
    });

    // 4. Set up modal backdrops click to close
    const paletteModal = document.getElementById("command-palette-modal");
    if (paletteModal) {
      paletteModal.addEventListener("click", (e) => {
        if (e.target === paletteModal) closeCommandPalette();
      });
    }

    const shortcutsModal = document.getElementById("shortcuts-cheat-sheet-modal");
    if (shortcutsModal) {
      shortcutsModal.addEventListener("click", (e) => {
        if (e.target === shortcutsModal) toggleShortcutsCheatSheet(false);
      });
    }

    const errorModal = document.getElementById("last-error-modal");
    if (errorModal) {
      errorModal.addEventListener("click", (e) => {
        if (e.target === errorModal) closeLastErrorModal();
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

    // 9. Attach shortcuts & minimal dashboard handlers
    initKeyboardShortcuts();
    initDebugDrawerEvents();
    initTalkChatInput();
    initMemorySubtabs();
    initSettingsSearch();
    initThemeToggle();

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
    sendPTTDown,
    sendPTTUp,
    sendPTTCancel,
    isPTTHolding: () => (isSpaceHolding || isPointerHolding),
    toggleMute,
    interruptSpeech,
    testSpeaker,
    testMic,
    showApprovalModal,
    escapeHtml,
    playChime,
    playListeningChime,
    playDoneChime,
    playErrorTone,
    switchVoiceProfile,
    setPersonalityMode,
    openCommandPalette,
    closeCommandPalette,
    toggleShortcutsCheatSheet,
    openLastErrorModal,
    closeLastErrorModal,
    toggleDebugDrawer,
    refreshDebugDrawer,
    setSoundEnabled,
    setSoundVolume,
    getSoundSettings,
  };
})();
