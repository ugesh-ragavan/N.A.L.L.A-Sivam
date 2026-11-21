/**
 * app/web/static/js/pages/personality.js
 *
 * Personality Studio Controller:
 *   • Sliders for Sarcasm, Warmth, Tamil mix, Verbosity.
 *   • Mode selector (Friend, Jarvis, Focus, Engineer).
 *   • Presets: "Cool Friend", "Pure Jarvis", "Roast Mode", "Calm Night".
 *   • Editable persona markdown text with save to disk & revert.
 *   • Live injected prompt preview.
 *   • Instant Test Chat box testing unsaved sliders live.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  const PRESETS = {
    friend: { mode: "Friend", sarcasm: 7, warmth: 8, tamil_mix: 40, verbosity: "very short" },
    jarvis: { mode: "Jarvis", sarcasm: 3, warmth: 6, tamil_mix: 10, verbosity: "short" },
    roast: { mode: "Friend", sarcasm: 10, warmth: 3, tamil_mix: 60, verbosity: "short" },
    calm: { mode: "Focus", sarcasm: 0, warmth: 9, tamil_mix: 10, verbosity: "very short" },
  };

  let activeMode = "Friend";

  function initPersonality() {
    // Preset buttons
    document.querySelectorAll(".persona-preset-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pKey = btn.dataset.preset;
        if (PRESETS[pKey]) applyPreset(PRESETS[pKey]);
      });
    });

    // Mode buttons
    document.querySelectorAll(".mode-select-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        setMode(btn.dataset.mode);
      });
    });

    // Slider inputs
    const sSarcasm = document.getElementById("ps-slider-sarcasm");
    const sWarmth = document.getElementById("ps-slider-warmth");
    const sTamil = document.getElementById("ps-slider-tamil");
    const sVerb = document.getElementById("ps-select-verbosity");

    [sSarcasm, sWarmth, sTamil, sVerb].forEach((el) => {
      if (el) el.addEventListener("input", onSliderUpdate);
    });

    // Save button
    const saveBtn = document.getElementById("ps-save-btn");
    if (saveBtn) {
      saveBtn.addEventListener("click", savePersonalitySettings);
    }

    // Persona text editor buttons
    const loadPersonaBtn = document.getElementById("ps-load-persona-btn");
    const savePersonaBtn = document.getElementById("ps-save-persona-btn");
    const revertPersonaBtn = document.getElementById("ps-revert-persona-btn");

    if (loadPersonaBtn) loadPersonaBtn.addEventListener("click", loadRawPersona);
    if (savePersonaBtn) savePersonaBtn.addEventListener("click", saveRawPersona);
    if (revertPersonaBtn) revertPersonaBtn.addEventListener("click", revertRawPersona);

    // Test chat input
    const testSendBtn = document.getElementById("ps-test-send-btn");
    const testInput = document.getElementById("ps-test-input");
    if (testSendBtn && testInput) {
      testSendBtn.addEventListener("click", sendTestChat);
      testInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") sendTestChat();
      });
    }

    loadCurrentSettings();
    loadRawPersona();
  }

  function setMode(mode) {
    activeMode = mode;
    document.querySelectorAll(".mode-select-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.mode === mode);
    });
    const badge = document.getElementById("ps-active-mode-badge");
    if (badge) badge.innerText = `${mode} Mode`;
    updatePromptPreview();
  }

  function applyPreset(preset) {
    setMode(preset.mode);
    const sSarcasm = document.getElementById("ps-slider-sarcasm");
    const sWarmth = document.getElementById("ps-slider-warmth");
    const sTamil = document.getElementById("ps-slider-tamil");
    const sVerb = document.getElementById("ps-select-verbosity");

    if (sSarcasm) sSarcasm.value = preset.sarcasm;
    if (sWarmth) sWarmth.value = preset.warmth;
    if (sTamil) sTamil.value = preset.tamil_mix;
    if (sVerb) sVerb.value = preset.verbosity;

    onSliderUpdate();
    window.NallaApp.showToast(`Applied preset. Test below before saving.`, "info");
  }

  function onSliderUpdate() {
    const s = document.getElementById("ps-slider-sarcasm").value;
    const w = document.getElementById("ps-slider-warmth").value;
    const t = document.getElementById("ps-slider-tamil").value;
    const v = document.getElementById("ps-select-verbosity").value;

    document.getElementById("ps-val-sarcasm").innerText = `${s}/10`;
    document.getElementById("ps-val-warmth").innerText = `${w}/10`;
    document.getElementById("ps-val-tamil").innerText = `${t}%`;

    updatePromptPreview();
  }

  function updatePromptPreview() {
    const s = document.getElementById("ps-slider-sarcasm")?.value || "7";
    const w = document.getElementById("ps-slider-warmth")?.value || "8";
    const t = document.getElementById("ps-slider-tamil")?.value || "40";
    const v = document.getElementById("ps-select-verbosity")?.value || "very short";

    const preview = `Sarcasm: ${s}/10. Warmth: ${w}/10. Tamil mix: ${t}%. Replies: ${v}.`;
    const box = document.getElementById("ps-prompt-preview-box");
    if (box) box.innerText = preview;
  }

  async function loadCurrentSettings() {
    try {
      const res = await fetch("/api/personality");
      if (res.ok) {
        const data = await res.json();
        setMode(data.mode || "Friend");
        document.getElementById("ps-slider-sarcasm").value = data.sarcasm;
        document.getElementById("ps-slider-warmth").value = data.warmth;
        document.getElementById("ps-slider-tamil").value = data.tamil_mix;
        document.getElementById("ps-select-verbosity").value = data.verbosity;
        onSliderUpdate();
      }
    } catch (e) {
      // Fallback
    }
  }

  async function savePersonalitySettings() {
    const s = parseInt(document.getElementById("ps-slider-sarcasm").value, 10);
    const w = parseInt(document.getElementById("ps-slider-warmth").value, 10);
    const t = parseInt(document.getElementById("ps-slider-tamil").value, 10);
    const v = document.getElementById("ps-select-verbosity").value;

    try {
      const res = await fetch("/api/personality", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: activeMode,
          sarcasm: s,
          warmth: w,
          tamil_mix: t,
          verbosity: v,
        }),
      });
      if (res.ok) {
        window.NallaApp.showToast("Personality settings saved & applied live!", "success");
      } else {
        window.NallaApp.showToast("Error saving personality", "alert");
      }
    } catch (e) {
      window.NallaApp.showToast("Network error saving personality", "alert");
    }
  }

  // ------------------------------------------------------------------------
  // Persona Markdown Editor
  // ------------------------------------------------------------------------
  async function loadRawPersona() {
    try {
      const res = await fetch("/api/persona/raw");
      if (res.ok) {
        const data = await res.json();
        const editor = document.getElementById("ps-persona-editor");
        if (editor) editor.value = data.text;
      }
    } catch (e) {
      // Fallback
    }
  }

  async function saveRawPersona() {
    const editor = document.getElementById("ps-persona-editor");
    if (!editor) return;
    try {
      const res = await fetch("/api/persona/raw", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: editor.value }),
      });
      if (res.ok) {
        window.NallaApp.showToast("Persona markdown updated on disk!", "success");
      } else {
        window.NallaApp.showToast("Failed to save persona markdown.", "alert");
      }
    } catch (e) {
      window.NallaApp.showToast("Error saving persona.", "alert");
    }
  }

  async function revertRawPersona() {
    try {
      const res = await fetch("/api/persona/revert", { method: "POST" });
      if (res.ok) {
        await loadRawPersona();
        window.NallaApp.showToast("Reverted persona markdown to previous version.", "info");
      }
    } catch (e) {
      window.NallaApp.showToast("Error reverting persona.", "alert");
    }
  }

  // ------------------------------------------------------------------------
  // Test Chat Box (Uses unsaved sliders)
  // ------------------------------------------------------------------------
  async function sendTestChat() {
    const input = document.getElementById("ps-test-input");
    const output = document.getElementById("ps-test-output");
    const text = input ? input.value.trim() : "";
    if (!text) return;

    input.value = "";
    if (output) {
      output.innerHTML = `<span style="color:var(--text-dim);">Thinking with current slider values...</span>`;
    }

    const s = parseInt(document.getElementById("ps-slider-sarcasm").value, 10);
    const w = parseInt(document.getElementById("ps-slider-warmth").value, 10);
    const t = parseInt(document.getElementById("ps-slider-tamil").value, 10);
    const v = document.getElementById("ps-select-verbosity").value;

    try {
      const res = await fetch("/api/personality/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          mode: activeMode,
          sarcasm: s,
          warmth: w,
          tamil_mix: t,
          verbosity: v,
        }),
      });
      const data = await res.json();
      if (res.ok && output) {
        output.innerHTML = `<strong>Nalla:</strong> ${window.NallaApp.escapeHtml(data.reply)}`;
      } else if (output) {
        output.innerText = data.detail || "Error generating test response.";
      }
    } catch (err) {
      if (output) output.innerText = `Network error: ${err.message}`;
    }
  }

  window.NallaPages.personality = {
    init: initPersonality,
    onActivate: loadCurrentSettings,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPersonality);
  } else {
    initPersonality();
  }
})();
