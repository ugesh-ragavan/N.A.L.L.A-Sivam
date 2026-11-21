/**
 * app/web/static/js/pages/memory.js
 *
 * Durable Memory Controller:
 *   • Grid of memory cards with FTS5 instant search.
 *   • Category & importance level filters.
 *   • Inline editing and permanent delete.
 *   • Add new memory form with classification.
 *   • JSON backup export.
 *   • "What Nalla used in the last reply" panel.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  let currentMemories = [];

  function initMemory() {
    const searchInput = document.getElementById("mem-search-input");
    const catFilter = document.getElementById("mem-filter-cat");
    const addForm = document.getElementById("mem-add-form");

    if (searchInput) searchInput.addEventListener("input", () => loadMemories());
    if (catFilter) catFilter.addEventListener("change", () => loadMemories());

    if (addForm) {
      addForm.addEventListener("submit", (e) => {
        e.preventDefault();
        handleAddMemory();
      });
    }

    loadMemories();
  }

  async function loadMemories() {
    const q = document.getElementById("mem-search-input")?.value || "";
    const cat = document.getElementById("mem-filter-cat")?.value || "";
    const grid = document.getElementById("mem-cards-grid");
    const countBadge = document.getElementById("mem-total-count");

    const url = `/api/memories?q=${encodeURIComponent(q)}&category=${encodeURIComponent(cat)}`;
    try {
      const res = await fetch(url);
      const data = await res.json();
      currentMemories = data;

      if (countBadge) countBadge.innerText = `${data.length} memories`;

      if (!grid) return;
      if (data.length === 0) {
        grid.innerHTML = `<div style="grid-column: 1/-1; text-align:center; color:var(--text-dim); padding:40px;">No memories found.</div>`;
        return;
      }

      grid.innerHTML = data.map((m) => `
        <div class="hud-card hud-bracket" id="mem-card-${m.id}" style="padding:16px;">
          <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
            <span class="badge badge-accent">${window.NallaApp.escapeHtml(m.category)}</span>
            <span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-dim);">Imp: ${m.importance}/5</span>
          </div>
          <div id="mem-text-${m.id}" style="font-size:0.9rem; line-height:1.5; margin-bottom:12px; color:var(--text-bright);">
            ${window.NallaApp.escapeHtml(m.text)}
          </div>
          <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid var(--border-subtle); padding-top:8px; font-size:0.75rem; color:var(--text-dim);">
            <span>#${m.id}</span>
            <div style="display:flex; gap:6px;">
              <button class="btn btn-sm btn-outline" onclick="window.NallaPages.memory.startEdit(${m.id})">Edit</button>
              <button class="btn btn-sm btn-red" onclick="window.NallaPages.memory.deleteMemory(${m.id})">Del</button>
            </div>
          </div>
        </div>
      `).join("");
    } catch (err) {
      console.error("Failed loading memories", err);
    }
  }

  async function handleAddMemory() {
    const textEl = document.getElementById("mem-new-text");
    const catEl = document.getElementById("mem-new-cat");
    const impEl = document.getElementById("mem-new-imp");

    const text = textEl ? textEl.value.trim() : "";
    const category = catEl ? catEl.value : "fact";
    const importance = parseInt(impEl ? impEl.value : "3", 10);

    if (!text) return;

    try {
      const res = await fetch("/api/memories", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, category, importance }),
      });
      if (res.ok) {
        textEl.value = "";
        window.NallaApp.showToast("Memory saved to SQLite!", "success");
        loadMemories();
      } else {
        const err = await res.json();
        window.NallaApp.showToast(err.detail || "Error saving memory", "alert");
      }
    } catch (e) {
      window.NallaApp.showToast("Network error saving memory.", "alert");
    }
  }

  function startEdit(id) {
    const textDiv = document.getElementById(`mem-text-${id}`);
    if (!textDiv) return;
    const current = textDiv.innerText;

    textDiv.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:6px;">
        <input type="text" id="mem-edit-input-${id}" value="${window.NallaApp.escapeHtml(current)}" style="width:100%;">
        <div style="display:flex; gap:6px; justify-content:flex-end;">
          <button class="btn btn-sm btn-green" onclick="window.NallaPages.memory.saveEdit(${id})">Save</button>
          <button class="btn btn-sm btn-outline" onclick="window.NallaPages.memory.load()">Cancel</button>
        </div>
      </div>
    `;
  }

  async function saveEdit(id) {
    const input = document.getElementById(`mem-edit-input-${id}`);
    const newText = input ? input.value.trim() : "";
    if (!newText) return;

    try {
      const res = await fetch(`/api/memories/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: newText }),
      });
      if (res.ok) {
        window.NallaApp.showToast(`Memory #${id} updated.`, "success");
        loadMemories();
      } else {
        window.NallaApp.showToast("Update failed.", "alert");
      }
    } catch (e) {
      window.NallaApp.showToast("Network error.", "alert");
    }
  }

  async function deleteMemory(id) {
    if (!confirm(`Permanently remove memory #${id} from local SQLite store?`)) return;
    try {
      const res = await fetch(`/api/memories/${id}`, { method: "DELETE" });
      if (res.ok) {
        window.NallaApp.showToast(`Memory #${id} permanently deleted.`, "info");
        loadMemories();
      }
    } catch (e) {
      window.NallaApp.showToast("Error deleting memory.", "alert");
    }
  }

  window.NallaPages.memory = {
    init: initMemory,
    load: loadMemories,
    onActivate: loadMemories,
    startEdit,
    saveEdit,
    deleteMemory,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initMemory);
  } else {
    initMemory();
  }
})();
