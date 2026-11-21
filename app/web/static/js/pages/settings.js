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
        localStorage.setItem("nalla_sound", e.target.checked ? "true" : "false");
        window.NallaApp.showToast(e.target.checked ? "Audio chimes enabled." : "Audio muted.", "info");
      });
    }

    // Danger Zone Clear Memory
    const clearMemBtn = document.getElementById("set-clear-memory-btn");
    if (clearMemBtn) {
      clearMemBtn.addEventListener("click", handleClearAllMemory);
    }

    loadSettings();
  }

  async function loadSettings() {
    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const data = await res.json();
        const chatSel = document.getElementById("set-chat-model");
        const cmdSel = document.getElementById("set-cmd-model");
        const soundToggle = document.getElementById("set-sound-toggle");

        if (chatSel && data.chat_model) chatSel.value = data.chat_model;
        if (cmdSel && data.command_model) cmdSel.value = data.command_model;

        const savedSound = localStorage.getItem("nalla_sound");
        if (soundToggle && savedSound !== null) {
          soundToggle.checked = savedSound === "true";
          window.NallaApp.setSoundEnabled(soundToggle.checked);
        }
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
