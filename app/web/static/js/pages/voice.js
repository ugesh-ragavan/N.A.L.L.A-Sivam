/**
 * app/web/static/js/pages/voice.js
 *
 * Voice Studio Controller:
 *   • Dynamic Voice Discovery: populates EdgeTTS & Piper voices grouped by language.
 *   • Voice Profiles Manager: saves & loads up to 5 named profiles in data/settings.json.
 *   • Bilingual Voice Routing: configurable English & Tamil voices for mixed speech.
 *   • Instant Voice Preview: speaks "Yes boss, sollunga!" with rate/pitch/volume.
 *   • Audio Feel: Speed presets (Relaxed, Normal, Quick), listening/done chimes.
 *   • STT & Hardware Controls: Whisper model, language, mic device, VAD sensitivity, live meter.
 *   • Fallback Notice: automatic visual banner when offline Piper fallback triggers.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  let currentProfiles = {};
  let activeProfileName = "Nalla Default";
  let audioContext = null;

  function initVoiceStudio() {
    setupSliders();
    setupSpeedPresets();
    setupProfileControls();
    setupButtons();
    loadDevices();
    loadVoiceList().then(() => {
      loadVoiceSettings();
    });

    // Listen for WebSocket notices & chimes
    if (window.NallaApp && window.NallaApp.onWebSocketMessage) {
      // already routed in app.js
    }
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
        document.getElementById("vs-rate-val").innerText = `${sign}${pct}% (${val.toFixed(2)}x)`;
      });
    }

    if (pitchSlider) {
      pitchSlider.addEventListener("input", (e) => {
        const val = parseInt(e.target.value, 10);
        document.getElementById("vs-pitch-val").innerText = `${val >= 0 ? "+" : ""}${val} Hz`;
      });
    }

    if (volSlider) {
      volSlider.addEventListener("input", (e) => {
        document.getElementById("vs-vol-val").innerText = `${e.target.value}%`;
      });
    }

    if (chimeVolSlider) {
      chimeVolSlider.addEventListener("input", (e) => {
        document.getElementById("vs-chime-vol-val").innerText = `${e.target.value}%`;
      });
    }

    if (micSensSlider) {
      micSensSlider.addEventListener("input", (e) => {
        document.getElementById("vs-mic-sens-val").innerText = e.target.value;
      });
    }
  }

  // -------------------------------------------------------------------------
  // Speed Presets
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
      });
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
        const engine = selected ? selected.dataset.engine : "edge-tts";
        const badge = document.getElementById("vs-voice-engine-badge");
        if (badge) badge.innerText = engine;
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
        const vsLang = document.getElementById("vs-stt-lang-select");
        const vsDev = document.getElementById("vs-mic-device-select");
        const vsSens = document.getElementById("vs-mic-sensitivity-slider");

        if (vsStt && stt.whisper_model) vsStt.value = stt.whisper_model;
        if (vsLang && stt.language) vsLang.value = stt.language;
        if (vsDev && stt.input_device !== null && stt.input_device !== undefined) {
          vsDev.value = String(stt.input_device);
        }
        if (vsSens && stt.sensitivity) {
          vsSens.value = stt.sensitivity;
          vsSens.dispatchEvent(new Event("input"));
        }
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

    const voiceSelect = document.getElementById("vs-voice-select");
    const voiceTaSelect = document.getElementById("vs-voice-ta-select");
    const rateSlider = document.getElementById("vs-rate-slider");
    const pitchSlider = document.getElementById("vs-pitch-slider");
    const volSlider = document.getElementById("vs-vol-slider");

    if (voiceSelect && p.voice) {
      voiceSelect.value = p.voice;
      voiceSelect.dispatchEvent(new Event("change"));
    }
    if (voiceTaSelect && p.voice_ta) {
      voiceTaSelect.value = p.voice_ta;
    }

    // Rate
    if (rateSlider && p.rate !== undefined) {
      let rVal = 1.0;
      if (typeof p.rate === "string") {
        const m = p.rate.match(/([+-]?\d+)/);
        if (m) rVal = 1.0 + parseInt(m[1], 10) / 100.0;
      } else {
        rVal = parseFloat(p.rate);
      }
      rateSlider.value = rVal;
      rateSlider.dispatchEvent(new Event("input"));
    }

    // Pitch
    if (pitchSlider && p.pitch !== undefined) {
      let pVal = 0;
      if (typeof p.pitch === "string") {
        const m = p.pitch.match(/([+-]?\d+)/);
        if (m) pVal = parseInt(m[1], 10);
      } else {
        pVal = parseInt(p.pitch, 10);
      }
      pitchSlider.value = pVal;
      pitchSlider.dispatchEvent(new Event("input"));
    }

    // Volume
    if (volSlider && p.volume !== undefined) {
      let vVal = 100;
      if (typeof p.volume === "string") {
        const m = p.volume.match(/([+-]?\d+)/);
        if (m) vVal = 100 + parseInt(m[1], 10);
      } else {
        vVal = parseInt(p.volume, 10);
      }
      volSlider.value = vVal;
      volSlider.dispatchEvent(new Event("input"));
    }
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
        const rateSlider = document.getElementById("vs-rate-slider");
        const pitchSlider = document.getElementById("vs-pitch-slider");
        const volSlider = document.getElementById("vs-vol-slider");

        const selectedOpt = voiceSelect.options[voiceSelect.selectedIndex];
        const engine = selectedOpt ? selectedOpt.dataset.engine || "edge-tts" : "edge-tts";

        currentProfiles[cleanName] = {
          engine: engine,
          voice: voiceSelect ? voiceSelect.value : "en-IN-NeerjaNeural",
          voice_ta: voiceTaSelect ? voiceTaSelect.value : "ta-IN-PallaviNeural",
          rate: rateSlider ? `${Math.round((parseFloat(rateSlider.value) - 1.0) * 100):+d}%` : "+0%",
          pitch: pitchSlider ? `${parseInt(pitchSlider.value, 10):+d}Hz` : "+0Hz",
          volume: volSlider ? `${parseInt(volSlider.value, 10) - 100:+d}%` : "+0%",
        };

        activeProfileName = cleanName;
        renderProfileDropdown();
        saveCurrentProfiles(cleanName);
        window.NallaApp.showToast(`Saved voice profile '${cleanName}'.`, "info");
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
  // Preview & Button Actions
  // -------------------------------------------------------------------------
  function setupButtons() {
    const previewBtn = document.getElementById("vs-preview-btn");
    const testSpeakerBtn = document.getElementById("vs-test-speaker-btn");
    const testMicBtn = document.getElementById("vs-test-mic-btn");
    const saveAllBtn = document.getElementById("vs-save-all-btn");

    if (previewBtn) {
      previewBtn.addEventListener("click", () => runVoicePreview());
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

  async function runVoicePreview() {
    const previewBtn = document.getElementById("vs-preview-btn");
    const voiceSelect = document.getElementById("vs-voice-select");
    const rateSlider = document.getElementById("vs-rate-slider");
    const pitchSlider = document.getElementById("vs-pitch-slider");
    const volSlider = document.getElementById("vs-vol-slider");

    if (!voiceSelect) return;
    const selectedOpt = voiceSelect.options[voiceSelect.selectedIndex];
    const engine = selectedOpt ? selectedOpt.dataset.engine || "edge-tts" : "edge-tts";

    if (previewBtn) {
      previewBtn.disabled = true;
      previewBtn.innerHTML = "<span>⏳</span> Synthesizing preview...";
    }

    try {
      const res = await fetch("/api/voice/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: "Yes boss, sollunga!",
          engine: engine,
          voice: voiceSelect.value,
          rate: rateSlider ? parseFloat(rateSlider.value) : 1.0,
          pitch: pitchSlider ? parseInt(pitchSlider.value, 10) : 0,
          volume: volSlider ? parseInt(volSlider.value, 10) : 100,
        }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const blob = await res.blob();
      const audioUrl = URL.createObjectURL(blob);
      const audio = new Audio(audioUrl);
      audio.play();

      if (previewBtn) {
        previewBtn.innerHTML = "<span>🔊</span> Playing preview...";
      }

      audio.onended = () => {
        if (previewBtn) {
          previewBtn.disabled = false;
          previewBtn.innerHTML = '<span>▶️</span> Preview Voice ("Yes boss, sollunga!")';
        }
      };
    } catch (err) {
      if (previewBtn) {
        previewBtn.disabled = false;
        previewBtn.innerHTML = '<span>▶️</span> Preview Voice ("Yes boss, sollunga!")';
      }
      window.NallaApp.showToast(`Preview failed: ${err.message}`, "error");
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
      const vsLang = document.getElementById("vs-stt-lang-select");
      const vsDev = document.getElementById("vs-mic-device-select");
      const vsSens = document.getElementById("vs-mic-sensitivity-slider");

      await fetch("/api/voice/stt-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          whisper_model: vsStt ? vsStt.value : "base",
          language: vsLang ? vsLang.value : "auto",
          input_device: vsDev && vsDev.value ? parseInt(vsDev.value, 10) : null,
          sensitivity: vsSens ? parseFloat(vsSens.value) : 400.0,
        }),
      });

      window.NallaApp.showToast("Voice Studio settings saved live.", "info");
    } catch (err) {
      window.NallaApp.showToast("Failed to save settings: " + err.message, "error");
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
        // Two rising tones: 520Hz -> 680Hz
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
        // Two falling tones: 680Hz -> 480Hz
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
  };

  // Auto-init on page load
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initVoiceStudio);
  } else {
    initVoiceStudio();
  }
})();
