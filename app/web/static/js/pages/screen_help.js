/**
 * screen_help.js - Screen Inspection & Sensitivity Blur Gate (Prompt 2A)
 *
 * Privacy & Safety guarantees:
 * - Local preview only. Nothing leaves the machine before user presses Send.
 * - Interactive canvas for drawing redaction/blur rectangles on sensitive text.
 * - Screenshots live in-memory only and are discarded after analysis or Cancel.
 * - Keyboard shortcut Ctrl+Shift+S or voice shortcut ("look at my screen", "what's this error").
 */

(function () {
  let currentCaptureId = null;
  let currentImage = null;
  let blurRects = [];
  let isDrawing = false;
  let startX = 0;
  let startY = 0;
  let originalWidth = 0;
  let originalHeight = 0;
  let rememberSession = false;

  const modal = document.getElementById("screen-gate-modal");
  const canvas = document.getElementById("screen-preview-canvas");
  const ctx = canvas ? canvas.getContext("2d") : null;
  const blurCountEl = document.getElementById("screen-gate-blur-count");
  const questionInput = document.getElementById("screen-question-input");
  const sendBtn = document.getElementById("screen-gate-send-btn");
  const cancelBtn = document.getElementById("screen-gate-cancel-btn");
  const clearBlursBtn = document.getElementById("screen-clear-blurs-btn");
  const saveDiskBtn = document.getElementById("screen-save-disk-btn");
  const rememberChk = document.getElementById("screen-remember-session-chk");
  const diagnosisPanel = document.getElementById("screen-diagnosis-panel");
  const diagnosisContent = document.getElementById("screen-diagnosis-content");
  const diagnosisLatency = document.getElementById("screen-diagnosis-latency");
  const orbScreenBtn = document.getElementById("orb-screen-btn");

  function showToast(msg, isError = false) {
    if (window.showToast) {
      window.showToast(msg, isError);
    } else {
      console.log("[ScreenGate Toast]", msg);
    }
  }

  function redrawCanvas(tempRect = null) {
    if (!ctx || !currentImage) return;

    // Clear and draw image
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(currentImage, 0, 0, canvas.width, canvas.height);

    // Draw saved blur/redaction rectangles
    blurRects.forEach((r, idx) => {
      ctx.fillStyle = "rgba(0, 0, 0, 0.85)";
      ctx.fillRect(r.x, r.y, r.w, r.h);

      ctx.strokeStyle = "rgba(0, 229, 255, 0.9)";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(r.x, r.y, r.w, r.h);

      // Label
      ctx.fillStyle = "rgba(0, 229, 255, 0.9)";
      ctx.font = "10px monospace";
      ctx.fillText(`BLUR #${idx + 1}`, r.x + 4, r.y + 12);
    });

    // Draw active transient rectangle while dragging
    if (tempRect) {
      ctx.fillStyle = "rgba(255, 42, 109, 0.35)";
      ctx.fillRect(tempRect.x, tempRect.y, tempRect.w, tempRect.h);
      ctx.strokeStyle = "rgba(255, 42, 109, 0.9)";
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(tempRect.x, tempRect.y, tempRect.w, tempRect.h);
      ctx.setLineDash([]);
    }

    if (blurCountEl) {
      blurCountEl.textContent = `${blurRects.length} blur zone${blurRects.length === 1 ? "" : "s"}`;
    }
  }

  function openScreenGateModal(captureId, thumbnail, query, width, height) {
    currentCaptureId = captureId;
    blurRects = [];
    originalWidth = width || 1280;
    originalHeight = height || 720;

    if (questionInput) {
      questionInput.value = query || "What's this error?";
    }
    if (diagnosisPanel) {
      diagnosisPanel.style.display = "none";
      if (diagnosisContent) diagnosisContent.textContent = "";
    }
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.innerHTML = "🚀 Send to Nalla";
    }

    const img = new Image();
    img.onload = function () {
      currentImage = img;
      // Set canvas display size
      const maxDisplayWidth = Math.min(window.innerWidth * 0.88, 920);
      const aspect = img.height / img.width;
      canvas.width = maxDisplayWidth;
      canvas.height = Math.round(maxDisplayWidth * aspect);

      redrawCanvas();
      if (modal) {
        modal.classList.add("active");
        modal.style.display = "flex";
      }
    };
    img.src = thumbnail;
  }

  function closeScreenGateModal() {
    if (modal) {
      modal.classList.remove("active");
      modal.style.display = "none";
    }
    currentCaptureId = null;
    currentImage = null;
    blurRects = [];
  }

  async function triggerScreenCapture(target = "full", defaultQuery = "What's this error?") {
    try {
      showToast("Capturing screen preview for Sensitivity Gate...");
      const res = await fetch("/api/screen/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: target }),
      });
      const data = await res.json();
      if (!data.ok) {
        showToast("Screen capture failed: " + (data.error || "Unknown error"), true);
        return;
      }
      openScreenGateModal(data.capture_id, data.thumbnail, defaultQuery, data.width, data.height);
    } catch (err) {
      console.error("Screen capture error:", err);
      showToast("Screen capture request failed: " + err.message, true);
    }
  }

  async function cancelCapture() {
    if (!currentCaptureId) {
      closeScreenGateModal();
      return;
    }
    try {
      const cid = currentCaptureId;
      closeScreenGateModal();
      await fetch("/api/screen/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ capture_id: cid }),
      });
      showToast("Screen inspection cancelled. Image discarded from memory.");
    } catch (err) {
      console.warn("Cancel capture error:", err);
    }
  }

  async function sendCaptureToNalla() {
    if (!currentCaptureId) return;

    const question = questionInput ? questionInput.value.trim() : "What's this error?";
    if (!question) {
      showToast("Please enter a question or keep the default.", true);
      return;
    }

    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.innerHTML = "⏳ Diagnosing with Gemini...";
    }

    // Scale blur rectangles to original image coordinate space
    const scaleX = originalWidth / canvas.width;
    const scaleY = originalHeight / canvas.height;
    const scaledBlurs = blurRects.map((r) => ({
      x: Math.round(r.x * scaleX),
      y: Math.round(r.y * scaleY),
      w: Math.round(r.w * scaleX),
      h: Math.round(r.h * scaleY),
    }));

    try {
      const res = await fetch("/api/screen/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          capture_id: currentCaptureId,
          question: question,
          blur_rectangles: scaledBlurs,
          session_id: "web",
          remember_session: rememberChk ? rememberChk.checked : false,
        }),
      });

      const data = await res.json();
      if (!data.ok && !data.reply) {
        showToast("Screen diagnosis error: " + (data.error || "Failed to analyze"), true);
        if (sendBtn) {
          sendBtn.disabled = false;
          sendBtn.innerHTML = "🚀 Send to Nalla";
        }
        return;
      }

      // Display response inside diagnosis panel
      if (diagnosisPanel) {
        diagnosisPanel.style.display = "block";
        if (diagnosisLatency) {
          diagnosisLatency.textContent = `Latency: ${data.latency_ms || 0} ms`;
        }
        if (diagnosisContent) {
          diagnosisContent.textContent = data.reply;
        }
      }

      if (sendBtn) {
        sendBtn.innerHTML = "✓ Done";
      }

      // Update HUD transcript / subtitles if available
      const subtitlesEl = document.getElementById("hud-subtitles-text");
      if (subtitlesEl && data.spoken) {
        subtitlesEl.textContent = data.spoken;
      }

      showToast("Screen analysis complete!");
    } catch (err) {
      console.error("Screen analyze error:", err);
      showToast("Screen analysis request failed: " + err.message, true);
      if (sendBtn) {
        sendBtn.disabled = false;
        sendBtn.innerHTML = "🚀 Send to Nalla";
      }
    }
  }

  async function saveToDisk() {
    if (!currentCaptureId) return;
    try {
      const res = await fetch("/api/screen/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ capture_id: currentCaptureId }),
      });
      const data = await res.json();
      if (data.ok) {
        showToast(`Saved to ${data.path}`);
      } else {
        showToast("Failed to save image.", true);
      }
    } catch (err) {
      showToast("Save image error: " + err.message, true);
    }
  }

  // Canvas Mouse & Touch Drawing Listeners for Blurs
  if (canvas) {
    function getCanvasCoords(e) {
      const rect = canvas.getBoundingClientRect();
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;
      return {
        x: Math.max(0, Math.min(canvas.width, clientX - rect.left)),
        y: Math.max(0, Math.min(canvas.height, clientY - rect.top)),
      };
    }

    canvas.addEventListener("mousedown", (e) => {
      isDrawing = true;
      const coords = getCanvasCoords(e);
      startX = coords.x;
      startY = coords.y;
    });

    canvas.addEventListener("mousemove", (e) => {
      if (!isDrawing) return;
      const coords = getCanvasCoords(e);
      const x = Math.min(startX, coords.x);
      const y = Math.min(startY, coords.y);
      const w = Math.abs(coords.x - startX);
      const h = Math.abs(coords.y - startY);
      redrawCanvas({ x, y, w, h });
    });

    window.addEventListener("mouseup", (e) => {
      if (!isDrawing) return;
      isDrawing = false;
      const coords = getCanvasCoords(e);
      const x = Math.min(startX, coords.x);
      const y = Math.min(startY, coords.y);
      const w = Math.abs(coords.x - startX);
      const h = Math.abs(coords.y - startY);

      if (w > 8 && h > 8) {
        blurRects.push({ x, y, w, h });
      }
      redrawCanvas();
    });

    // Touch support
    canvas.addEventListener("touchstart", (e) => {
      e.preventDefault();
      isDrawing = true;
      const coords = getCanvasCoords(e);
      startX = coords.x;
      startY = coords.y;
    }, { passive: false });

    canvas.addEventListener("touchmove", (e) => {
      if (!isDrawing) return;
      e.preventDefault();
      const coords = getCanvasCoords(e);
      const x = Math.min(startX, coords.x);
      const y = Math.min(startY, coords.y);
      const w = Math.abs(coords.x - startX);
      const h = Math.abs(coords.y - startY);
      redrawCanvas({ x, y, w, h });
    }, { passive: false });

    canvas.addEventListener("touchend", (e) => {
      if (!isDrawing) return;
      isDrawing = false;
      redrawCanvas();
    });
  }

  // Button Listeners
  if (cancelBtn) cancelBtn.addEventListener("click", cancelCapture);
  if (sendBtn) sendBtn.addEventListener("click", sendCaptureToNalla);
  if (saveDiskBtn) saveDiskBtn.addEventListener("click", saveToDisk);
  if (clearBlursBtn) {
    clearBlursBtn.addEventListener("click", () => {
      blurRects = [];
      redrawCanvas();
    });
  }
  if (orbScreenBtn) {
    orbScreenBtn.addEventListener("click", () => triggerScreenCapture("full", "What's this error?"));
  }

  // Global Keyboard Shortcut: Ctrl + Shift + S
  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      triggerScreenCapture("full", "What's this error?");
    } else if (e.key === "Escape" && modal && modal.classList.contains("active")) {
      cancelCapture();
    }
  });

  // Export functions to global scope for WebSocket and voice integration
  window.triggerScreenCapture = triggerScreenCapture;
  window.openScreenGateModal = openScreenGateModal;

  // Hook into WebSocket messages for voice shortcut triggers
  const existingOnMessage = window.onVoiceWsMessage;
  window.onVoiceWsMessage = function (data) {
    if (existingOnMessage) existingOnMessage(data);
    if (data.type === "screen_preview_request") {
      openScreenGateModal(data.capture_id, data.thumbnail, data.query, data.width, data.height);
    }
  };
})();
