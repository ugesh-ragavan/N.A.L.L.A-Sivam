/**
 * app/web/static/js/pages/voice_lab.js
 *
 * Tamil Voice Lab & Pronunciation Studio Controller (Prompts D & E):
 *   • Comparison Grid: Test sentences x up to 6 selectable voices.
 *   • Three text variants per sentence:
 *       (a) Tamil script
 *       (b) Tanglish in English letters
 *       (c) Tamil script after applying pronunciation dictionary
 *   • Rating per cell: Good / OK / Bad + issue tags + flag mispronounced word.
 *   • Summary view: Category averages, top issue tags, flagged words table,
 *     and 1-click "Set as Tamil Voice" / "Set as English Voice" recommendations.
 *   • Round-trip STT check (CER - Character Error Rate).
 *   • Live Dictionary Quick-Editor modal with auto re-render of variant (c).
 *   • Export results to CSV.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  let testSentences = [];
  let activeCategory = "all";
  let activeVariant = "a_tamil"; // "a_tamil" | "b_tanglish" | "c_dict"
  let selectedVoices = [
    { id: "ta-IN-PallaviNeural", engine: "edge-tts", label: "Pallavi (ta-IN)" },
    { id: "ta-IN-ValluvarNeural", engine: "edge-tts", label: "Valluvar (ta-IN)" },
    { id: "ta-LK-SaranyaNeural", engine: "edge-tts", label: "Saranya (ta-LK)" },
    { id: "ta-SG-VenbaNeural", engine: "edge-tts", label: "Venba (ta-SG)" },
    { id: "ta-MY-KaniNeural", engine: "edge-tts", label: "Kani (ta-MY)" },
    { id: "achird", engine: "gemini", label: "Achird (Gemini)" },
  ];
  let cellRatings = {}; // key: "sid:voice:variant" -> record
  let currentlyPlayingAudio = null;

  function initVoiceLab() {
    setupLabModalEvents();
    setupCategoryTabs();
    setupVariantTabs();
    setupDictEditor();
  }

  function openVoiceLab() {
    const modal = document.getElementById("voice-lab-modal");
    if (modal) {
      modal.classList.add("show");
      loadTestSentences().then(() => {
        loadLabSummary();
        renderComparisonGrid();
      });
    }
  }

  function closeVoiceLab() {
    const modal = document.getElementById("voice-lab-modal");
    if (modal) modal.classList.remove("show");
    stopAudio();
  }

  function stopAudio() {
    if (currentlyPlayingAudio) {
      try {
        currentlyPlayingAudio.pause();
        currentlyPlayingAudio = null;
      } catch (e) {}
    }
  }

  // -------------------------------------------------------------------------
  // Modal & Navigation Setup
  // -------------------------------------------------------------------------
  function setupLabModalEvents() {
    const closeBtn = document.getElementById("vl-close-btn");
    const exportBtn = document.getElementById("vl-export-csv-btn");
    const refreshBtn = document.getElementById("vl-refresh-btn");
    const openDictBtn = document.getElementById("vl-open-dict-btn");
    const openAbBtn = document.getElementById("vl-open-ab-btn");

    if (closeBtn) closeBtn.addEventListener("click", closeVoiceLab);
    if (openAbBtn) {
      openAbBtn.addEventListener("click", () => {
        if (window.NallaVoiceAB && window.NallaVoiceAB.openModal) {
          window.NallaVoiceAB.openModal();
        }
      });
    }
    if (exportBtn) {
      exportBtn.addEventListener("click", () => {
        window.location.href = "/api/voice/lab/export";
      });
    }
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        loadTestSentences().then(() => {
          loadLabSummary();
          renderComparisonGrid();
          window.NallaApp.showToast("Voice Lab refreshed.", "info");
        });
      });
    }
    if (openDictBtn) {
      openDictBtn.addEventListener("click", () => openDictEditor());
    }

    // Escape key closes modal
    window.addEventListener("keydown", (e) => {
      const modal = document.getElementById("voice-lab-modal");
      if (e.key === "Escape" && modal && modal.classList.contains("show")) {
        closeVoiceLab();
      }
    });
  }

  function setupCategoryTabs() {
    const strip = document.getElementById("vl-category-tabs");
    if (!strip) return;
    strip.querySelectorAll(".vl-cat-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        strip.querySelectorAll(".vl-cat-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        activeCategory = btn.dataset.category || "all";
        renderComparisonGrid();
      });
    });
  }

  function setupVariantTabs() {
    const strip = document.getElementById("vl-variant-tabs");
    if (!strip) return;
    strip.querySelectorAll(".vl-var-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        strip.querySelectorAll(".vl-var-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        activeVariant = btn.dataset.variant || "a_tamil";
        renderComparisonGrid();
      });
    });
  }

  // -------------------------------------------------------------------------
  // Data Loading
  // -------------------------------------------------------------------------
  async function loadTestSentences() {
    try {
      const res = await fetch("/api/voice/lab/tests");
      if (res.ok) {
        const data = await res.json();
        testSentences = data.sentences || [];
      }
    } catch (e) {
      console.warn("Failed loading voice lab tests:", e);
    }
  }

  async function loadLabSummary() {
    try {
      const res = await fetch("/api/voice/lab/summary");
      if (!res.ok) return;
      const data = await res.json();
      renderSummaryCards(data);
    } catch (e) {
      console.warn("Failed loading lab summary:", e);
    }
  }

  // -------------------------------------------------------------------------
  // Comparison Grid Rendering
  // -------------------------------------------------------------------------
  function renderComparisonGrid() {
    const gridEl = document.getElementById("vl-comparison-grid");
    if (!gridEl) return;

    const filtered = activeCategory === "all"
      ? testSentences
      : testSentences.filter((s) => (s.category || "").toLowerCase() === activeCategory.toLowerCase());

    if (filtered.length === 0) {
      gridEl.innerHTML = '<div style="color:var(--text-dim); padding:30px; text-align:center;">No sentences found in this category.</div>';
      return;
    }

    let html = `
      <div class="vl-table-container" style="overflow-x:auto;">
        <table class="vl-grid-table" style="width:100%; border-collapse:collapse; font-size:0.82rem;">
          <thead>
            <tr style="background:rgba(255,255,255,0.04); border-bottom:1px solid var(--border-subtle); text-align:left;">
              <th style="padding:10px 12px; width:280px; min-width:240px;">Test Sentence (${activeVariant.replace("_", " ").toUpperCase()})</th>
              ${selectedVoices.map((v) => `
                <th style="padding:10px 12px; min-width:210px; border-left:1px solid var(--border-subtle);">
                  <div style="font-weight:700; color:var(--text-bright);">${escapeHtml(v.label)}</div>
                  <div style="font-size:0.7rem; color:var(--text-dim); text-transform:uppercase;">${escapeHtml(v.engine)}</div>
                </th>
              `).join("")}
            </tr>
          </thead>
          <tbody>
    `;

    filtered.forEach((s) => {
      const variants = s.variants || {};
      const displayText = variants[activeVariant] || s.tamil || s.tanglish || "";

      html += `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
          <td style="padding:12px; vertical-align:top;">
            <div style="display:flex; gap:6px; align-items:center; margin-bottom:4px;">
              <span class="badge" style="font-size:0.7rem; background:rgba(0,255,200,0.1); color:var(--accent);">${escapeHtml(s.id)}</span>
              <span style="font-size:0.72rem; color:var(--text-dim); text-transform:uppercase;">${escapeHtml(s.category)}</span>
            </div>
            <div style="font-size:0.9rem; color:var(--text-bright); line-height:1.45; font-weight:500;">
              ${escapeHtml(displayText)}
            </div>
            <div style="font-size:0.75rem; color:var(--text-dim); margin-top:4px; font-style:italic;">
              ${escapeHtml(s.tanglish)}
            </div>
          </td>
          ${selectedVoices.map((v) => {
            const cellKey = `${s.id}:${v.id}:${activeVariant}`;
            return `
              <td style="padding:12px; vertical-align:top; border-left:1px solid rgba(255,255,255,0.05);" id="cell-${cellKey}">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                  <button class="btn btn-xs btn-outline vl-play-cell-btn" data-sid="${s.id}" data-voice="${v.id}" data-engine="${v.engine}" data-variant="${activeVariant}">
                    ▶️ Play
                  </button>
                  <span class="vl-latency-badge" id="lat-${cellKey}" style="font-size:0.7rem; color:var(--text-dim); font-family:var(--font-mono);">-- ms</span>
                </div>
                
                <!-- Rating buttons -->
                <div style="display:flex; gap:4px; margin-bottom:6px;">
                  <button class="btn btn-xs vl-rate-btn" data-rating="Good" data-sid="${s.id}" data-voice="${v.id}" data-engine="${v.engine}" data-variant="${activeVariant}" style="flex:1; padding:2px; font-size:0.72rem;">Good</button>
                  <button class="btn btn-xs vl-rate-btn" data-rating="OK" data-sid="${s.id}" data-voice="${v.id}" data-engine="${v.engine}" data-variant="${activeVariant}" style="flex:1; padding:2px; font-size:0.72rem;">OK</button>
                  <button class="btn btn-xs vl-rate-btn" data-rating="Bad" data-sid="${s.id}" data-voice="${v.id}" data-engine="${v.engine}" data-variant="${activeVariant}" style="flex:1; padding:2px; font-size:0.72rem;">Bad</button>
                </div>

                <!-- Issue Tags Dropdown/Chips -->
                <div style="display:flex; flex-wrap:wrap; gap:3px; margin-bottom:6px;">
                  <button class="vl-tag-chip" data-tag="wrong stress" data-sid="${s.id}" data-voice="${v.id}" data-engine="${v.engine}" data-variant="${activeVariant}">stress</button>
                  <button class="vl-tag-chip" data-tag="English accent" data-sid="${s.id}" data-voice="${v.id}" data-engine="${v.engine}" data-variant="${activeVariant}">accent</button>
                  <button class="vl-tag-chip" data-tag="garbled" data-sid="${s.id}" data-voice="${v.id}" data-engine="${v.engine}" data-variant="${activeVariant}">garbled</button>
                  <button class="vl-tag-chip" data-tag="too formal" data-sid="${s.id}" data-voice="${v.id}" data-engine="${v.engine}" data-variant="${activeVariant}">bookish</button>
                </div>

                <!-- Round trip CER badge -->
                <div style="display:flex; justify-content:space-between; align-items:center; font-size:0.7rem;">
                  <span id="cer-${cellKey}" style="color:var(--text-dim);">CER: --</span>
                  <button class="btn btn-xs btn-outline vl-roundtrip-btn" data-sid="${s.id}" data-voice="${v.id}" data-engine="${v.engine}" data-variant="${activeVariant}" style="font-size:0.65rem; padding:1px 4px;" title="Check with Whisper STT">
                    🔄 CER
                  </button>
                </div>
              </td>
            `;
          }).join("")}
        </tr>
      `;
    });

    html += "</tbody></table></div>";
    gridEl.innerHTML = html;

    attachGridEventListeners();
  }

  function attachGridEventListeners() {
    // Play button
    document.querySelectorAll(".vl-play-cell-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const sid = btn.dataset.sid;
        const voice = btn.dataset.voice;
        const engine = btn.dataset.engine;
        const variant = btn.dataset.variant;
        const cellKey = `${sid}:${voice}:${variant}`;
        const latEl = document.getElementById(`lat-${cellKey}`);

        stopAudio();
        btn.disabled = true;
        btn.innerHTML = "⏳ ...";

        try {
          const res = await fetch("/api/voice/lab/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              sentence_id: sid,
              variant: variant,
              voice_id: voice,
              engine: engine,
            }),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();

          if (latEl && data.time_to_first_audio_ms !== undefined) {
            latEl.innerText = `${Math.round(data.time_to_first_audio_ms)}ms`;
          }

          if (data.audio_url) {
            btn.innerHTML = "🔊 Playing";
            const audio = new Audio(data.audio_url);
            currentlyPlayingAudio = audio;
            audio.play();
            audio.onended = () => {
              btn.disabled = false;
              btn.innerHTML = "▶️ Play";
            };
            audio.onerror = () => {
              btn.disabled = false;
              btn.innerHTML = "▶️ Play";
            };
          } else {
            btn.disabled = false;
            btn.innerHTML = "▶️ Play";
          }
        } catch (err) {
          btn.disabled = false;
          btn.innerHTML = "▶️ Play";
          window.NallaApp.showToast(`Play failed: ${err.message}`, "alert");
        }
      });
    });

    // Rating buttons
    document.querySelectorAll(".vl-rate-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const rating = btn.dataset.rating;
        const sid = btn.dataset.sid;
        const voice = btn.dataset.voice;
        const engine = btn.dataset.engine;
        const variant = btn.dataset.variant;

        btn.parentElement.querySelectorAll(".vl-rate-btn").forEach((b) => {
          b.style.background = "";
          b.style.color = "";
        });

        if (rating === "Good") {
          btn.style.background = "var(--green)";
          btn.style.color = "#000";
        } else if (rating === "OK") {
          btn.style.background = "var(--amber)";
          btn.style.color = "#000";
        } else {
          btn.style.background = "var(--red)";
          btn.style.color = "#fff";
        }

        try {
          await fetch("/api/voice/lab/rate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              sentence_id: sid,
              voice_id: voice,
              engine: engine,
              variant: variant,
              rating: rating,
            }),
          });
          loadLabSummary();
        } catch (e) {
          console.warn("Rating save failed:", e);
        }
      });
    });

    // Issue Tag Chips
    document.querySelectorAll(".vl-tag-chip").forEach((chip) => {
      chip.addEventListener("click", async () => {
        chip.classList.toggle("active");
        if (chip.classList.contains("active")) {
          chip.style.background = "var(--accent)";
          chip.style.color = "#000";
        } else {
          chip.style.background = "";
          chip.style.color = "";
        }

        const sid = chip.dataset.sid;
        const voice = chip.dataset.voice;
        const engine = chip.dataset.engine;
        const variant = chip.dataset.variant;
        const cell = chip.closest("td");
        const activeTags = Array.from(cell.querySelectorAll(".vl-tag-chip.active")).map((c) => c.dataset.tag);

        try {
          await fetch("/api/voice/lab/rate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              sentence_id: sid,
              voice_id: voice,
              engine: engine,
              variant: variant,
              rating: "OK",
              issue_tags: activeTags,
            }),
          });
          loadLabSummary();
        } catch (e) {}
      });
    });

    // Round Trip CER Button
    document.querySelectorAll(".vl-roundtrip-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const sid = btn.dataset.sid;
        const voice = btn.dataset.voice;
        const engine = btn.dataset.engine;
        const variant = btn.dataset.variant;
        const cellKey = `${sid}:${voice}:${variant}`;
        const cerEl = document.getElementById(`cer-${cellKey}`);

        btn.disabled = true;
        btn.innerText = "⏳";

        const filename = `${sid}_${variant}_${engine.replace(/[^\w\-]/g, "_")}_${voice.replace(/[^\w\-]/g, "_")}.wav`;
        const sentenceObj = testSentences.find((s) => s.id === sid) || {};
        const expected = (sentenceObj.variants && sentenceObj.variants[variant]) || sentenceObj.tamil || sentenceObj.tanglish || "";

        try {
          const res = await fetch("/api/voice/lab/round-trip", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              clip_filename: filename,
              expected_text: expected,
            }),
          });
          if (res.ok) {
            const data = await res.json();
            const pct = Math.round((data.cer || 0) * 100);
            if (cerEl) {
              cerEl.innerText = `CER: ${pct}%`;
              cerEl.style.color = pct < 20 ? "var(--green)" : (pct < 40 ? "var(--amber)" : "var(--red)");
              if (data.flagged) {
                cerEl.innerText = `⚠️ CER: ${pct}%`;
              }
            }
          }
        } catch (e) {
          if (cerEl) cerEl.innerText = "CER: err";
        } finally {
          btn.disabled = false;
          btn.innerText = "🔄 CER";
        }
      });
    });
  }

  // -------------------------------------------------------------------------
  // Summary & Voice Recommendations
  // -------------------------------------------------------------------------
  function renderSummaryCards(summary) {
    const recs = summary.recommendations || {};
    const bestTa = recs.best_tamil_voice || "ta-IN-PallaviNeural";
    const bestTaScore = recs.best_tamil_score !== null ? `${Math.round(recs.best_tamil_score * 100)}%` : "Not rated yet";
    const bestEn = recs.best_english_voice || "achird";
    const bestEnScore = recs.best_english_score !== null ? `${Math.round(recs.best_english_score * 100)}%` : "Not rated yet";

    const taRecEl = document.getElementById("vl-rec-tamil");
    const enRecEl = document.getElementById("vl-rec-english");
    const totalEl = document.getElementById("vl-total-ratings");

    if (taRecEl) {
      taRecEl.innerHTML = `
        <div style="font-size:0.75rem; color:var(--text-dim); text-transform:uppercase;">RECOMMENDED TAMIL VOICE</div>
        <div style="font-size:1.05rem; font-weight:700; color:var(--text-bright); margin:2px 0;">${escapeHtml(bestTa)}</div>
        <div style="font-size:0.78rem; color:var(--green); margin-bottom:8px;">Score: ${bestTaScore}</div>
        <button class="btn btn-xs btn-primary" id="vl-set-tamil-voice-btn" data-voice="${bestTa}">
          ★ Set as Tamil Voice
        </button>
      `;
      const btn = document.getElementById("vl-set-tamil-voice-btn");
      if (btn) {
        btn.addEventListener("click", () => setVoiceFromLab("ta", bestTa));
      }
    }

    if (enRecEl) {
      enRecEl.innerHTML = `
        <div style="font-size:0.75rem; color:var(--text-dim); text-transform:uppercase;">RECOMMENDED ENGLISH VOICE</div>
        <div style="font-size:1.05rem; font-weight:700; color:var(--text-bright); margin:2px 0;">${escapeHtml(bestEn)}</div>
        <div style="font-size:0.78rem; color:var(--accent); margin-bottom:8px;">Score: ${bestEnScore}</div>
        <button class="btn btn-xs btn-outline" id="vl-set-english-voice-btn" data-voice="${bestEn}">
          ★ Set as English Voice
        </button>
      `;
      const btn = document.getElementById("vl-set-english-voice-btn");
      if (btn) {
        btn.addEventListener("click", () => setVoiceFromLab("en", bestEn));
      }
    }

    if (totalEl) totalEl.innerText = `${summary.total_ratings || 0} Ratings`;
  }

  async function setVoiceFromLab(slot, voiceId) {
    try {
      const res = await fetch("/api/voice/lab/set-voice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slot: slot, voice_id: voiceId }),
      });
      if (res.ok) {
        window.NallaApp.showToast(`Set ${slot.toUpperCase()} voice to '${voiceId}' live!`, "success");
      }
    } catch (e) {
      window.NallaApp.showToast(`Failed setting voice: ${e.message}`, "alert");
    }
  }

  // -------------------------------------------------------------------------
  // Tanglish Pronunciation Dictionary Live Editor Modal (Prompt E)
  // -------------------------------------------------------------------------
  let dictMapping = {};

  function setupDictEditor() {
    const modal = document.getElementById("dict-editor-modal");
    const closeBtn = document.getElementById("dict-close-btn");
    const addBtn = document.getElementById("dict-add-btn");
    const saveBtn = document.getElementById("dict-save-btn");

    if (closeBtn && modal) {
      closeBtn.addEventListener("click", () => modal.classList.remove("show"));
    }

    if (addBtn) {
      addBtn.addEventListener("click", () => {
        const keyInp = document.getElementById("dict-new-tanglish");
        const valInp = document.getElementById("dict-new-tamil");
        const key = keyInp ? keyInp.value.trim().toLowerCase() : "";
        const val = valInp ? valInp.value.trim() : "";
        if (!key || !val) {
          alert("Please enter both Tanglish and Tamil script.");
          return;
        }
        dictMapping[key] = val;
        renderDictTable();
        if (keyInp) keyInp.value = "";
        if (valInp) valInp.value = "";
      });
    }

    if (saveBtn) {
      saveBtn.addEventListener("click", async () => {
        saveBtn.disabled = true;
        saveBtn.innerText = "Saving...";
        try {
          const res = await fetch("/api/voice/tanglish-dict", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mapping: dictMapping }),
          });
          if (res.ok) {
            window.NallaApp.showToast("Tanglish Pronunciation Dictionary saved live!", "success");
            modal.classList.remove("show");
            // Re-render lab grid so variant (c) is updated immediately
            loadTestSentences().then(() => renderComparisonGrid());
          }
        } catch (e) {
          window.NallaApp.showToast("Failed saving dictionary: " + e.message, "alert");
        } finally {
          saveBtn.disabled = false;
          saveBtn.innerText = "Save Live";
        }
      });
    }
  }

  async function openDictEditor(prefillKey = "") {
    const modal = document.getElementById("dict-editor-modal");
    if (!modal) return;
    try {
      const res = await fetch("/api/voice/tanglish-dict");
      if (res.ok) {
        const data = await res.json();
        dictMapping = data.mapping || {};
        renderDictTable();
      }
    } catch (e) {}

    const keyInp = document.getElementById("dict-new-tanglish");
    if (keyInp && prefillKey) {
      keyInp.value = prefillKey;
    }

    modal.classList.add("show");
  }

  function renderDictTable() {
    const tbody = document.getElementById("dict-table-body");
    if (!tbody) return;

    const entries = Object.entries(dictMapping);
    if (entries.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--text-dim); padding:20px;">Dictionary is empty.</td></tr>';
      return;
    }

    tbody.innerHTML = entries.map(([tanglish, tamil]) => `
      <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
        <td style="padding:6px 10px; font-family:var(--font-mono); color:var(--accent); font-weight:600;">${escapeHtml(tanglish)}</td>
        <td style="padding:6px 10px; font-weight:600; color:var(--text-bright);">${escapeHtml(tamil)}</td>
        <td style="padding:6px 10px; text-align:right;">
          <button class="btn btn-xs btn-outline btn-red dict-del-btn" data-key="${escapeHtml(tanglish)}" style="padding:2px 6px;">🗑️</button>
        </td>
      </tr>
    `).join("");

    tbody.querySelectorAll(".dict-del-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const k = btn.dataset.key;
        delete dictMapping[k];
        renderDictTable();
      });
    });
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Expose API
  window.NallaPages.voiceLab = {
    open: openVoiceLab,
    close: closeVoiceLab,
    openDict: openDictEditor,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initVoiceLab);
  } else {
    initVoiceLab();
  }
})();
