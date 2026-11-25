/**
 * app/web/static/js/pages/voice.js
 *
 * Voice Studio Controller (Prompt 6C):
 *   • Engine & model selector with status light and today's TTS counter and cap.
 *   • Voice gallery of cards (name, character tag, favourite star, Preview button, playing animation).
 *   • Voice Library browser with filters (language, gender, pitch, context, search) and Preview buttons,
 *     default filter Tamil and English (India).
 *   • Custom voice_... ID field with preview and select.
 *   • Delivery style text box and speed presets (Relaxed / Normal / Quick).
 *   • Expressive-tags toggle that applies live.
 *   • Profile manager (up to 5 named profiles, one active, switchable from Command Palette).
 *   • Tamil pronunciation-map live editor with quick-add chips.
 *   • STT panel with Whisper model, language, mic device, live mic level meter, and Test mic.
 *   • Everything applies live without a restart.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  let currentProfiles = {};
  let activeProfileName = "Nalla Default";
  let audioContext = null;
  let currentPronunciationMap = {};
  let prebuiltVoicesList = [];
  let currentlyPlayingAudio = null;
  let currentlyPlayingBtn = null;
  let currentlyPlayingCard = null;

  function initVoiceStudio() {
    setupSliders();
    setupSpeedPresets();
    setupProfileControls();
    setupButtons();
    setupExpressiveTagsToggle();
    setupPronunciationChips();
    setupListenLanguageChips();
    loadDevices();
    loadVoiceList().then(() => {
      loadVoiceSettings();
    });
    loadVoiceStatus();
    setupExtendedLibrary();
    setupPronunciationMap();
    setupQuotaControls();
  }

  // -------------------------------------------------------------------------
  // Listen Languages — chip multi-selector
  // -------------------------------------------------------------------------

  /** Return the currently active ISO language codes from the chip strip. */
  function getListenLanguages() {
    const chips = document.querySelectorAll("#vs-listen-lang-chips .lang-chip.active");
    const langs = Array.from(chips).map((c) => c.dataset.lang).filter(Boolean);
    // If "auto" chip is active, return ["auto"] (unrestricted)
    if (langs.includes("auto")) return ["auto"];
    return langs.length > 0 ? langs : ["en", "ta"];
  }

  /** Set chip active state from a saved languages array. */
  function setListenLanguageChips(langs) {
    const chips = document.querySelectorAll("#vs-listen-lang-chips .lang-chip");
    const isAuto = langs.includes("auto") || langs.length === 0;
    chips.forEach((chip) => {
      const lang = chip.dataset.lang;
      const shouldBeActive = isAuto ? lang === "auto" : langs.includes(lang);
      toggleChipActive(chip, shouldBeActive);
    });
    updateListenLangHint();
  }

  /** Toggle visual active state on a chip button. */
  function toggleChipActive(chip, force) {
    const isActive = force !== undefined ? force : !chip.classList.contains("active");
    if (isActive) {
      chip.classList.add("active");
      chip.style.background = "var(--accent)";
      chip.style.color = "#fff";
      chip.style.borderColor = "var(--accent)";
    } else {
      chip.classList.remove("active");
      chip.style.background = "transparent";
      chip.style.color = "var(--text-dim)";
      chip.style.borderColor = "var(--border-subtle)";
    }
  }

  /** Update the hint label below the chip strip. */
  function updateListenLangHint() {
    const activeEl = document.getElementById("vs-listen-lang-active");
    if (!activeEl) return;
    const langs = getListenLanguages();
    const labels = {
      en: "English", ta: "Tamil", hi: "Hindi", fr: "French", de: "German", auto: "All Languages",
    };
    activeEl.textContent = langs.map((l) => labels[l] || l).join(", ");
  }

  /** Wire up chip click handlers. */
  function setupListenLanguageChips() {
    const container = document.getElementById("vs-listen-lang-chips");
    if (!container) return;
    container.querySelectorAll(".lang-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const lang = chip.dataset.lang;
        if (lang === "auto") {
          // "auto" is exclusive: clicking it activates only itself
          container.querySelectorAll(".lang-chip").forEach((c) => toggleChipActive(c, false));
          toggleChipActive(chip, true);
        } else {
          // Deactivate "auto" chip if a specific language is chosen
          const autoChip = container.querySelector(".lang-chip[data-lang='auto']");
          if (autoChip) toggleChipActive(autoChip, false);
          // Toggle this chip (but ensure at least one stays active)
          const wasActive = chip.classList.contains("active");
          const activeBefore = container.querySelectorAll(".lang-chip.active").length;
          if (wasActive && activeBefore <= 1) return; // prevent empty selection
          toggleChipActive(chip, !wasActive);
        }
        updateListenLangHint();
      });
    });
  }

  // -------------------------------------------------------------------------
  // Slider Displays & Handlers
  // -------------------------------------------------------------------------
  function setupSliders() {
    const rateSlider = document.getElementById("vs-rate-slider");
    const pitchSlider = document.getElementById("vs-pitch-slider");
    const volSlider = document.getElementById("vs-vol-slider");
    const chimeVolSlider = document.getElementById("vs-chime-vol");
    const micSensSlider = document.getElementById("vs-mic-sensitivity-slider");

    if (rateSlider) {
      rateSlider.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        const pct = Math.round((val - 1.0) * 100);
        const sign = pct >= 0 ? "+" : "";
        const rateValEl = document.getElementById("vs-rate-val");
        if (rateValEl) rateValEl.innerText = `${sign}${pct}% (${val.toFixed(2)}x)`;
      });
    }

    if (pitchSlider) {
      pitchSlider.addEventListener("input", (e) => {
        const val = parseInt(e.target.value, 10);
        const pitchValEl = document.getElementById("vs-pitch-val");
        if (pitchValEl) pitchValEl.innerText = `${val >= 0 ? "+" : ""}${val} Hz`;
      });
    }

    if (volSlider) {
      volSlider.addEventListener("input", (e) => {
        const volValEl = document.getElementById("vs-vol-val");
        if (volValEl) volValEl.innerText = `${e.target.value}%`;
      });
    }

    const rateTaSlider = document.getElementById("vs-rate-ta-slider");
    const volTaSlider = document.getElementById("vs-vol-ta-slider");
    if (rateTaSlider) {
      rateTaSlider.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        const rateTaValEl = document.getElementById("vs-rate-ta-val");
        if (rateTaValEl) rateTaValEl.innerText = `${val.toFixed(2)}x`;
      });
    }
    if (volTaSlider) {
      volTaSlider.addEventListener("input", (e) => {
        const volTaValEl = document.getElementById("vs-vol-ta-val");
        if (volTaValEl) volTaValEl.innerText = `${e.target.value}%`;
      });
    }

    if (chimeVolSlider) {
      chimeVolSlider.addEventListener("input", (e) => {
        const chimeValEl = document.getElementById("vs-chime-vol-val");
        if (chimeValEl) chimeValEl.innerText = `${e.target.value}%`;
      });
    }

    if (micSensSlider) {
      micSensSlider.addEventListener("input", (e) => {
        const sensValEl = document.getElementById("vs-mic-sens-val");
        if (sensValEl) sensValEl.innerText = e.target.value;
      });
    }

    const wwSensSlider = document.getElementById("vs-ww-sensitivity-slider");
    if (wwSensSlider) {
      wwSensSlider.addEventListener("input", (e) => {
        const wwSensValEl = document.getElementById("vs-ww-sens-val");
        if (wwSensValEl) wwSensValEl.innerText = e.target.value;
      });
    }
  }

  // -------------------------------------------------------------------------
  // Speed Presets (Relaxed, Normal, Quick)
  // -------------------------------------------------------------------------
  function setupSpeedPresets() {
    document.querySelectorAll(".speed-preset-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".speed-preset-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const preset = btn.dataset.preset;
        const rateSlider = document.getElementById("vs-rate-slider");
        if (!rateSlider) return;

        if (preset === "Relaxed") rateSlider.value = 0.85;
        else if (preset === "Quick") rateSlider.value = 1.15;
        else rateSlider.value = 1.0;

        rateSlider.dispatchEvent(new Event("input"));
        window.NallaApp.showToast(`Speed set to ${preset}`, "info");
      });
    });
  }

  // -------------------------------------------------------------------------
  // Expressive Tags Toggle Live Application
  // -------------------------------------------------------------------------
  function setupExpressiveTagsToggle() {
    const toggle = document.getElementById("vs-expressive-tags-toggle");
    if (!toggle) return;

    toggle.addEventListener("change", async () => {
      const isEnabled = toggle.checked;
      try {
        await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expressive_tags: isEnabled }),
        });
        if (currentProfiles[activeProfileName]) {
          currentProfiles[activeProfileName].expressive_tags = isEnabled;
          saveCurrentProfiles(activeProfileName);
        }
        window.NallaApp.showToast(
          `Expressive audio tags ${isEnabled ? "enabled" : "disabled"}.`,
          "info"
        );
      } catch (err) {
        console.warn("Failed to update expressive tags:", err);
      }
    });
  }

  // -------------------------------------------------------------------------
  // Dynamic Voice List Discovery
  // -------------------------------------------------------------------------
  async function loadVoiceList() {
    const voiceSelect = document.getElementById("vs-voice-select");
    const voiceTaSelect = document.getElementById("vs-voice-ta-select");
    if (!voiceSelect) return;

    try {
      const res = await fetch("/api/voice/voices");
      if (!res.ok) return;
      const data = await res.json();
      const grouped = data.voices || {};

      voiceSelect.innerHTML = "";
      if (voiceTaSelect) voiceTaSelect.innerHTML = "";

      for (const [groupName, voices] of Object.entries(grouped)) {
        if (!voices || voices.length === 0) continue;

        const optGroup = document.createElement("optgroup");
        optGroup.label = groupName;

        const optGroupTa = document.createElement("optgroup");
        optGroupTa.label = groupName;

        voices.forEach((v) => {
          const opt = document.createElement("option");
          opt.value = v.id;
          opt.innerText = `${v.name} - ${v.friendly_name || v.id}`;
          opt.dataset.engine = v.engine || "edge-tts";
          optGroup.appendChild(opt);

          if (groupName === "Tamil" || (v.locale && v.locale.startsWith("ta"))) {
            const optTa = opt.cloneNode(true);
            optGroupTa.appendChild(optTa);
          }
        });

        voiceSelect.appendChild(optGroup);
        if (voiceTaSelect && optGroupTa.children.length > 0) {
          voiceTaSelect.appendChild(optGroupTa);
        }
      }

      voiceSelect.addEventListener("change", () => {
        const selected = voiceSelect.options[voiceSelect.selectedIndex];
        const engine = selected ? selected.dataset.engine || "gemini" : "gemini";
        const badge = document.getElementById("vs-voice-engine-badge");
        if (badge) badge.innerText = engine;

        const engSelect = document.getElementById("vs-engine-select");
        if (engSelect && engine) engSelect.value = engine;
      });
    } catch (err) {
      console.warn("Could not load dynamic voices:", err);
    }
  }

  // -------------------------------------------------------------------------
  // Audio Devices List
  // -------------------------------------------------------------------------
  async function loadDevices() {
    const devSelect = document.getElementById("vs-mic-device-select");
    if (!devSelect) return;

    try {
      const res = await fetch("/api/audio/devices");
      if (!res.ok) return;
      const data = await res.json();
      const inputs = data.input_devices || [];

      devSelect.innerHTML = '<option value="">Default System Microphone</option>';
      inputs.forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d.index;
        opt.innerText = `[#${d.index}] ${d.name} (${d.channels}ch, ${Math.round(d.sample_rate / 1000)}kHz)`;
        devSelect.appendChild(opt);
      });
    } catch (e) {
      // Ignored
    }
  }

  // -------------------------------------------------------------------------
  // Voice Status & Gemini Gallery
  // -------------------------------------------------------------------------
  async function loadVoiceStatus() {
    try {
      const res = await fetch("/api/voice/status");
      if (!res.ok) return;
      const data = await res.json();

      // Status indicator
      const statusInd = document.getElementById("vs-status-indicator");
      if (statusInd) {
        if (data.status === "online") {
          statusInd.className = "badge badge-accent";
          statusInd.innerText = "● Online (Gemini TTS)";
        } else {
          statusInd.className = "badge";
          statusInd.style.background = "#ffb400";
          statusInd.style.color = "#000";
          statusInd.innerText = "● Fallback Active";
        }
      }

      // Quota display
      if (data.quota) {
        const countEl = document.getElementById("vs-quota-count");
        const capEl = document.getElementById("vs-quota-cap");
        const capInput = document.getElementById("vs-quota-cap-input");
        if (countEl) countEl.innerText = data.quota.count;
        if (capEl) capEl.innerText = data.quota.cap === 0 ? "Unlimited" : data.quota.cap;
        if (capInput) capInput.value = data.quota.cap;
      }

      // Prebuilt voices gallery
      if (data.prebuilt_voices && data.prebuilt_voices.length > 0) {
        prebuiltVoicesList = data.prebuilt_voices;
        renderPrebuiltGallery(prebuiltVoicesList);
      }
    } catch (err) {
      console.warn("Could not load voice status:", err);
    }
  }

  function getFavoriteVoices() {
    try {
      const saved = localStorage.getItem("nalla_fav_voices");
      return saved ? JSON.parse(saved) : ["achird", "puck", "sulafat"];
    } catch {
      return ["achird", "puck", "sulafat"];
    }
  }

  function setFavoriteVoices(favs) {
    try {
      localStorage.setItem("nalla_fav_voices", JSON.stringify(favs));
    } catch {
      // Ignore
    }
  }

  function renderPrebuiltGallery(voices) {
    const gallery = document.getElementById("vs-prebuilt-gallery");
    if (!gallery) return;

    const favs = new Set(getFavoriteVoices());
    gallery.innerHTML = "";

    voices.forEach((v) => {
      const card = document.createElement("div");
      card.className = "voice-card";
      card.dataset.voiceId = v.id;
      card.style.background = "rgba(255, 255, 255, 0.03)";
      card.style.border = v.recommended ? "1px solid var(--accent)" : "1px solid var(--border-subtle)";
      card.style.borderRadius = "6px";
      card.style.padding = "10px 12px";
      card.style.display = "flex";
      card.style.flexDirection = "column";
      card.style.justifyContent = "space-between";
      card.style.position = "relative";

      const isFav = favs.has(v.id);

      card.innerHTML = `
        <div>
          <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px;">
            <div style="display:flex; align-items:center; gap:6px;">
              <span style="font-weight:600; font-size:0.9rem; color:var(--text);">${v.name}</span>
              ${v.recommended ? '<span style="font-size:0.65rem; background:rgba(0,255,200,0.15); color:var(--accent); padding:1px 5px; border-radius:3px;">Top Pick</span>' : ''}
            </div>
            <button class="fav-star-btn" data-voice="${v.id}" style="background:none; border:none; cursor:pointer; font-size:1.1rem; padding:0; line-height:1; color:${isFav ? 'var(--amber, #ffb400)' : 'var(--text-dim)'};" title="Star as Favorite">
              ${isFav ? '★' : '☆'}
            </button>
          </div>
          <div style="font-size:0.75rem; color:var(--accent); margin-bottom:6px; font-family:var(--font-mono);">
            ${v.character || 'Conversational voice'}
          </div>
          <div style="display:flex; gap:6px; font-size:0.7rem; color:var(--text-dim); margin-bottom:10px;">
            <span style="text-transform:capitalize;">${v.gender}</span> • <span>24 kHz</span>
          </div>
        </div>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-sm btn-outline audition-voice-btn" data-voice="${v.id}" style="flex:1; font-size:0.75rem; padding:4px 6px;">
            ▶️ Preview
          </button>
          <button class="btn btn-sm btn-outline select-voice-btn" data-voice="${v.id}" style="font-size:0.75rem; padding:4px 8px;" title="Set as Active Primary Voice">
            Select
          </button>
        </div>
      `;

      // Audition button with playing animation
      const audBtn = card.querySelector(".audition-voice-btn");
      if (audBtn) {
        audBtn.addEventListener("click", () => {
          runVoicePreview({ voice: v.id, engine: "gemini", button: audBtn, card: card });
        });
      }

      // Select button
      const selBtn = card.querySelector(".select-voice-btn");
      if (selBtn) {
        selBtn.addEventListener("click", () => {
          const vSelect = document.getElementById("vs-voice-select");
          const engSelect = document.getElementById("vs-engine-select");
          if (vSelect) {
            vSelect.value = v.id;
            vSelect.dispatchEvent(new Event("change"));
          }
          if (engSelect) engSelect.value = "gemini";
          window.NallaApp.showToast(`Selected ${v.name} as Primary Voice.`, "info");
        });
      }

      // Favorite toggle
      const starBtn = card.querySelector(".fav-star-btn");
      if (starBtn) {
        starBtn.addEventListener("click", () => {
          const curFavs = new Set(getFavoriteVoices());
          if (curFavs.has(v.id)) {
            curFavs.delete(v.id);
            starBtn.innerText = "☆";
            starBtn.style.color = "var(--text-dim)";
          } else {
            curFavs.add(v.id);
            starBtn.innerText = "★";
            starBtn.style.color = "var(--amber, #ffb400)";
          }
          setFavoriteVoices(Array.from(curFavs));
        });
      }

      gallery.appendChild(card);
    });
  }

  // -------------------------------------------------------------------------
  // Extended Voice Library Browser (Filters: Language, Gender, Pitch, Context, Search)
  // -------------------------------------------------------------------------
  function setupExtendedLibrary() {
    const searchBtn = document.getElementById("vs-ext-search-btn");
    const searchInput = document.getElementById("vs-ext-search");
    const langSelect = document.getElementById("vs-ext-lang");
    const genderSelect = document.getElementById("vs-ext-gender");
    const pitchSelect = document.getElementById("vs-ext-pitch");
    const contextSelect = document.getElementById("vs-ext-context");
    const resultsContainer = document.getElementById("vs-ext-results");

    if (!searchBtn || !resultsContainer) return;

    async function executeExtendedSearch() {
      searchBtn.disabled = true;
      searchBtn.innerText = "Filtering...";
      resultsContainer.innerHTML = '<div style="font-size:0.8rem; color:var(--text-dim); text-align:center; padding:16px;">Querying Voice Catalog...</div>';

      try {
        const q = searchInput ? encodeURIComponent(searchInput.value.trim()) : "";
        const lang = langSelect ? encodeURIComponent(langSelect.value) : "ta-IN%2Cen-IN";
        const gender = genderSelect ? encodeURIComponent(genderSelect.value) : "";
        const pitch = pitchSelect ? encodeURIComponent(pitchSelect.value) : "";
        const context = contextSelect ? encodeURIComponent(contextSelect.value) : "";

        const url = `/api/voice/extended-library?query=${q}&language_code=${lang}&gender=${gender}&pitch=${pitch}&contexts=${context}&search=${q}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const voices = data.voices || [];

        if (voices.length === 0) {
          resultsContainer.innerHTML = '<div style="font-size:0.8rem; color:var(--text-dim); text-align:center; padding:16px;">No voices matched these filters.</div>';
          return;
        }

        resultsContainer.innerHTML = "";
        voices.forEach((v) => {
          const row = document.createElement("div");
          row.className = "voice-card";
          row.style.background = "rgba(255, 255, 255, 0.02)";
          row.style.border = "1px solid var(--border-subtle)";
          row.style.borderRadius = "4px";
          row.style.padding = "8px 10px";
          row.style.display = "flex";
          row.style.justifyContent = "space-between";
          row.style.alignItems = "center";
          row.style.gap = "8px";

          const pitchBadge = v.pitch ? `<span style="font-size:0.68rem; background:rgba(255,255,255,0.06); padding:1px 4px; border-radius:3px;">${v.pitch}</span>` : "";
          const ctxBadge = (v.context || v.persona) ? `<span style="font-size:0.68rem; background:rgba(0,255,200,0.08); color:var(--accent); padding:1px 4px; border-radius:3px;">${v.context || v.persona}</span>` : "";

          row.innerHTML = `
            <div style="flex:1; min-width:0;">
              <div style="display:flex; align-items:center; gap:6px; margin-bottom:2px;">
                <span style="font-size:0.82rem; font-weight:600; color:var(--text);">${v.name || v.id}</span>
                ${pitchBadge}
                ${ctxBadge}
              </div>
              <div style="font-size:0.72rem; color:var(--text-dim);">
                ${v.language_code || 'ta-IN'} • ${v.gender || 'neutral'} • ${v.description || v.accent || 'Natural'}
              </div>
            </div>
            <div style="display:flex; gap:4px;">
              <button class="btn btn-sm btn-outline ext-audition-btn" data-voice="${v.id}" style="font-size:0.72rem; padding:3px 7px;" title="Preview Audio">
                ▶️ Preview
              </button>
              <button class="btn btn-sm btn-outline ext-use-btn" data-voice="${v.id}" style="font-size:0.72rem; padding:3px 7px;" title="Set as Custom Voice ID">
                Use
              </button>
            </div>
          `;

          const aBtn = row.querySelector(".ext-audition-btn");
          if (aBtn) {
            aBtn.addEventListener("click", () => {
              runVoicePreview({ voice: v.id, engine: "gemini", button: aBtn, card: row });
            });
          }

          const uBtn = row.querySelector(".ext-use-btn");
          if (uBtn) {
            uBtn.addEventListener("click", () => {
              const custInput = document.getElementById("vs-custom-voice-input");
              if (custInput) custInput.value = v.id;
              window.NallaApp.showToast(`Selected custom voice ID: ${v.id}`, "info");
            });
          }

          resultsContainer.appendChild(row);
        });
      } catch (err) {
        resultsContainer.innerHTML = `<div style="font-size:0.8rem; color:var(--red); text-align:center; padding:16px;">Failed to load extended voices: ${err.message}</div>`;
      } finally {
        searchBtn.disabled = false;
        searchBtn.innerText = "Filter";
      }
    }

    searchBtn.addEventListener("click", executeExtendedSearch);
    if (langSelect) langSelect.addEventListener("change", executeExtendedSearch);
    if (genderSelect) genderSelect.addEventListener("change", executeExtendedSearch);
    if (pitchSelect) pitchSelect.addEventListener("change", executeExtendedSearch);
    if (contextSelect) contextSelect.addEventListener("change", executeExtendedSearch);

    // Initial search on load
    setTimeout(executeExtendedSearch, 300);
  }

  // -------------------------------------------------------------------------
  // Pronunciation Map Live CRUD
  // -------------------------------------------------------------------------
  function setupPronunciationChips() {
    document.querySelectorAll(".pron-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const tanglish = chip.dataset.tanglish;
        const tamil = chip.dataset.tamil;
        if (!tanglish || !tamil) return;

        currentPronunciationMap[tanglish.toLowerCase()] = tamil;
        renderPronunciationList();
        window.NallaApp.showToast(`Added '${tanglish}' ➔ '${tamil}' to map.`, "info");
      });
    });
  }

  async function setupPronunciationMap() {
    const listEl = document.getElementById("vs-pron-list");
    const addBtn = document.getElementById("vs-add-pron-btn");
    const saveBtn = document.getElementById("vs-save-pron-btn");
    const tanglishInput = document.getElementById("vs-pron-tanglish");
    const tamilInput = document.getElementById("vs-pron-tamil");

    try {
      const res = await fetch("/api/voice/pronunciation");
      if (res.ok) {
        const data = await res.json();
        currentPronunciationMap = data.map || data.mapping || {};
        renderPronunciationList();
      }
    } catch {
      // Ignored
    }

    if (addBtn) {
      addBtn.addEventListener("click", () => {
        const tanglish = tanglishInput ? tanglishInput.value.trim().toLowerCase() : "";
        const tamil = tamilInput ? tamilInput.value.trim() : "";
        if (!tanglish || !tamil) {
          alert("Please enter both Tanglish spelling and Tamil script replacement.");
          return;
        }

        currentPronunciationMap[tanglish] = tamil;
        renderPronunciationList();
        if (tanglishInput) tanglishInput.value = "";
        if (tamilInput) tamilInput.value = "";
      });
    }

    if (saveBtn) {
      saveBtn.addEventListener("click", async () => {
        saveBtn.disabled = true;
        saveBtn.innerText = "Saving...";
        try {
          const res = await fetch("/api/voice/pronunciation", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ map: currentPronunciationMap }),
          });
          if (res.ok) {
            window.NallaApp.showToast("Tamil Pronunciation Map saved live.", "success");
          } else {
            throw new Error(`HTTP ${res.status}`);
          }
        } catch (err) {
          window.NallaApp.showToast(`Failed to save map: ${err.message}`, "alert");
        } finally {
          saveBtn.disabled = false;
          saveBtn.innerText = "💾 Save Map";
        }
      });
    }
  }

  function renderPronunciationList() {
    const listEl = document.getElementById("vs-pron-list");
    if (!listEl) return;

    listEl.innerHTML = "";
    const entries = Object.entries(currentPronunciationMap);
    if (entries.length === 0) {
      listEl.innerHTML = '<div style="font-size:0.8rem; color:var(--text-dim); text-align:center; padding:12px;">Map is empty. Add Tanglish -> Tamil pairs above.</div>';
      return;
    }

    entries.forEach(([tanglish, tamil]) => {
      const item = document.createElement("div");
      item.style.background = "rgba(255, 255, 255, 0.02)";
      item.style.border = "1px solid var(--border-subtle)";
      item.style.borderRadius = "4px";
      item.style.padding = "5px 10px";
      item.style.display = "flex";
      item.style.justifyContent = "space-between";
      item.style.alignItems = "center";
      item.style.fontSize = "0.8rem";

      item.innerHTML = `
        <div>
          <span style="font-family:var(--font-mono); color:var(--accent); font-weight:600;">${tanglish}</span>
          <span style="color:var(--text-dim); margin:0 6px;">➔</span>
          <span style="color:var(--text); font-weight:600;">${tamil}</span>
        </div>
        <button class="btn btn-sm btn-outline btn-red del-pron-btn" data-key="${tanglish}" style="padding:1px 6px; font-size:0.7rem;" title="Delete">
          🗑️
        </button>
      `;

      const delBtn = item.querySelector(".del-pron-btn");
      if (delBtn) {
        delBtn.addEventListener("click", () => {
          delete currentPronunciationMap[tanglish];
          renderPronunciationList();
        });
      }

      listEl.appendChild(item);
    });
  }

  // -------------------------------------------------------------------------
  // Quota Controls & Engine Selection
  // -------------------------------------------------------------------------
  function setupQuotaControls() {
    const setCapBtn = document.getElementById("vs-set-cap-btn");
    const capInput = document.getElementById("vs-quota-cap-input");
    const engSelect = document.getElementById("vs-engine-select");
    const modelSelect = document.getElementById("vs-model-select");

    if (setCapBtn && capInput) {
      setCapBtn.addEventListener("click", async () => {
        const val = parseInt(capInput.value, 10);
        if (isNaN(val) || val < 0) return;

        try {
          const res = await fetch("/api/voice/quota/cap", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cap: val }),
          });
          if (res.ok) {
            const data = await res.json();
            const capEl = document.getElementById("vs-quota-cap");
            if (capEl) capEl.innerText = data.cap === 0 ? "Unlimited" : data.cap;
            window.NallaApp.showToast(`Daily quota cap set to ${data.cap}`, "info");
          }
        } catch (err) {
          window.NallaApp.showToast("Failed to set quota cap: " + err.message, "alert");
        }
      });
    }

    if (engSelect) {
      engSelect.addEventListener("change", (e) => {
        const engine = e.target.value;
        const badge = document.getElementById("vs-voice-engine-badge");
        if (badge) badge.innerText = engine;
        if (currentProfiles[activeProfileName]) {
          currentProfiles[activeProfileName].engine = engine;
          saveCurrentProfiles(activeProfileName);
        }
      });
    }

    if (modelSelect) {
      modelSelect.addEventListener("change", (e) => {
        const model = e.target.value;
        if (currentProfiles[activeProfileName]) {
          currentProfiles[activeProfileName].model = model;
          saveCurrentProfiles(activeProfileName);
        }
      });
    }
  }

  // -------------------------------------------------------------------------
  // Profiles Management & Loading Settings
  // -------------------------------------------------------------------------
  async function loadVoiceSettings() {
    try {
      const res = await fetch("/api/voice/profiles");
      if (!res.ok) return;
      const data = await res.json();

      currentProfiles = data.profiles || {};
      activeProfileName = data.active || "Nalla Default";

      // Render profile options
      renderProfileDropdown();

      // Apply active profile
      if (currentProfiles[activeProfileName]) {
        applyProfileToUI(currentProfiles[activeProfileName], activeProfileName);
      }

      // Audio Feel
      if (data.audio_feel) {
        const feel = data.audio_feel;
        const listenChime = document.getElementById("vs-chime-listen");
        const doneChime = document.getElementById("vs-chime-done");
        const chimeVol = document.getElementById("vs-chime-vol");

        if (listenChime) listenChime.checked = feel.listening_chime !== false;
        if (doneChime) doneChime.checked = feel.done_chime !== false;
        if (chimeVol) {
          chimeVol.value = feel.chime_volume || 80;
          chimeVol.dispatchEvent(new Event("input"));
        }
      }

      // STT options
      if (data.stt) {
        const stt = data.stt;
        const vsStt = document.getElementById("vs-stt-select");
        const vsDev = document.getElementById("vs-mic-device-select");
        const vsSens = document.getElementById("vs-mic-sensitivity-slider");

        if (vsStt && stt.whisper_model) vsStt.value = stt.whisper_model;
        if (vsDev && stt.input_device !== null && stt.input_device !== undefined) {
          vsDev.value = String(stt.input_device);
        }
        if (vsSens && stt.sensitivity) {
          vsSens.value = stt.sensitivity;
          vsSens.dispatchEvent(new Event("input"));
        }

        // Restore language chips from saved listen_languages list
        const savedLangs = Array.isArray(stt.listen_languages) ? stt.listen_languages : ["en", "ta"];
        setListenLanguageChips(savedLangs);
      }

      // Wake Word options
      try {
        const wwRes = await fetch("/api/voice/wakeword");
        if (wwRes.ok) {
          const wwData = await wwRes.json();
          const wwEng = document.getElementById("vs-ww-engine-select");
          const wwKey = document.getElementById("vs-ww-keyword-input");
          const wwSens = document.getElementById("vs-ww-sensitivity-slider");
          const wwSensVal = document.getElementById("vs-ww-sens-val");
          const wwFollow = document.getElementById("vs-follow-up-toggle");

          if (wwEng && wwData.engine) wwEng.value = wwData.engine;
          if (wwKey && wwData.keyword) wwKey.value = wwData.keyword;
          if (wwSens && wwData.sensitivity !== undefined) {
            wwSens.value = wwData.sensitivity;
            if (wwSensVal) wwSensVal.innerText = wwData.sensitivity;
          }
          if (wwFollow && wwData.follow_up_enabled !== undefined) {
            wwFollow.checked = !!wwData.follow_up_enabled;
          }
        }
      } catch (wwErr) {
        console.warn("Could not load wakeword settings:", wwErr);
      }

      // Talk Mode options (Prompt A)
      try {
        const tmRes = await fetch("/api/voice/talk-mode");
        if (tmRes.ok) {
          const tmData = (await tmRes.json()).talk_mode || {};
          const tmSel = document.getElementById("vs-talk-mode-select");
          const kmwTog = document.getElementById("vs-keep-mic-warm-toggle");
          const ghTog = document.getElementById("vs-global-hotkey-toggle");
          const ghKey = document.getElementById("vs-global-hotkey-key-input");
          const ssttTog = document.getElementById("vs-streaming-stt-toggle");
          const scTog = document.getElementById("vs-soft-click-toggle");

          if (tmSel && tmData.talk_mode) tmSel.value = tmData.talk_mode;
          if (kmwTog && tmData.keep_mic_warm !== undefined) kmwTog.checked = !!tmData.keep_mic_warm;
          if (ghTog && tmData.global_hotkey_enabled !== undefined) ghTog.checked = !!tmData.global_hotkey_enabled;
          if (ghKey && tmData.global_hotkey_key) ghKey.value = tmData.global_hotkey_key;
          if (ssttTog && tmData.streaming_stt !== undefined) ssttTog.checked = !!tmData.streaming_stt;
          if (scTog && tmData.soft_click_sound !== undefined) scTog.checked = !!tmData.soft_click_sound;
        }
      } catch (tmErr) {
        console.warn("Could not load talk-mode settings:", tmErr);
      }
    } catch (err) {
      console.warn("Could not load voice profiles:", err);
    }
  }

  function renderProfileDropdown() {
    const pSelect = document.getElementById("vs-profile-select");
    if (!pSelect) return;

    pSelect.innerHTML = "";
    Object.keys(currentProfiles).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.innerText = name;
      if (name === activeProfileName) opt.selected = true;
      pSelect.appendChild(opt);
    });

    const badge = document.getElementById("vs-active-profile-badge");
    if (badge) badge.innerText = activeProfileName;
  }

  function applyProfileToUI(p, name) {
    activeProfileName = name;
    const badge = document.getElementById("vs-active-profile-badge");
    if (badge) badge.innerText = name;

    const slotEn = p.voice_slot_en || {};
    const slotTa = p.voice_slot_ta || {};

    const engSelect = document.getElementById("vs-engine-select");
    const voiceSelect = document.getElementById("vs-voice-select");
    const rateSlider = document.getElementById("vs-rate-slider");
    const volSlider = document.getElementById("vs-vol-slider");

    const engTaSelect = document.getElementById("vs-engine-ta-select");
    const voiceTaSelect = document.getElementById("vs-voice-ta-select");
    const rateTaSlider = document.getElementById("vs-rate-ta-slider");
    const volTaSlider = document.getElementById("vs-vol-ta-slider");
    const autoSwitchToggle = document.getElementById("vs-auto-switch-toggle");

    const modelSelect = document.getElementById("vs-model-select");
    const styleInput = document.getElementById("vs-style-input");
    const tagsToggle = document.getElementById("vs-expressive-tags-toggle");

    // English slot
    const enEngine = slotEn.engine || p.engine || "gemini";
    const enVoice = slotEn.voice || p.voice || "achird";
    const enRate = slotEn.rate !== undefined ? slotEn.rate : (p.rate !== undefined ? p.rate : 1.0);
    const enVol = slotEn.volume !== undefined ? slotEn.volume : (p.volume !== undefined ? p.volume : 100);

    if (engSelect) engSelect.value = enEngine;
    if (voiceSelect) {
      voiceSelect.value = enVoice;
      voiceSelect.dispatchEvent(new Event("change"));
    }
    if (rateSlider) {
      let rVal = 1.0;
      if (typeof enRate === "string") {
        const m = enRate.match(/([+-]?\d+)/);
        if (m) rVal = 1.0 + parseInt(m[1], 10) / 100.0;
      } else {
        rVal = parseFloat(enRate);
      }
      rateSlider.value = rVal;
      rateSlider.dispatchEvent(new Event("input"));
    }
    if (volSlider) {
      let vVal = 100;
      if (typeof enVol === "string") {
        const m = enVol.match(/([+-]?\d+)/);
        if (m) vVal = 100 + parseInt(m[1], 10);
      } else {
        vVal = parseInt(enVol, 10);
      }
      volSlider.value = vVal;
      volSlider.dispatchEvent(new Event("input"));
    }

    // Tamil slot
    const taEngine = slotTa.engine || "edge-tts";
    const taVoice = slotTa.voice || p.voice_ta || "ta-IN-PallaviNeural";
    const taRate = slotTa.rate !== undefined ? slotTa.rate : 1.0;
    const taVol = slotTa.volume !== undefined ? slotTa.volume : 100;

    if (engTaSelect) engTaSelect.value = taEngine;
    if (voiceTaSelect) voiceTaSelect.value = taVoice;
    if (rateTaSlider) {
      let rVal = 1.0;
      if (typeof taRate === "string") {
        const m = taRate.match(/([+-]?\d+)/);
        if (m) rVal = 1.0 + parseInt(m[1], 10) / 100.0;
      } else {
        rVal = parseFloat(taRate);
      }
      rateTaSlider.value = rVal;
      rateTaSlider.dispatchEvent(new Event("input"));
    }
    if (volTaSlider) {
      let vVal = 100;
      if (typeof taVol === "string") {
        const m = taVol.match(/([+-]?\d+)/);
        if (m) vVal = 100 + parseInt(m[1], 10);
      } else {
        vVal = parseInt(taVol, 10);
      }
      volTaSlider.value = vVal;
      volTaSlider.dispatchEvent(new Event("input"));
    }

    if (autoSwitchToggle) {
      autoSwitchToggle.checked = p.auto_switch_language !== undefined ? !!p.auto_switch_language : true;
    }

    if (modelSelect && p.model) modelSelect.value = p.model;
    if (styleInput && p.style) styleInput.value = p.style;
    if (tagsToggle && p.expressive_tags !== undefined) tagsToggle.checked = !!p.expressive_tags;
  }

  function setupProfileControls() {
    const pSelect = document.getElementById("vs-profile-select");
    const saveBtn = document.getElementById("vs-save-profile-btn");
    const delBtn = document.getElementById("vs-delete-profile-btn");

    if (pSelect) {
      pSelect.addEventListener("change", (e) => {
        const selected = e.target.value;
        if (currentProfiles[selected]) {
          applyProfileToUI(currentProfiles[selected], selected);
          saveCurrentProfiles(selected);
          window.NallaApp.showToast(`Switched profile to: ${selected}`, "info");
        }
      });
    }

    if (saveBtn) {
      saveBtn.addEventListener("click", () => {
        const name = prompt("Enter profile name (e.g. 'Coimbatore Buddy', 'Midnight Focus'):", activeProfileName);
        if (!name || !name.trim()) return;
        const cleanName = name.trim();

        const count = Object.keys(currentProfiles).length;
        if (!currentProfiles[cleanName] && count >= 5) {
          alert("Maximum 5 profiles allowed. Please delete or overwrite an existing profile.");
          return;
        }

        const voiceSelect = document.getElementById("vs-voice-select");
        const voiceTaSelect = document.getElementById("vs-voice-ta-select");
        const engSelect = document.getElementById("vs-engine-select");
        const engTaSelect = document.getElementById("vs-engine-ta-select");
        const modelSelect = document.getElementById("vs-model-select");
        const styleInput = document.getElementById("vs-style-input");
        const tagsToggle = document.getElementById("vs-expressive-tags-toggle");
        const rateSlider = document.getElementById("vs-rate-slider");
        const volSlider = document.getElementById("vs-vol-slider");
        const rateTaSlider = document.getElementById("vs-rate-ta-slider");
        const volTaSlider = document.getElementById("vs-vol-ta-slider");
        const autoSwitchToggle = document.getElementById("vs-auto-switch-toggle");

        const enEngine = engSelect ? engSelect.value : "gemini";
        const enVoice = voiceSelect ? voiceSelect.value : "achird";
        const enRate = rateSlider ? parseFloat(rateSlider.value) : 1.0;
        const enVol = volSlider ? parseInt(volSlider.value, 10) : 100;

        const taEngine = engTaSelect ? engTaSelect.value : "edge-tts";
        const taVoice = voiceTaSelect ? voiceTaSelect.value : "ta-IN-PallaviNeural";
        const taRate = rateTaSlider ? parseFloat(rateTaSlider.value) : 1.0;
        const taVol = volTaSlider ? parseInt(volTaSlider.value, 10) : 100;

        currentProfiles[cleanName] = {
          engine: enEngine,
          voice: enVoice,
          voice_ta: taVoice,
          model: modelSelect ? modelSelect.value : "gemini-3.8-flash-lite-tts",
          style: styleInput ? styleInput.value : "casual, friendly, dry wit",
          expressive_tags: tagsToggle ? tagsToggle.checked : false,
          rate: enRate,
          volume: enVol,
          auto_switch_language: autoSwitchToggle ? autoSwitchToggle.checked : true,
          voice_slot_en: {
            engine: enEngine,
            voice: enVoice,
            rate: enRate,
            volume: enVol,
          },
          voice_slot_ta: {
            engine: taEngine,
            voice: taVoice,
            rate: taRate,
            volume: taVol,
          },
        };

        activeProfileName = cleanName;
        renderProfileDropdown();
        saveCurrentProfiles(cleanName);
        window.NallaApp.showToast(`Saved voice profile '${cleanName}'.`, "success");
      });
    }

    if (delBtn) {
      delBtn.addEventListener("click", () => {
        if (activeProfileName === "Nalla Default") {
          alert("Cannot delete the default profile.");
          return;
        }
        if (confirm(`Delete profile '${activeProfileName}'?`)) {
          delete currentProfiles[activeProfileName];
          activeProfileName = "Nalla Default";
          renderProfileDropdown();
          if (currentProfiles[activeProfileName]) {
            applyProfileToUI(currentProfiles[activeProfileName], activeProfileName);
          }
          saveCurrentProfiles(activeProfileName);
          window.NallaApp.showToast("Profile deleted.", "info");
        }
      });
    }
  }

  async function saveCurrentProfiles(activeName) {
    try {
      await fetch("/api/voice/profiles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          active: activeName || activeProfileName,
          profiles: currentProfiles,
        }),
      });
    } catch (e) {
      console.error("Failed to save profiles:", e);
    }
  }

  // -------------------------------------------------------------------------
  // Preview & Button Actions with Playing Animation
  // -------------------------------------------------------------------------
  function setupButtons() {
    const previewBtn = document.getElementById("vs-preview-btn");
    const previewTaBtn = document.getElementById("vs-preview-ta-btn");
    const testSpeakerBtn = document.getElementById("vs-test-speaker-btn");
    const testMicBtn = document.getElementById("vs-test-mic-btn");
    const saveAllBtn = document.getElementById("vs-save-all-btn");
    const previewCustBtn = document.getElementById("vs-preview-custom-btn");

    if (previewBtn) {
      previewBtn.addEventListener("click", () => {
        const engSelect = document.getElementById("vs-engine-select");
        const voiceSelect = document.getElementById("vs-voice-select");
        const rateSlider = document.getElementById("vs-rate-slider");
        const volSlider = document.getElementById("vs-vol-slider");
        runVoicePreview({
          slot: "en",
          text: "Yes boss, sollunga!",
          engine: engSelect ? engSelect.value : "gemini",
          voice: voiceSelect ? voiceSelect.value : "achird",
          rate: rateSlider ? parseFloat(rateSlider.value) : 1.0,
          volume: volSlider ? parseInt(volSlider.value, 10) : 100,
          button: previewBtn,
        });
      });
    }

    if (previewTaBtn) {
      previewTaBtn.addEventListener("click", () => {
        const engTaSelect = document.getElementById("vs-engine-ta-select");
        const voiceTaSelect = document.getElementById("vs-voice-ta-select");
        const rateTaSlider = document.getElementById("vs-rate-ta-slider");
        const volTaSlider = document.getElementById("vs-vol-ta-slider");
        runVoicePreview({
          slot: "ta",
          text: "வணக்கம், நான் நல்ல சிவம். உங்களுக்கு எப்படி உதவ வேண்டும்?",
          engine: engTaSelect ? engTaSelect.value : "edge-tts",
          voice: voiceTaSelect ? voiceTaSelect.value : "ta-IN-PallaviNeural",
          rate: rateTaSlider ? parseFloat(rateTaSlider.value) : 1.0,
          volume: volTaSlider ? parseInt(volTaSlider.value, 10) : 100,
          button: previewTaBtn,
        });
      });
    }

    if (previewCustBtn) {
      previewCustBtn.addEventListener("click", () => {
        const custInput = document.getElementById("vs-custom-voice-input");
        const voiceId = custInput ? custInput.value.trim() : "";
        if (!voiceId) {
          alert("Please enter a custom voice ID.");
          return;
        }
        runVoicePreview({ voice: voiceId, engine: "gemini", button: previewCustBtn });
      });
    }

    if (testSpeakerBtn) {
      testSpeakerBtn.addEventListener("click", () => window.NallaApp.testSpeaker());
    }

    if (testMicBtn) {
      testMicBtn.addEventListener("click", () => runMicMeterTest());
    }

    if (saveAllBtn) {
      saveAllBtn.addEventListener("click", () => saveAllSettingsLive());
    }
  }

  function stopCurrentAudio() {
    if (currentlyPlayingAudio) {
      try {
        currentlyPlayingAudio.pause();
        currentlyPlayingAudio = null;
      } catch (e) {}
    }
    if (currentlyPlayingCard) {
      currentlyPlayingCard.classList.remove("is-playing");
      currentlyPlayingCard = null;
    }
    if (currentlyPlayingBtn) {
      currentlyPlayingBtn.disabled = false;
      if (currentlyPlayingBtn.dataset.originalHtml) {
        currentlyPlayingBtn.innerHTML = currentlyPlayingBtn.dataset.originalHtml;
      }
      currentlyPlayingBtn = null;
    }
  }

  async function runVoicePreview(opts = {}) {
    const previewBtn = opts.button || document.getElementById("vs-preview-btn");
    const card = opts.card || (previewBtn ? previewBtn.closest(".voice-card") : null);
    const voiceSelect = document.getElementById("vs-voice-select");
    const engSelect = document.getElementById("vs-engine-select");
    const modelSelect = document.getElementById("vs-model-select");
    const styleInput = document.getElementById("vs-style-input");
    const tagsToggle = document.getElementById("vs-expressive-tags-toggle");
    const rateSlider = document.getElementById("vs-rate-slider");
    const pitchSlider = document.getElementById("vs-pitch-slider");
    const volSlider = document.getElementById("vs-vol-slider");

    const selectedOpt = voiceSelect ? voiceSelect.options[voiceSelect.selectedIndex] : null;
    const voice = opts.voice || (voiceSelect ? voiceSelect.value : "achird");
    const engine = opts.engine || (engSelect ? engSelect.value : (selectedOpt ? selectedOpt.dataset.engine || "gemini" : "gemini"));
    const model = modelSelect ? modelSelect.value : "gemini-3.8-flash-lite-tts";
    const style = styleInput ? styleInput.value : "casual, friendly, dry wit";
    const expressive = tagsToggle ? tagsToggle.checked : false;

    // Stop previous audio if playing
    stopCurrentAudio();

    let originalHtml = "";
    if (previewBtn) {
      originalHtml = previewBtn.innerHTML;
      previewBtn.dataset.originalHtml = originalHtml;
      previewBtn.disabled = true;
      previewBtn.innerHTML = "<span>⏳</span> Synthesizing...";
    }

    try {
      const res = await fetch("/api/voice/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slot: opts.slot || "en",
          text: opts.text || (opts.slot === "ta" ? "வணக்கம், நான் நல்ல சிவம். உங்களுக்கு எப்படி உதவ வேண்டும்?" : "Yes boss, sollunga!"),
          engine: engine,
          voice: voice,
          model: model,
          style: style,
          expressive_tags: expressive,
          rate: opts.rate !== undefined ? opts.rate : (rateSlider ? parseFloat(rateSlider.value) : 1.0),
          pitch: opts.pitch !== undefined ? opts.pitch : (pitchSlider ? parseInt(pitchSlider.value, 10) : 0),
          volume: opts.volume !== undefined ? opts.volume : (volSlider ? parseInt(volSlider.value, 10) : 100),
        }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const blob = await res.blob();
      const audioUrl = URL.createObjectURL(blob);
      const audio = new Audio(audioUrl);

      currentlyPlayingAudio = audio;
      currentlyPlayingBtn = previewBtn;
      currentlyPlayingCard = card;

      if (card) card.classList.add("is-playing");
      if (previewBtn) {
        previewBtn.disabled = false;
        previewBtn.innerHTML = '<span class="playing-wave"><span></span><span></span><span></span></span> Playing...';
      }

      audio.play();

      audio.onended = () => {
        stopCurrentAudio();
      };
      audio.onerror = () => {
        stopCurrentAudio();
      };
    } catch (err) {
      stopCurrentAudio();
      window.NallaApp.showToast(`Preview failed: ${err.message}`, "alert");
    }
  }

  async function saveAllSettingsLive() {
    const saveBtn = document.getElementById("vs-save-all-btn");
    if (saveBtn) saveBtn.innerText = "Saving...";

    try {
      // 1. Audio Feel
      const listenChime = document.getElementById("vs-chime-listen");
      const doneChime = document.getElementById("vs-chime-done");
      const chimeVol = document.getElementById("vs-chime-vol");

      await fetch("/api/voice/audio-feel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          listening_chime: listenChime ? listenChime.checked : true,
          done_chime: doneChime ? doneChime.checked : true,
          chime_volume: chimeVol ? parseInt(chimeVol.value, 10) : 80,
        }),
      });

      // 2. STT Settings
      const vsStt = document.getElementById("vs-stt-select");
      const vsDev = document.getElementById("vs-mic-device-select");
      const vsSens = document.getElementById("vs-mic-sensitivity-slider");

      await fetch("/api/voice/stt-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          whisper_model: vsStt ? vsStt.value : "base",
          listen_languages: getListenLanguages(),
          input_device: vsDev && vsDev.value ? parseInt(vsDev.value, 10) : null,
          sensitivity: vsSens ? parseFloat(vsSens.value) : 400.0,
        }),
      });

      // 2b. Wake Word Settings
      const wwEng = document.getElementById("vs-ww-engine-select");
      const wwKey = document.getElementById("vs-ww-keyword-input");
      const wwSens = document.getElementById("vs-ww-sensitivity-slider");
      const wwFollow = document.getElementById("vs-follow-up-toggle");

      await fetch("/api/voice/wakeword", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          engine: wwEng ? wwEng.value : "pushtotalk",
          keyword: wwKey ? wwKey.value : "hey_jarvis_v0.1.onnx",
          sensitivity: wwSens ? parseFloat(wwSens.value) : 0.5,
          follow_up_enabled: wwFollow ? wwFollow.checked : true,
          follow_up_timeout: 30.0,
        }),
      });

      // 2c. Talk Mode Settings (Prompt A)
      const tmSel = document.getElementById("vs-talk-mode-select");
      const kmwTog = document.getElementById("vs-keep-mic-warm-toggle");
      const ghTog = document.getElementById("vs-global-hotkey-toggle");
      const ghKey = document.getElementById("vs-global-hotkey-key-input");
      const ssttTog = document.getElementById("vs-streaming-stt-toggle");
      const scTog = document.getElementById("vs-soft-click-toggle");

      await fetch("/api/voice/talk-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          talk_mode: tmSel ? tmSel.value : "walkie_talkie",
          keep_mic_warm: kmwTog ? kmwTog.checked : false,
          global_hotkey_enabled: ghTog ? ghTog.checked : false,
          global_hotkey_key: ghKey ? ghKey.value.trim() : "F9",
          streaming_stt: ssttTog ? ssttTog.checked : false,
          soft_click_sound: scTog ? scTog.checked : true,
        }),
      });

      // 3. Update current profile live
      const voiceSelect = document.getElementById("vs-voice-select");
      const voiceTaSelect = document.getElementById("vs-voice-ta-select");
      const engSelect = document.getElementById("vs-engine-select");
      const modelSelect = document.getElementById("vs-model-select");
      const styleInput = document.getElementById("vs-style-input");
      const tagsToggle = document.getElementById("vs-expressive-tags-toggle");
      const rateSlider = document.getElementById("vs-rate-slider");
      const pitchSlider = document.getElementById("vs-pitch-slider");
      const volSlider = document.getElementById("vs-vol-slider");

      if (currentProfiles[activeProfileName]) {
        currentProfiles[activeProfileName] = {
          engine: engSelect ? engSelect.value : "gemini",
          model: modelSelect ? modelSelect.value : "gemini-3.8-flash-lite-tts",
          voice: voiceSelect ? voiceSelect.value : "achird",
          voice_ta: voiceTaSelect ? voiceTaSelect.value : "ta-IN-PallaviNeural",
          style: styleInput ? styleInput.value : "casual, friendly, dry wit",
          expressive_tags: tagsToggle ? tagsToggle.checked : false,
          rate: rateSlider ? `${Math.round((parseFloat(rateSlider.value) - 1.0) * 100):+d}%` : "+0%",
          pitch: pitchSlider ? `${parseInt(pitchSlider.value, 10):+d}Hz` : "+0Hz",
          volume: volSlider ? `${parseInt(volSlider.value, 10) - 100:+d}%` : "+0%",
        };
        await saveCurrentProfiles(activeProfileName);
      }

      // Refresh Voice Status
      await loadVoiceStatus();

      window.NallaApp.showToast("Voice Studio settings saved live without restart.", "success");
    } catch (err) {
      window.NallaApp.showToast("Failed to save settings: " + err.message, "alert");
    } finally {
      if (saveBtn) saveBtn.innerText = "💾 Save All Live";
    }
  }

  async function runMicMeterTest() {
    const bar = document.getElementById("vs-mic-meter-fill");
    const label = document.getElementById("vs-mic-level-label");
    if (label) label.innerText = "Listening 1.5s...";

    try {
      const res = await fetch("/api/voice/test-mic", { method: "POST" });
      const data = await res.json();
      if (data.ok && bar) {
        const pct = Math.min(100, Math.round((data.rms / 600) * 100));
        bar.style.width = `${pct}%`;
        if (pct > 60) {
          bar.style.background = "var(--amber)";
        } else if (pct > 20) {
          bar.style.background = "var(--accent)";
        } else {
          bar.style.background = "var(--green)";
        }
        if (label) label.innerText = `Peak: ${data.peak} | RMS: ${data.rms} (${pct}%)`;
      } else if (label) {
        label.innerText = data.message || "Failed";
      }
    } catch (err) {
      if (label) label.innerText = "Error testing mic";
    }
  }

  // -------------------------------------------------------------------------
  // Web Audio Synthetic Chimes
  // -------------------------------------------------------------------------
  function playSyntheticChime(type, volumePct = 80) {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      if (!audioContext) audioContext = new AudioCtx();
      if (audioContext.state === "suspended") audioContext.resume();

      const gain = audioContext.createGain();
      const masterVol = Math.max(0.01, (volumePct / 100) * 0.2);
      gain.gain.setValueAtTime(masterVol, audioContext.currentTime);
      gain.connect(audioContext.destination);

      const now = audioContext.currentTime;
      if (type === "listening") {
        const osc1 = audioContext.createOscillator();
        osc1.type = "sine";
        osc1.frequency.setValueAtTime(520, now);
        osc1.connect(gain);
        osc1.start(now);
        osc1.stop(now + 0.12);

        const osc2 = audioContext.createOscillator();
        osc2.type = "sine";
        osc2.frequency.setValueAtTime(680, now + 0.12);
        osc2.connect(gain);
        osc2.start(now + 0.12);
        osc2.stop(now + 0.26);

        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.26);
      } else {
        const osc1 = audioContext.createOscillator();
        osc1.type = "sine";
        osc1.frequency.setValueAtTime(680, now);
        osc1.connect(gain);
        osc1.start(now);
        osc1.stop(now + 0.14);

        const osc2 = audioContext.createOscillator();
        osc2.type = "sine";
        osc2.frequency.setValueAtTime(480, now + 0.14);
        osc2.connect(gain);
        osc2.start(now + 0.14);
        osc2.stop(now + 0.32);

        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.32);
      }
    } catch (e) {
      // AudioContext unavailable or blocked by autoplay policy
    }
  }

  // Export
  window.NallaVoiceStudio = {
    playChime: playSyntheticChime,
    showNotice: function (msg) {
      const banner = document.getElementById("vs-notice-banner");
      const text = document.getElementById("vs-notice-text");
      if (banner && text) {
        text.innerText = msg;
        banner.style.display = "flex";
      }
    },
    loadVoiceStatus: loadVoiceStatus,
    reloadProfiles: loadVoiceSettings,
  };

  // Auto-init on page load
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initVoiceStudio);
  } else {
    initVoiceStudio();
  }
})();
