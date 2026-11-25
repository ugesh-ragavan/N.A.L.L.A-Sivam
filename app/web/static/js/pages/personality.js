/**
 * app/web/static/js/pages/personality.js
 *
 * Personality Studio 6D Controller:
 *   • Sliders: Sarcasm (0-10), Warmth (0-10), Tamil mix (0-100%), Verbosity (very short / short / normal).
 *   • Mode selector: Friend, Jarvis, Focus, Engineer.
 *   • Presets: "Cool Friend", "Pure Jarvis", "Roast Mode", "Calm Night".
 *   • Master persona text editor with Save & Revert (persona/nalla_master.md).
 *   • Few-shot studio: read-only JSON viewer + editor for its 8 canonical examples.
 *   • Live prompt preview: exact final system prompt, personality block, and real-time mood override.
 *   • Test Chat box answering with unsaved slider settings.
 *   • Model Comparison: side-by-side comparison between active chat_model and a picked second model.
 *   • Locked Identity Guard item awareness.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  const PRESETS = {
    friend: { mode: "Friend", sarcasm: 7, warmth: 8, tamil_mix: 40, verbosity: "very short" },
    jarvis: { mode: "Jarvis", sarcasm: 3, warmth: 6, tamil_mix: 10, verbosity: "short" },
    roast: { mode: "Friend", sarcasm: 10, warmth: 1, tamil_mix: 60, verbosity: "short" },
    calm: { mode: "Focus", sarcasm: 0, warmth: 10, tamil_mix: 10, verbosity: "very short" },
  };

  let activeMode = "Friend";
  let previewDebounceTimer = null;
  let fewShotCache = [];
  let isDirty = false;

  function initPersonality() {
    // 1. Presets
    document.querySelectorAll(".persona-preset-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pKey = btn.dataset.preset;
        if (PRESETS[pKey]) applyPreset(PRESETS[pKey]);
      });
    });

    // 2. Modes
    document.querySelectorAll(".mode-select-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        setMode(btn.dataset.mode);
        markDirty();
        debouncedPromptPreview();
      });
    });

    // 3. Sliders & Verbosity
    const sSarcasm = document.getElementById("ps-slider-sarcasm");
    const sWarmth = document.getElementById("ps-slider-warmth");
    const sTamil = document.getElementById("ps-slider-tamil");
    const sVerb = document.getElementById("ps-select-verbosity");

    [sSarcasm, sWarmth, sTamil, sVerb].forEach((el) => {
      if (el) {
        el.addEventListener("input", () => {
          onSliderUpdate();
          markDirty();
          debouncedPromptPreview();
        });
      }
    });

    // 4. Save Personality Settings
    const saveBtn = document.getElementById("ps-save-btn");
    if (saveBtn) {
      saveBtn.addEventListener("click", savePersonalitySettings);
    }

    // 5. Live Prompt Preview controls & Mood tester
    const sampleMoodInput = document.getElementById("ps-sample-user-text");
    if (sampleMoodInput) {
      sampleMoodInput.addEventListener("input", debouncedPromptPreview);
    }

    const refreshPreviewBtn = document.getElementById("ps-refresh-preview-btn");
    if (refreshPreviewBtn) {
      refreshPreviewBtn.addEventListener("click", fetchLivePromptPreview);
    }

    // Preview Tabs
    document.querySelectorAll(".preview-tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".preview-tab-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const tab = btn.dataset.tab;
        document.querySelectorAll(".preview-tab-content").forEach((el) => {
          el.style.display = "none";
        });
        const target = document.getElementById(`ps-preview-tab-${tab}`);
        if (target) target.style.display = "block";
      });
    });

    // 6. Test Chat & Compare
    const testSendBtn = document.getElementById("ps-test-send-btn");
    const testInput = document.getElementById("ps-test-input");
    const compareBtn = document.getElementById("ps-compare-btn");

    if (testSendBtn && testInput) {
      testSendBtn.addEventListener("click", sendTestChat);
      testInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") sendTestChat();
      });
    }

    if (compareBtn) {
      compareBtn.addEventListener("click", sendCompareChat);
    }

    // 7. Master Persona Tabs (Persona vs Few-Shot)
    document.querySelectorAll(".persona-tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".persona-tab-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const tab = btn.dataset.tab;
        const personaSub = document.getElementById("ps-subpanel-persona");
        const fewshotSub = document.getElementById("ps-subpanel-fewshot");
        if (tab === "persona") {
          if (personaSub) personaSub.style.display = "block";
          if (fewshotSub) fewshotSub.style.display = "none";
        } else {
          if (personaSub) personaSub.style.display = "none";
          if (fewshotSub) fewshotSub.style.display = "block";
          loadFewShotExamples();
        }
      });
    });

    // Persona text editor buttons
    const savePersonaBtn = document.getElementById("ps-save-persona-btn");
    const revertPersonaBtn = document.getElementById("ps-revert-persona-btn");
    if (savePersonaBtn) savePersonaBtn.addEventListener("click", saveRawPersona);
    if (revertPersonaBtn) revertPersonaBtn.addEventListener("click", revertRawPersona);

    // Few-Shot controls
    const toggleJsonBtn = document.getElementById("ps-toggle-raw-json-btn");
    const saveFewshotBtn = document.getElementById("ps-save-fewshot-btn");
    const revertFewshotBtn = document.getElementById("ps-revert-fewshot-btn");
    if (toggleJsonBtn) toggleJsonBtn.addEventListener("click", toggleRawJson);
    if (saveFewshotBtn) saveFewshotBtn.addEventListener("click", saveFewShotExamples);
    if (revertFewshotBtn) revertFewshotBtn.addEventListener("click", revertFewShotExamples);

    // Coach Mode toggle in personality
    const coachToggle = document.getElementById("ps-toggle-coach-mode");
    if (coachToggle) {
      coachToggle.addEventListener("change", async (e) => {
        try {
          await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ coach_mode: e.target.checked }),
          });
          if (window.NallaApp && window.NallaApp.showToast) {
            window.NallaApp.showToast(`Coach mode ${e.target.checked ? "enabled" : "disabled"}.`, "info");
          }
        } catch (err) {}
      });
    }

    // Initial load
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
    markDirty();
    debouncedPromptPreview();
    if (window.NallaApp && window.NallaApp.showToast) {
      window.NallaApp.showToast(`Applied preset. Audition in Test Chat before saving.`, "info");
    }
  }

  function onSliderUpdate() {
    const s = document.getElementById("ps-slider-sarcasm")?.value || "7";
    const w = document.getElementById("ps-slider-warmth")?.value || "8";
    const t = document.getElementById("ps-slider-tamil")?.value || "40";
    const v = document.getElementById("ps-select-verbosity")?.value || "very short";

    const valS = document.getElementById("ps-val-sarcasm");
    const valW = document.getElementById("ps-val-warmth");
    const valT = document.getElementById("ps-val-tamil");

    if (valS) valS.innerText = `${s}/10`;
    if (valW) valW.innerText = `${w}/10`;
    if (valT) valT.innerText = `${t}%`;

    const preview = `Sarcasm: ${s}/10. Warmth: ${w}/10. Tamil mix: ${t}%. Replies: ${v}.`;
    const box = document.getElementById("ps-prompt-preview-box");
    if (box) box.innerText = preview;
  }

  function markDirty() {
    isDirty = true;
    const badge = document.getElementById("ps-unsaved-badge");
    if (badge) badge.style.display = "inline-block";
  }

  function markClean() {
    isDirty = false;
    const badge = document.getElementById("ps-unsaved-badge");
    if (badge) badge.style.display = "none";
  }

  function debouncedPromptPreview() {
    if (previewDebounceTimer) clearTimeout(previewDebounceTimer);
    previewDebounceTimer = setTimeout(fetchLivePromptPreview, 250);
  }

  // ------------------------------------------------------------------------
  // Live Prompt Preview & Real-Time Mood Detection
  // ------------------------------------------------------------------------
  async function fetchLivePromptPreview() {
    const s = parseInt(document.getElementById("ps-slider-sarcasm")?.value || 7, 10);
    const w = parseInt(document.getElementById("ps-slider-warmth")?.value || 8, 10);
    const t = parseInt(document.getElementById("ps-slider-tamil")?.value || 40, 10);
    const v = document.getElementById("ps-select-verbosity")?.value || "very short";
    const sampleMsg = document.getElementById("ps-sample-user-text")?.value || "";

    try {
      const res = await fetch("/api/personality/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: sampleMsg || "Hello Nalla",
          mode: activeMode,
          sarcasm: s,
          warmth: w,
          tamil_mix: t,
          verbosity: v,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        const fullView = document.getElementById("ps-full-prompt-view");
        const blockView = document.getElementById("ps-personality-block-view");
        const moodView = document.getElementById("ps-mood-override-view");
        const indicator = document.getElementById("ps-stress-indicator");

        if (fullView) fullView.innerText = data.full_prompt || "";
        if (blockView) blockView.innerText = data.personality_block || "";
        if (moodView) {
          moodView.innerText = data.mood_override || "(No mood override active)";
          moodView.style.color = data.stress_detected ? "var(--red)" : "var(--amber)";
        }
        if (indicator) {
          if (data.stress_detected) {
            indicator.className = "badge badge-red";
            indicator.innerText = "Mood: Stressed / Exhausted";
          } else {
            indicator.className = "badge badge-outline";
            indicator.innerText = "Mood: Neutral";
          }
        }
      }
    } catch (err) {
      console.warn("Could not fetch prompt preview:", err);
    }
  }

  async function loadCurrentSettings() {
    try {
      const res = await fetch("/api/personality");
      if (res.ok) {
        const data = await res.json();
        setMode(data.mode || "Friend");
        const sSarcasm = document.getElementById("ps-slider-sarcasm");
        const sWarmth = document.getElementById("ps-slider-warmth");
        const sTamil = document.getElementById("ps-slider-tamil");
        const sVerb = document.getElementById("ps-select-verbosity");
        const coachToggle = document.getElementById("ps-toggle-coach-mode");

        if (sSarcasm) sSarcasm.value = data.sarcasm;
        if (sWarmth) sWarmth.value = data.warmth;
        if (sTamil) sTamil.value = data.tamil_mix;
        if (sVerb) sVerb.value = data.verbosity;
        if (coachToggle && data.coach_mode !== undefined) coachToggle.checked = data.coach_mode;

        onSliderUpdate();
        markClean();
        fetchLivePromptPreview();
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
        markClean();
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Personality settings saved & applied live!", "success");
        }
        fetchLivePromptPreview();
      } else {
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Error saving personality", "alert");
        }
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) {
        window.NallaApp.showToast("Network error saving personality", "alert");
      }
    }
  }

  // ------------------------------------------------------------------------
  // Master Persona Editor (persona/nalla_master.md)
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
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Persona markdown updated on disk!", "success");
        }
        fetchLivePromptPreview();
      } else {
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Failed to save persona markdown.", "alert");
        }
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) {
        window.NallaApp.showToast("Error saving persona.", "alert");
      }
    }
  }

  async function revertRawPersona() {
    try {
      const res = await fetch("/api/persona/revert", { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        const editor = document.getElementById("ps-persona-editor");
        if (editor) editor.value = data.text;
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Reverted persona markdown to default baseline.", "info");
        }
        fetchLivePromptPreview();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) {
        window.NallaApp.showToast("Error reverting persona.", "alert");
      }
    }
  }

  // ------------------------------------------------------------------------
  // Few-Shot Canonical Examples Studio (8 pairs)
  // ------------------------------------------------------------------------
  async function loadFewShotExamples() {
    try {
      const res = await fetch("/api/fewshot");
      if (res.ok) {
        const data = await res.json();
        fewShotCache = data.examples || [];
        renderFewShotCards();
        const rawJsonEl = document.getElementById("ps-fewshot-raw-json");
        if (rawJsonEl) rawJsonEl.innerText = data.raw_json || JSON.stringify(fewShotCache, null, 2);
      }
    } catch (err) {
      console.warn("Error loading few-shot examples:", err);
    }
  }

  function renderFewShotCards() {
    const grid = document.getElementById("ps-fewshot-cards-grid");
    if (!grid) return;
    if (!fewShotCache || fewShotCache.length === 0) {
      grid.innerHTML = `<div style="color:var(--text-dim); padding:20px;">No few-shot examples found.</div>`;
      return;
    }

    grid.innerHTML = fewShotCache
      .map((ex, idx) => {
        const safeUser = (ex.user || "").replace(/"/g, "&quot;");
        const safeModel = ex.model || "";
        return `
          <div class="fewshot-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="badge badge-accent" style="font-size:0.7rem;">Pair #${idx + 1}</span>
              <span style="font-size:0.7rem; color:var(--text-dim);">Canonical Example</span>
            </div>
            <div>
              <div style="font-size:0.72rem; color:var(--text-dim); margin-bottom:2px; text-transform:uppercase;">User Prompt</div>
              <input type="text" class="fs-input-user" data-idx="${idx}" value="${safeUser}" style="width:100%; font-size:0.8rem; padding:4px 8px;">
            </div>
            <div>
              <div style="font-size:0.72rem; color:var(--text-dim); margin-bottom:2px; text-transform:uppercase;">Nalla Reply</div>
              <textarea class="fs-input-model" data-idx="${idx}" rows="2" style="width:100%; font-size:0.8rem; padding:4px 8px; font-family:var(--font-sans);">${safeModel}</textarea>
            </div>
          </div>
        `;
      })
      .join("");

    // Wire input changes to in-memory fewShotCache
    grid.querySelectorAll(".fs-input-user").forEach((inp) => {
      inp.addEventListener("input", (e) => {
        const idx = parseInt(e.target.dataset.idx, 10);
        if (fewShotCache[idx]) {
          fewShotCache[idx].user = e.target.value;
          updateRawJsonView();
        }
      });
    });

    grid.querySelectorAll(".fs-input-model").forEach((inp) => {
      inp.addEventListener("input", (e) => {
        const idx = parseInt(e.target.dataset.idx, 10);
        if (fewShotCache[idx]) {
          fewShotCache[idx].model = e.target.value;
          updateRawJsonView();
        }
      });
    });
  }

  function updateRawJsonView() {
    const rawJsonEl = document.getElementById("ps-fewshot-raw-json");
    if (rawJsonEl) rawJsonEl.innerText = JSON.stringify(fewShotCache, null, 2);
  }

  function toggleRawJson() {
    const container = document.getElementById("ps-raw-json-container");
    const btn = document.getElementById("ps-toggle-raw-json-btn");
    if (!container) return;
    const isHidden = container.style.display === "none";
    container.style.display = isHidden ? "block" : "none";
    if (btn) btn.innerText = isHidden ? "✕ Hide Raw JSON" : "👁 View Raw JSON";
  }

  async function saveFewShotExamples() {
    try {
      const res = await fetch("/api/fewshot", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ examples: fewShotCache }),
      });
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Saved 8 few-shot canonical examples to disk!", "success");
        }
      } else {
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Error saving few-shot examples.", "alert");
        }
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) {
        window.NallaApp.showToast("Network error saving few-shot examples.", "alert");
      }
    }
  }

  async function revertFewShotExamples() {
    try {
      const res = await fetch("/api/fewshot/revert", { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        fewShotCache = data.examples || [];
        renderFewShotCards();
        updateRawJsonView();
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Reverted few-shot examples to canonical defaults.", "info");
        }
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) {
        window.NallaApp.showToast("Error reverting few-shot examples.", "alert");
      }
    }
  }

  // ------------------------------------------------------------------------
  // Test Chat & Model Comparison
  // ------------------------------------------------------------------------
  async function sendTestChat() {
    const input = document.getElementById("ps-test-input");
    const output = document.getElementById("ps-test-output");
    const singleContainer = document.getElementById("ps-single-output-container");
    const compareContainer = document.getElementById("ps-compare-output-container");

    const text = input ? input.value.trim() : "";
    if (!text) return;

    if (singleContainer) singleContainer.style.display = "block";
    if (compareContainer) compareContainer.style.display = "none";

    if (output) {
      output.innerHTML = `<span style="color:var(--text-dim);">Thinking with unsaved sliders (${activeMode} mode)...</span>`;
    }

    const s = parseInt(document.getElementById("ps-slider-sarcasm")?.value || 7, 10);
    const w = parseInt(document.getElementById("ps-slider-warmth")?.value || 8, 10);
    const t = parseInt(document.getElementById("ps-slider-tamil")?.value || 40, 10);
    const v = document.getElementById("ps-select-verbosity")?.value || "very short";

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
        const lat = data.latency_ms ? ` <span class="badge badge-accent" style="font-size:0.65rem; margin-left:6px;">${data.latency_ms} ms</span>` : "";
        output.innerHTML = `<strong>Nalla:</strong> ${escapeHtml(data.reply)}${lat}`;
      } else if (output) {
        output.innerText = data.detail || data.reply || "Error generating test response.";
      }
    } catch (err) {
      if (output) output.innerText = `Network error: ${err.message}`;
    }
  }

  async function sendCompareChat() {
    const input = document.getElementById("ps-test-input");
    const singleContainer = document.getElementById("ps-single-output-container");
    const compareContainer = document.getElementById("ps-compare-output-container");
    const modelBSelect = document.getElementById("ps-compare-model-select");

    const text = input ? input.value.trim() : "";
    if (!text) return;

    if (singleContainer) singleContainer.style.display = "none";
    if (compareContainer) compareContainer.style.display = "block";

    const titleA = document.getElementById("ps-comp-title-a");
    const titleB = document.getElementById("ps-comp-title-b");
    const latA = document.getElementById("ps-comp-lat-a");
    const latB = document.getElementById("ps-comp-lat-b");
    const bodyA = document.getElementById("ps-comp-body-a");
    const bodyB = document.getElementById("ps-comp-body-b");

    const modelBName = modelBSelect ? modelBSelect.value : "gemini-2.5-flash";

    if (titleA) titleA.innerText = "Model A (Active Chat Model)";
    if (titleB) titleB.innerText = `Model B (${modelBName})`;
    if (latA) latA.innerText = "Running...";
    if (latB) latB.innerText = "Running...";
    if (bodyA) bodyA.innerHTML = `<span style="color:var(--text-dim);">Generating response...</span>`;
    if (bodyB) bodyB.innerHTML = `<span style="color:var(--text-dim);">Generating response...</span>`;

    const s = parseInt(document.getElementById("ps-slider-sarcasm")?.value || 7, 10);
    const w = parseInt(document.getElementById("ps-slider-warmth")?.value || 8, 10);
    const t = parseInt(document.getElementById("ps-slider-tamil")?.value || 40, 10);
    const v = document.getElementById("ps-select-verbosity")?.value || "very short";

    try {
      const res = await fetch("/api/personality/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          mode: activeMode,
          sarcasm: s,
          warmth: w,
          tamil_mix: t,
          verbosity: v,
          model_b: modelBName,
        }),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        if (titleA) titleA.innerText = `Model A: ${data.model_a.name}`;
        if (titleB) titleB.innerText = `Model B: ${data.model_b.name}`;
        if (latA) latA.innerText = `${data.model_a.latency_ms} ms`;
        if (latB) latB.innerText = `${data.model_b.latency_ms} ms`;
        if (bodyA) bodyA.innerText = data.model_a.reply;
        if (bodyB) bodyB.innerText = data.model_b.reply;
      } else {
        if (bodyA) bodyA.innerText = "Error in comparison";
        if (bodyB) bodyB.innerText = data.detail || "Error in comparison";
      }
    } catch (err) {
      if (bodyA) bodyA.innerText = `Error: ${err.message}`;
      if (bodyB) bodyB.innerText = `Error: ${err.message}`;
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  window.NallaPages.personality = {
    init: initPersonality,
    onActivate: () => {
      loadCurrentSettings();
      loadRawPersona();
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPersonality);
  } else {
    initPersonality();
  }
})();
