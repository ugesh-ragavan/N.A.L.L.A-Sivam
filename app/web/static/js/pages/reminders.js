/**
 * app/web/static/js/pages/reminders.js
 *
 * Reminders & Tasks Controller:
 *   • List with due times and status badges.
 *   • Quick add form with datetime-local picker.
 *   • Mark done button.
 *   • Snooze (+15m, +30m, +60m) controls.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  function initReminders() {
    const form = document.getElementById("rem-add-form");
    if (form) {
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        handleAddReminder();
      });
    }

    // Set default due time: 15 minutes from now
    const dueInput = document.getElementById("rem-due-input");
    if (dueInput) {
      const now = new Date();
      now.setMinutes(now.getMinutes() + 15);
      const pad = (n) => String(n).padStart(2, "0");
      const localIso = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
      dueInput.value = localIso;
    }

    loadReminders();
  }

  async function loadReminders() {
    const listEl = document.getElementById("rem-list-container");
    if (!listEl) return;

    try {
      const res = await fetch("/api/reminders");
      const reminders = await res.json();

      if (reminders.length === 0) {
        listEl.innerHTML = `<div style="text-align:center; color:var(--text-dim); padding:40px;">No scheduled reminders or tasks.</div>`;
        return;
      }

      listEl.innerHTML = reminders.map((r) => {
        const isDone = r.status === "done";
        const dueFormatted = r.due_at.replace("T", " ").slice(0, 16);
        return `
          <div class="hud-card" style="padding:14px 18px; display:flex; justify-content:space-between; align-items:center;">
            <div style="flex:1;">
              <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                <span class="badge ${isDone ? "badge-green" : "badge-amber"}">${r.status}</span>
                <span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-dim);">Due: ${dueFormatted}</span>
              </div>
              <div style="font-size:0.95rem; ${isDone ? "text-decoration:line-through; color:var(--text-dim);" : "color:var(--text-bright);"}">
                ${window.NallaApp.escapeHtml(r.text)}
              </div>
            </div>
            <div style="display:flex; gap:8px;">
              ${!isDone ? `
                <button class="btn btn-sm btn-green" onclick="window.NallaPages.reminders.markDone(${r.id})">✓ Done</button>
                <button class="btn btn-sm btn-outline" onclick="window.NallaPages.reminders.snooze(${r.id}, 15)">+15m</button>
              ` : `
                <span style="font-size:0.75rem; color:var(--text-dim);">Completed</span>
              `}
            </div>
          </div>
        `;
      }).join("");
    } catch (e) {
      console.error("Failed loading reminders", e);
    }
  }

  async function handleAddReminder() {
    const textEl = document.getElementById("rem-text-input");
    const dueEl = document.getElementById("rem-due-input");
    const text = textEl ? textEl.value.trim() : "";
    const due_at = dueEl ? dueEl.value : "";

    if (!text || !due_at) return;

    try {
      const res = await fetch("/api/reminders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, due_at }),
      });
      if (res.ok) {
        textEl.value = "";
        window.NallaApp.showToast("Reminder scheduled!", "success");
        loadReminders();
      } else {
        window.NallaApp.showToast("Failed to schedule reminder.", "alert");
      }
    } catch (e) {
      window.NallaApp.showToast("Network error.", "alert");
    }
  }

  async function markDone(id) {
    try {
      const res = await fetch(`/api/reminders/${id}/done`, { method: "PATCH" });
      if (res.ok) {
        window.NallaApp.showToast(`Reminder #${id} marked done.`, "success");
        loadReminders();
      }
    } catch (e) {
      window.NallaApp.showToast("Error updating reminder.", "alert");
    }
  }

  async function snooze(id, minutes = 15) {
    try {
      const res = await fetch(`/api/reminders/${id}/snooze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ minutes }),
      });
      if (res.ok) {
        window.NallaApp.showToast(`Reminder #${id} snoozed by ${minutes}m.`, "info");
        loadReminders();
      }
    } catch (e) {
      window.NallaApp.showToast("Error snoozing reminder.", "alert");
    }
  }

  window.NallaPages.reminders = {
    init: initReminders,
    onActivate: loadReminders,
    markDone,
    snooze,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initReminders);
  } else {
    initReminders();
  }
})();
