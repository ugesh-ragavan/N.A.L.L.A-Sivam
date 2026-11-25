/**
 * app/web/static/js/pages/settings.js
 *
 * System Settings & Configuration Controller:
 *   • Masked Gemini API Key with safe preview toggle.
 *   • Chat Model & Command Model selection.
 *   • Accent color theme selector.
 *   • Audio chimes sound toggle.
 *   • JSON backup export & import.
 *   • Danger Zone: Clear memory with typed "DELETE" confirmation.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  function initSettings() {
    // API Key mask toggle
    const toggleKeyBtn = document.getElementById("set-toggle-key-btn");
    const keyInput = document.getElementById("set-api-key");
    if (toggleKeyBtn && keyInput) {
      toggleKeyBtn.addEventListener("click", () => {
        if (keyInput.type === "password") {
          keyInput.type = "text";
          toggleKeyBtn.innerText = "Hide";
        } else {
          keyInput.type = "password";
          toggleKeyBtn.innerText = "Reveal";
        }
      });
    }

    // Save model settings
    const saveModelBtn = document.getElementById("set-save-models-btn");
    if (saveModelBtn) {
      saveModelBtn.addEventListener("click", saveModelSettings);
    }

    // Presence & Proactive Policy listeners
    document.getElementById("btn-presence-save")?.addEventListener("click", savePresenceSettings);
    document.getElementById("btn-test-briefing")?.addEventListener("click", () => testPresencePiece("briefing"));
    document.getElementById("btn-test-welcome")?.addEventListener("click", () => testPresencePiece("welcome_back"));
    document.getElementById("btn-test-checkin")?.addEventListener("click", () => testPresencePiece("check_in"));
    document.getElementById("btn-test-wrapup")?.addEventListener("click", () => testPresencePiece("wrap_up"));


    // Theme buttons in settings
    document.querySelectorAll(".settings-theme-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        window.NallaApp.setTheme(btn.dataset.theme);
        window.NallaApp.showToast(`Theme changed to ${btn.dataset.theme}.`, "info");
      });
    });

    // Sound toggle
    const soundToggle = document.getElementById("set-sound-toggle");
    if (soundToggle) {
      soundToggle.addEventListener("change", (e) => {
        window.NallaApp.setSoundEnabled(e.target.checked);
        window.NallaApp.showToast(e.target.checked ? "Audio chimes enabled." : "Audio muted.", "info");
      });
    }

    // Sound volume slider
    const soundSlider = document.getElementById("set-sound-volume");
    if (soundSlider) {
      soundSlider.addEventListener("input", (e) => {
        const valPct = parseInt(e.target.value, 10);
        window.NallaApp.setSoundVolume(valPct / 100);
      });
      soundSlider.addEventListener("change", () => {
        window.NallaApp.playListeningChime();
      });
    }

    // Danger Zone Clear Memory
    const clearMemBtn = document.getElementById("set-clear-memory-btn");
    if (clearMemBtn) {
      clearMemBtn.addEventListener("click", handleClearAllMemory);
    }

    // Kongu touch slider
    const konguSlider = document.getElementById("set-kongu-slider");
    const konguValText = document.getElementById("set-kongu-val");
    if (konguSlider && konguValText) {
      konguSlider.addEventListener("input", (e) => {
        konguValText.innerText = `${e.target.value}/10`;
      });
      konguSlider.addEventListener("change", saveTamilPipelineSettings);
    }

    const tamilModeSelect = document.getElementById("set-tamil-text-mode");
    if (tamilModeSelect) {
      tamilModeSelect.addEventListener("change", saveTamilPipelineSettings);
    }

    const sttEngineSelect = document.getElementById("set-stt-engine");
    if (sttEngineSelect) {
      sttEngineSelect.addEventListener("change", saveTamilPipelineSettings);
    }

    const forceTtsSelect = document.getElementById("set-force-tts-engine");
    if (forceTtsSelect) {
      forceTtsSelect.addEventListener("change", saveTamilPipelineSettings);
    }

    const voiceFxSelect = document.getElementById("set-voice-fx");
    if (voiceFxSelect) {
      voiceFxSelect.addEventListener("change", saveTamilPipelineSettings);
    }

    const geminiModelSelect = document.getElementById("set-gemini-tts-model");
    if (geminiModelSelect) {
      geminiModelSelect.addEventListener("change", saveTamilPipelineSettings);
    }

    const geminiStyleInput = document.getElementById("set-gemini-tts-style");
    if (geminiStyleInput) {
      geminiStyleInput.addEventListener("blur", saveTamilPipelineSettings);
    }

    const expressiveToggle = document.getElementById("set-expressive-tags");
    if (expressiveToggle) {
      expressiveToggle.addEventListener("change", saveTamilPipelineSettings);
    }

    const talkLookSelect = document.getElementById("set-talk-look");
    if (talkLookSelect) {
      talkLookSelect.addEventListener("change", (e) => {
        saveTamilPipelineSettings();
        if (window.NallaPages && window.NallaPages.home && window.NallaPages.home.applyLookMode) {
          window.NallaPages.home.applyLookMode(e.target.value);
        }
      });
    }

    loadSettings();
  }

  async function saveTamilPipelineSettings() {
    const tamilMode = document.getElementById("set-tamil-text-mode")?.value;
    const konguVal = parseInt(document.getElementById("set-kongu-slider")?.value || "2", 10);
    const sttEngine = document.getElementById("set-stt-engine")?.value;
    const forceTtsEngine = document.getElementById("set-force-tts-engine")?.value;
    const voiceFx = document.getElementById("set-voice-fx")?.value;
    const geminiTtsModel = document.getElementById("set-gemini-tts-model")?.value;
    const geminiTtsStyle = document.getElementById("set-gemini-tts-style")?.value;
    const expressiveTags = document.getElementById("set-expressive-tags")?.checked;
    const talkLook = document.getElementById("set-talk-look")?.value;

    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tamil_text_mode: tamilMode,
          kongu_touch: konguVal,
          stt_engine: sttEngine,
          force_tts_engine: forceTtsEngine,
          voice_fx: voiceFx,
          gemini_tts_model: geminiTtsModel,
          gemini_tts_style: geminiTtsStyle,
          expressive_tags: expressiveTags,
          talk_look: talkLook,
        }),
      });
      if (res.ok) {
        window.NallaApp.showToast("Settings updated live.", "info");
      }
    } catch (e) {
      console.warn("Failed saving settings:", e);
    }
  }

  async function loadSettings() {
    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const data = await res.json();
        const chatSel = document.getElementById("set-chat-model");
        const cmdSel = document.getElementById("set-cmd-model");
        const soundToggle = document.getElementById("set-sound-toggle");
        const tamilModeSel = document.getElementById("set-tamil-text-mode");
        const konguSlider = document.getElementById("set-kongu-slider");
        const konguValText = document.getElementById("set-kongu-val");
        const sttEngineSel = document.getElementById("set-stt-engine");
        const forceTtsSel = document.getElementById("set-force-tts-engine");
        const voiceFxSel = document.getElementById("set-voice-fx");
        const geminiModelSel = document.getElementById("set-gemini-tts-model");
        const geminiStyleInp = document.getElementById("set-gemini-tts-style");
        const expressiveTgl = document.getElementById("set-expressive-tags");
        const talkLookSel = document.getElementById("set-talk-look");

        if (chatSel && data.chat_model) chatSel.value = data.chat_model;
        if (cmdSel && data.command_model) cmdSel.value = data.command_model;
        if (tamilModeSel && data.tamil_text_mode) tamilModeSel.value = data.tamil_text_mode;
        if (forceTtsSel && data.force_tts_engine) forceTtsSel.value = data.force_tts_engine;
        if (voiceFxSel && data.voice_fx) voiceFxSel.value = data.voice_fx;
        if (geminiModelSel && data.gemini_tts_model) geminiModelSel.value = data.gemini_tts_model;
        if (geminiStyleInp && data.gemini_tts_style) geminiStyleInp.value = data.gemini_tts_style;
        if (expressiveTgl && data.expressive_tags !== undefined) expressiveTgl.checked = data.expressive_tags;
        if (talkLookSel && data.talk_look) {
          talkLookSel.value = data.talk_look;
          if (window.NallaPages && window.NallaPages.home && window.NallaPages.home.applyLookMode) {
            window.NallaPages.home.applyLookMode(data.talk_look);
          }
        }
        if (konguSlider && data.kongu_touch !== undefined) {
          konguSlider.value = data.kongu_touch;
          if (konguValText) konguValText.innerText = `${data.kongu_touch}/10`;
        }
        if (sttEngineSel && data.stt_engine) sttEngineSel.value = data.stt_engine;

        const soundSettings = window.NallaApp.getSoundSettings ? window.NallaApp.getSoundSettings() : { enabled: true, volume: 0.7 };
        if (soundToggle) soundToggle.checked = soundSettings.enabled;
        const soundSlider = document.getElementById("set-sound-volume");
        const soundValText = document.getElementById("set-sound-volume-val");
        if (soundSlider) soundSlider.value = Math.round(soundSettings.volume * 100);
        if (soundValText) soundValText.innerText = `${Math.round(soundSettings.volume * 100)}%`;

        await loadPresenceSettings();
      }
    } catch (e) {
      console.error("Failed loading settings", e);
    }
  }

  async function saveModelSettings() {
    const chatModel = document.getElementById("set-chat-model")?.value;
    const cmdModel = document.getElementById("set-cmd-model")?.value;

    try {
      const res = await fetch("/api/settings/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_model: chatModel, command_model: cmdModel }),
      });
      if (res.ok) {
        window.NallaApp.showToast("Model preferences updated!", "success");
      } else {
        window.NallaApp.showToast("Error updating models.", "alert");
      }
    } catch (e) {
      window.NallaApp.showToast("Network error.", "alert");
    }
  }

  async function handleClearAllMemory() {
    const input = prompt("DANGER: Type 'DELETE' to permanently wipe all stored SQLite memories:");
    if (input !== "DELETE") {
      window.NallaApp.showToast("Memory wipe cancelled.", "info");
      return;
    }

    try {
      const res = await fetch("/api/memories/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "DELETE" }),
      });
      if (res.ok) {
        window.NallaApp.showToast("All memories wiped successfully.", "alert");
      } else {
        window.NallaApp.showToast("Failed to clear memories.", "alert");
      }
    } catch (e) {
      window.NallaApp.showToast("Error clearing memories.", "alert");
    }
  }

  async function loadPresenceSettings() {
    try {
      const res = await fetch("/api/presence");
      if (!res.ok) return;
      const data = await res.json();
      const s = data.settings || {};
      const elProactive = document.getElementById("set-proactive-toggle");
      if (elProactive) elProactive.checked = !!s.proactive_enabled;

      const elQuietStart = document.getElementById("set-quiet-start");
      if (elQuietStart) elQuietStart.value = s.quiet_hours_start || "23:30";

      const elQuietEnd = document.getElementById("set-quiet-end");
      if (elQuietEnd) elQuietEnd.value = s.quiet_hours_end || "06:30";

      const elDnd = document.getElementById("set-proactive-dnd");
      if (elDnd) elDnd.checked = !!s.dnd;

      const elCapHour = document.getElementById("set-cap-hour");
      if (elCapHour) elCapHour.value = s.max_per_hour ?? 2;

      const elCapDay = document.getElementById("set-cap-day");
      if (elCapDay) elCapDay.value = s.max_per_day ?? 8;

      const elSoundCue = document.getElementById("set-sound-cue");
      if (elSoundCue) elSoundCue.checked = !!s.sound_cue;

      const elBriefing = document.getElementById("set-cat-briefing");
      if (elBriefing) elBriefing.checked = !!s.briefing_enabled;

      const elCheckins = document.getElementById("set-cat-checkins");
      if (elCheckins) elCheckins.checked = !!s.check_ins_enabled;

      const elWelcome = document.getElementById("set-cat-welcome");
      if (elWelcome) elWelcome.checked = !!s.welcome_back_enabled;

      const elWrapup = document.getElementById("set-cat-wrapup");
      if (elWrapup) elWrapup.checked = !!s.wrap_up_enabled;
    } catch (e) {
      console.warn("Could not load presence settings", e);
    }
  }

  async function savePresenceSettings() {
    const payload = {
      proactive_enabled: document.getElementById("set-proactive-toggle")?.checked,
      quiet_hours_start: document.getElementById("set-quiet-start")?.value,
      quiet_hours_end: document.getElementById("set-quiet-end")?.value,
      dnd: document.getElementById("set-proactive-dnd")?.checked,
      max_per_hour: parseInt(document.getElementById("set-cap-hour")?.value || "2", 10),
      max_per_day: parseInt(document.getElementById("set-cap-day")?.value || "8", 10),
      sound_cue: document.getElementById("set-sound-cue")?.checked,
      briefing_enabled: document.getElementById("set-cat-briefing")?.checked,
      check_ins_enabled: document.getElementById("set-cat-checkins")?.checked,
      welcome_back_enabled: document.getElementById("set-cat-welcome")?.checked,
      wrap_up_enabled: document.getElementById("set-cat-wrapup")?.checked,
    };
    try {
      const res = await fetch("/api/presence", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        window.NallaApp?.showToast("Presence policy settings saved.", "info");
      }
    } catch (e) {
      window.NallaApp?.showToast("Error saving presence settings.", "alert");
    }
  }

  async function testPresencePiece(category) {
    const box = document.getElementById("presence-test-result");
    if (box) {
      box.style.display = "block";
      box.innerHTML = `<span style="color:var(--accent);">Generating ${category} in Nalla's voice...</span>`;
    }
    try {
      const res = await fetch("/api/presence/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category }),
      });
      if (!res.ok) throw new Error("Server error");
      const data = await res.json();
      if (box) {
        box.innerHTML = `
          <div style="color:var(--accent); font-weight:700; margin-bottom:4px;">🗣️ Spoken (${category}):</div>
          <div style="margin-bottom:8px; font-style:italic; color:#e0f2fe;">"${data.spoken}"</div>
          ${data.screen ? `<div style="font-size:0.72rem; color:var(--text-dim); white-space:pre-wrap; border-top:1px dashed var(--border-subtle); padding-top:6px; margin-top:6px;">${data.screen}</div>` : ""}
        `;
      }
    } catch (e) {
      if (box) box.innerHTML = `<span style="color:var(--red);">Error generating piece: ${e.message}</span>`;
    }
  }

  window.NallaPages.settings = {
    init: initSettings,
    onActivate: loadSettings,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSettings);
  } else {
    initSettings();
  }
})();
