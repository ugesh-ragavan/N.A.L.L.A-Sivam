/**
 * app/web/static/js/pages/voice.js
 *
 * Voice Studio Controller:
 *   • Voice Picker (EdgeTTS & Piper) with live speech audio preview.
 *   • Speech Rate, Pitch, and Volume sliders with live preview.
 *   • Voice Profiles ("Warm Companion", "Robotic Butler", "Tamil Native", "Offline Piper").
 *   • STT Model selection (base vs small).
 *   • Real-time Microphone Meter and Test Mic / Speaker controls.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  const PROFILES = {
    warm: {
      name: "Warm Companion",
      voice: "en-IN-NeerjaNeural",
      rate: 1.0,
      pitch: 0,
      volume: 100,
    },
    butler: {
      name: "Robotic Butler",
      voice: "en-IN-PrabhatNeural",
      rate: 1.05,
      pitch: -5,
      volume: 100,
    },
    tamil: {
      name: "Tamil Native",
      voice: "ta-IN-PallaviNeural",
      rate: 1.0,
      pitch: 0,
      volume: 100,
    },
    piper: {
      name: "Offline Piper",
      voice: "piper-lessac",
      rate: 1.1,
      pitch: 0,
      volume: 100,
    },
  };

  function initVoiceStudio() {
    // Profile buttons
    document.querySelectorAll(".profile-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pKey = btn.dataset.profile;
        if (PROFILES[pKey]) applyProfile(PROFILES[pKey]);
      });
    });

    // Sliders
    const rateSlider = document.getElementById("vs-rate-slider");
    const pitchSlider = document.getElementById("vs-pitch-slider");
    const volSlider = document.getElementById("vs-vol-slider");

    if (rateSlider) {
      rateSlider.addEventListener("input", (e) => {
        document.getElementById("vs-rate-val").innerText = `${parseFloat(e.target.value).toFixed(2)}x`;
      });
    }
    if (pitchSlider) {
      pitchSlider.addEventListener("input", (e) => {
        document.getElementById("vs-pitch-val").innerText = `${e.target.value > 0 ? "+" : ""}${e.target.value} Hz`;
      });
    }
    if (volSlider) {
      volSlider.addEventListener("input", (e) => {
        document.getElementById("vs-vol-val").innerText = `${e.target.value}%`;
      });
    }

    // Buttons
    const testSpeakerBtn = document.getElementById("vs-test-speaker-btn");
    const testMicBtn = document.getElementById("vs-test-mic-btn");

    if (testSpeakerBtn) {
      testSpeakerBtn.addEventListener("click", () => window.NallaApp.testSpeaker());
    }
    if (testMicBtn) {
      testMicBtn.addEventListener("click", () => runMicMeterTest());
    }

    loadVoiceSettings();
  }

  function applyProfile(profile) {
    const voiceSelect = document.getElementById("vs-voice-select");
    const rateSlider = document.getElementById("vs-rate-slider");
    const pitchSlider = document.getElementById("vs-pitch-slider");
    const volSlider = document.getElementById("vs-vol-slider");

    if (voiceSelect) voiceSelect.value = profile.voice;
    if (rateSlider) {
      rateSlider.value = profile.rate;
      document.getElementById("vs-rate-val").innerText = `${profile.rate}x`;
    }
    if (pitchSlider) {
      pitchSlider.value = profile.pitch;
      document.getElementById("vs-pitch-val").innerText = `${profile.pitch} Hz`;
    }
    if (volSlider) {
      volSlider.value = profile.volume;
      document.getElementById("vs-vol-val").innerText = `${profile.volume}%`;
    }

    window.NallaApp.showToast(`Applied '${profile.name}' voice profile.`, "info");
  }

  async function loadVoiceSettings() {
    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const s = await res.json();
        const vsVoice = document.getElementById("vs-voice-select");
        const vsStt = document.getElementById("vs-stt-select");
        if (vsVoice && s.tts_voice) vsVoice.value = s.tts_voice;
        if (vsStt && s.whisper_model) vsStt.value = s.whisper_model;
      }
    } catch (e) {
      // Fallback
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
        // RMS scaling 0 to ~1000
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

  window.NallaPages.voice = {
    init: initVoiceStudio,
    onActivate: loadVoiceSettings,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initVoiceStudio);
  } else {
    initVoiceStudio();
  }
})();
