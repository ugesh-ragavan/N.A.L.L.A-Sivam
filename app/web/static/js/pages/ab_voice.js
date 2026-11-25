/**
 * app/web/static/js/pages/ab_voice.js
 *
 * Voice Quality A/B Benchmark & Evaluation Controller (Step 8):
 * Compares the exact same 10 standardized lines under:
 *   - Setting A (Old Setup): Formal text, raw un-normalized audio, separate unbuffered playback chunks, FX Off.
 *   - Setting B (New Setup): "Write for the Ear" persona, 15ms cosine fade, RMS norm (0.15), Voice FX (JARVIS/Clean), gapless playback.
 * Provides Good / OK / Bad rating buttons per clip with persistence in data/ab_test_results.json.
 */

(function () {
  "use strict";

  window.NallaVoiceAB = window.NallaVoiceAB || {};

  let benchmarkLines = [];
  let currentRatings = {}; // key: `${lineId}_${setting}` -> rating
  let currentAudioPlayer = null;
  let currentPlayingKey = null;

  function init() {
    setupModalEvents();
  }

  function setupModalEvents() {
    const closeBtn = document.getElementById("ab-close-btn");
    const refreshBtn = document.getElementById("ab-refresh-btn");
    const genAllBtn = document.getElementById("ab-generate-all-btn");
    const fxSelect = document.getElementById("ab-voice-fx-select");

    if (closeBtn) closeBtn.addEventListener("click", closeModal);
    if (refreshBtn) refreshBtn.addEventListener("click", () => {
      loadRatingsAndSummary();
      window.NallaApp.showToast("Benchmark results refreshed.", "info");
    });
    if (genAllBtn) genAllBtn.addEventListener("click", generateAllAudio);
    if (fxSelect) {
      fxSelect.addEventListener("change", () => {
        renderBenchmarkCards();
      });
    }

    // Modal triggers from settings and voice lab
    const setOpenBtn = document.getElementById("set-open-ab-btn");
    if (setOpenBtn) setOpenBtn.addEventListener("click", openModal);

    const vlOpenBtn = document.getElementById("vl-open-ab-btn");
    if (vlOpenBtn) vlOpenBtn.addEventListener("click", openModal);

    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        const modal = document.getElementById("voice-ab-modal");
        if (modal && modal.classList.contains("show")) closeModal();
      }
    });
  }

  function openModal() {
    const modal = document.getElementById("voice-ab-modal");
    if (modal) {
      modal.classList.add("show");
      loadBenchmarkLines().then(() => {
        loadRatingsAndSummary();
      });
    }
  }

  function closeModal() {
    const modal = document.getElementById("voice-ab-modal");
    if (modal) modal.classList.remove("show");
    stopAudio();
  }

  function stopAudio() {
    if (currentAudioPlayer) {
      try {
        currentAudioPlayer.pause();
        currentAudioPlayer.currentTime = 0;
      } catch (e) {}
      currentAudioPlayer = null;
    }
    currentPlayingKey = null;
    document.querySelectorAll(".ab-play-btn").forEach((btn) => {
      btn.innerText = "▶ Play";
    });
  }

  async function loadBenchmarkLines() {
    try {
      const resp = await fetch("/api/voice/ab/lines");
      const data = await resp.json();
      if (data.ok && Array.isArray(data.lines)) {
        benchmarkLines = data.lines;
        renderBenchmarkCards();
      }
    } catch (exc) {
      console.error("Failed to load benchmark lines:", exc);
    }
  }

  async function loadRatingsAndSummary() {
    try {
      const resp = await fetch("/api/voice/ab/results");
      const data = await resp.json();
      if (data.ok) {
        currentRatings = {};
        if (Array.isArray(data.ratings)) {
          data.ratings.forEach((r) => {
            currentRatings[`${r.line_id}_${r.setting}`] = r.rating;
          });
        }
        updateSummaryUI(data.summary || {});
        updateActiveRatingButtons();
      }
    } catch (exc) {
      console.error("Failed to load A/B results:", exc);
    }
  }

  function updateSummaryUI(summary) {
    const scoreAEl = document.getElementById("ab-score-a");
    const scoreBEl = document.getElementById("ab-score-b");
    const winRateEl = document.getElementById("ab-win-rate-badge");
    const statusEl = document.getElementById("ab-acceptance-status");

    const aGood = summary.setting_a_good || 0;
    const aOk = summary.setting_a_ok || 0;
    const aBad = summary.setting_a_bad || 0;

    const bGood = summary.setting_b_good || 0;
    const bOk = summary.setting_b_ok || 0;
    const bBad = summary.setting_b_bad || 0;

    if (scoreAEl) scoreAEl.innerText = `${aGood} Good / ${aOk} OK / ${aBad} Bad`;
    if (scoreBEl) scoreBEl.innerText = `${bGood} Good / ${bOk} OK / ${bBad} Bad`;

    const winRate = summary.preference_b_rate || 0.0;
    if (winRateEl) {
      winRateEl.innerText = `Setting B Win Rate: ${winRate}%`;
      if (winRate >= 70.0) {
        winRateEl.style.borderColor = "var(--accent)";
        winRateEl.style.color = "var(--accent)";
        winRateEl.style.background = "rgba(0, 255, 200, 0.15)";
      }
    }

    const ratedCount = Math.min(10, Math.max(aGood + aOk + aBad, bGood + bOk + bBad));
    if (statusEl) {
      statusEl.innerText = `${ratedCount} of 10 Evaluated (${bGood} rated clearly natural)`;
    }
  }

  function renderBenchmarkCards() {
    const list = document.getElementById("ab-benchmark-list");
    if (!list) return;

    if (!benchmarkLines.length) {
      list.innerHTML = `<div style="text-align:center; padding:40px; color:var(--text-dim);">No benchmark lines found.</div>`;
      return;
    }

    const fxMode = document.getElementById("ab-voice-fx-select")?.value || "jarvis";

    list.innerHTML = benchmarkLines
      .map((line) => {
        return `
        <div class="hud-card" style="background:rgba(255,255,255,0.02); border:1px solid var(--border-subtle); border-radius:var(--radius-sm); padding:14px 16px;">
          <!-- Line Header -->
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:6px;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span class="badge badge-accent" style="font-weight:700;">#${line.id}</span>
              <span style="font-weight:700; font-size:0.95rem; color:var(--text-bright);">${escapeHtml(line.title)}</span>
            </div>
            <div style="font-size:0.75rem; color:var(--text-dim); font-style:italic;">
              🎯 Focus: ${escapeHtml(line.focus)}
            </div>
          </div>

          <!-- Comparison Grid (2 columns: Setting A vs Setting B) -->
          <div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px;">
            <!-- Column A: Setting A (Old Setup) -->
            <div style="background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.07); border-radius:var(--radius-sm); padding:12px; display:flex; flex-direction:column; justify-content:space-between;">
              <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                  <span style="font-weight:700; font-size:0.82rem; color:var(--text-dim); text-transform:uppercase;">Setting A (Old Setup)</span>
                  <span class="badge" style="font-size:0.68rem; background:rgba(255,100,100,0.1); color:#ff8080; border:1px solid rgba(255,100,100,0.3);">Formal / Raw / No Fade</span>
                </div>
                <div style="font-size:0.86rem; color:var(--text-main); margin-bottom:10px; line-height:1.4; font-family:serif; font-style:italic;">
                  "${escapeHtml(line.old_text)}"
                </div>
              </div>

              <!-- Audio & Rating Toolbar -->
              <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid rgba(255,255,255,0.05); padding-top:8px; margin-top:8px;">
                <button class="btn btn-xs btn-outline ab-play-btn" id="btn-play-${line.id}-A" onclick="window.NallaVoiceAB.playClip(${line.id}, 'A')">
                  ▶ Play
                </button>
                <div style="display:flex; align-items:center; gap:4px;">
                  <span style="font-size:0.72rem; color:var(--text-dim); margin-right:4px;">Rate:</span>
                  <button class="btn btn-xs btn-outline ab-rate-btn" data-key="${line.id}_A" data-rating="good" onclick="window.NallaVoiceAB.rateClip(${line.id}, 'A', 'good')">👍 Good</button>
                  <button class="btn btn-xs btn-outline ab-rate-btn" data-key="${line.id}_A" data-rating="ok" onclick="window.NallaVoiceAB.rateClip(${line.id}, 'A', 'ok')">😐 OK</button>
                  <button class="btn btn-xs btn-outline ab-rate-btn" data-key="${line.id}_A" data-rating="bad" onclick="window.NallaVoiceAB.rateClip(${line.id}, 'A', 'bad')">👎 Bad</button>
                </div>
              </div>
            </div>

            <!-- Column B: Setting B (New Setup) -->
            <div style="background:rgba(0,255,200,0.03); border:1px solid rgba(0,255,200,0.2); border-radius:var(--radius-sm); padding:12px; display:flex; flex-direction:column; justify-content:space-between;">
              <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                  <span style="font-weight:700; font-size:0.82rem; color:var(--accent); text-transform:uppercase;">Setting B (New Setup)</span>
                  <span class="badge" style="font-size:0.68rem; background:rgba(0,255,200,0.15); color:var(--accent); border:1px solid var(--accent);">Write for Ear · 15ms Fade · RMS Norm · FX ${fxMode.toUpperCase()}</span>
                </div>
                <div style="font-size:0.86rem; color:var(--text-bright); margin-bottom:10px; line-height:1.4; font-weight:500;">
                  "${escapeHtml(line.new_text)}"
                </div>
              </div>

              <!-- Audio & Rating Toolbar -->
              <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid rgba(0,255,200,0.1); padding-top:8px; margin-top:8px;">
                <button class="btn btn-xs btn-primary ab-play-btn" id="btn-play-${line.id}-B" onclick="window.NallaVoiceAB.playClip(${line.id}, 'B')">
                  ▶ Play
                </button>
                <div style="display:flex; align-items:center; gap:4px;">
                  <span style="font-size:0.72rem; color:var(--text-dim); margin-right:4px;">Rate:</span>
                  <button class="btn btn-xs btn-outline ab-rate-btn" data-key="${line.id}_B" data-rating="good" onclick="window.NallaVoiceAB.rateClip(${line.id}, 'B', 'good')">👍 Good</button>
                  <button class="btn btn-xs btn-outline ab-rate-btn" data-key="${line.id}_B" data-rating="ok" onclick="window.NallaVoiceAB.rateClip(${line.id}, 'B', 'ok')">😐 OK</button>
                  <button class="btn btn-xs btn-outline ab-rate-btn" data-key="${line.id}_B" data-rating="bad" onclick="window.NallaVoiceAB.rateClip(${line.id}, 'B', 'bad')">👎 Bad</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      `;
      })
      .join("");

    updateActiveRatingButtons();
  }

  function updateActiveRatingButtons() {
    document.querySelectorAll(".ab-rate-btn").forEach((btn) => {
      const key = btn.getAttribute("data-key");
      const rating = btn.getAttribute("data-rating");
      if (currentRatings[key] === rating) {
        btn.classList.add("active");
        if (rating === "good") {
          btn.style.background = "rgba(0, 255, 120, 0.2)";
          btn.style.borderColor = "#00ff88";
          btn.style.color = "#00ff88";
        } else if (rating === "ok") {
          btn.style.background = "rgba(255, 200, 0, 0.2)";
          btn.style.borderColor = "#ffcc00";
          btn.style.color = "#ffcc00";
        } else if (rating === "bad") {
          btn.style.background = "rgba(255, 60, 60, 0.2)";
          btn.style.borderColor = "#ff4444";
          btn.style.color = "#ff4444";
        }
      } else {
        btn.classList.remove("active");
        btn.style.background = "";
        btn.style.borderColor = "";
        btn.style.color = "";
      }
    });
  }

  async function playClip(lineId, setting) {
    const key = `${lineId}_${setting}`;
    const playBtn = document.getElementById(`btn-play-${lineId}-${setting}`);

    if (currentPlayingKey === key && currentAudioPlayer) {
      stopAudio();
      return;
    }

    stopAudio();

    if (playBtn) playBtn.innerText = "⏳ Loading...";

    const fxMode = document.getElementById("ab-voice-fx-select")?.value || "jarvis";

    try {
      const resp = await fetch("/api/voice/ab/synthesize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line_id: lineId, setting: setting, fx_mode: fxMode }),
      });
      const data = await resp.json();
      if (!data.ok || !data.audio_url) {
        throw new Error(data.detail || "Failed to generate audio clip");
      }

      currentPlayingKey = key;
      const audio = new Audio(data.audio_url);
      currentAudioPlayer = audio;

      audio.onended = () => {
        stopAudio();
      };
      audio.onerror = () => {
        window.NallaApp.showToast("Audio playback error", "error");
        stopAudio();
      };

      await audio.play();
      if (playBtn) playBtn.innerText = "⏹ Stop";
    } catch (exc) {
      console.error("Audio playback failure:", exc);
      window.NallaApp.showToast(`Audio generation failed: ${exc.message}`, "error");
      if (playBtn) playBtn.innerText = "▶ Play";
    }
  }

  async function rateClip(lineId, setting, rating) {
    try {
      const resp = await fetch("/api/voice/ab/rate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line_id: lineId, setting: setting, rating: rating }),
      });
      const data = await resp.json();
      if (data.ok) {
        currentRatings[`${lineId}_${setting}`] = rating;
        updateSummaryUI(data.summary || {});
        updateActiveRatingButtons();
        window.NallaApp.showToast(`Saved ${rating.toUpperCase()} rating for Line #${lineId} (${setting})`, "success");
      }
    } catch (exc) {
      console.error("Failed to save rating:", exc);
      window.NallaApp.showToast("Could not save rating", "error");
    }
  }

  async function generateAllAudio() {
    const genBtn = document.getElementById("ab-generate-all-btn");
    if (genBtn) {
      genBtn.disabled = true;
      genBtn.innerText = "⏳ Synthesizing (0/20)...";
    }

    const fxMode = document.getElementById("ab-voice-fx-select")?.value || "jarvis";
    let count = 0;

    for (const line of benchmarkLines) {
      for (const setting of ["A", "B"]) {
        try {
          await fetch("/api/voice/ab/synthesize", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ line_id: line.id, setting: setting, fx_mode: fxMode }),
          });
          count++;
          if (genBtn) genBtn.innerText = `⏳ Synthesizing (${count}/20)...`;
        } catch (e) {
          console.warn(`Failed synthesis for ${line.id}_${setting}:`, e);
        }
      }
    }

    if (genBtn) {
      genBtn.disabled = false;
      genBtn.innerText = "⚡ Synthesize All Audio";
    }
    window.NallaApp.showToast("All 10 benchmark lines synthesized for A and B!", "success");
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

  // Public API
  window.NallaVoiceAB = {
    init,
    openModal,
    closeModal,
    playClip,
    rateClip,
    generateAllAudio,
  };

  document.addEventListener("DOMContentLoaded", init);
})();
