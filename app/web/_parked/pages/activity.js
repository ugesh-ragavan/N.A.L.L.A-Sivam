/**
 * app/web/static/js/pages/activity.js
 *
 * Activity Log Timeline:
 *   • Filterable timeline of events (messages, tool executions, approvals, device toggles, errors).
 *   • Filter pills (All, Voice, Chat, Tools, Approvals).
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  let currentEvents = [];
  let currentFilter = "all";

  function initActivity() {
    document.querySelectorAll(".activity-filter-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".activity-filter-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentFilter = btn.dataset.filter || "all";
        renderEvents();
      });
    });

    loadActivity();
  }

  async function loadActivity() {
    const listEl = document.getElementById("activity-timeline-list");
    if (!listEl) return;

    try {
      const res = await fetch("/api/activity");
      const data = await res.json();
      currentEvents = data.events || [];
      renderEvents();
    } catch (e) {
      console.error("Failed loading activity", e);
    }
  }

  function renderEvents() {
    const listEl = document.getElementById("activity-timeline-list");
    if (!listEl) return;

    const filtered = currentEvents.filter((ev) => {
      if (currentFilter === "all") return true;
      return (ev.category || "").toLowerCase() === currentFilter;
    });

    if (filtered.length === 0) {
      listEl.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">📋</div>
          <div class="empty-state-text">No activity events found in '${window.NallaApp.escapeHtml(currentFilter)}' category.</div>
        </div>
      `;
      return;
    }

    listEl.innerHTML = filtered.map((ev) => `
      <div style="display:flex; gap:16px; position:relative; padding-bottom:20px;">
        <div style="display:flex; flex-direction:column; align-items:center;">
          <div style="width:12px; height:12px; border-radius:50%; background:var(--accent); box-shadow:0 0 8px var(--accent-glow);"></div>
          <div style="width:2px; flex:1; background:var(--border-subtle); margin-top:4px;"></div>
        </div>
        <div style="flex:1; background:rgba(10,15,30,0.6); border:1px solid var(--border-subtle); border-radius:var(--radius-sm); padding:10px 14px;">
          <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
            <span class="badge badge-accent">${window.NallaApp.escapeHtml(ev.category || "GENERAL")}</span>
            <span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-dim);">${ev.time}</span>
          </div>
          <div style="font-size:0.9rem; color:var(--text-bright);">${window.NallaApp.escapeHtml(ev.description || "")}</div>
          ${ev.detail ? `<div style="font-family:var(--font-mono); font-size:0.78rem; color:var(--text-dim); margin-top:4px;">${window.NallaApp.escapeHtml(ev.detail)}</div>` : ""}
        </div>
      </div>
    `).join("");
  }

  window.NallaPages.activity = {
    init: initActivity,
    onActivate: loadActivity,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initActivity);
  } else {
    initActivity();
  }
})();
