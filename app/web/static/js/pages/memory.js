/**
 * app/web/static/js/pages/memory.js
 *
 * Durable Memory Controller (6E Page 1):
 *   • Card grid of memories with FTS5 search.
 *   • Category & Importance filters.
 *   • Inline edit and delete with confirmation.
 *   • Add memory form with category and importance.
 *   • JSON backup export.
 *   • Panel showing which memories were used in the last reply.
 */

(function () {
  "use strict";

  window.NallaPages = window.NallaPages || {};

  let currentMemories = [];

  function initMemory() {
    const searchInput = document.getElementById("mem-search-input");
    const catFilter = document.getElementById("mem-filter-cat");
    const impFilter = document.getElementById("mem-filter-imp");
    const addForm = document.getElementById("mem-add-form");
    const refreshLastBtn = document.getElementById("mem-refresh-last-btn");

    if (searchInput) searchInput.addEventListener("input", () => loadMemories());
    if (catFilter) catFilter.addEventListener("change", () => loadMemories());
    if (impFilter) impFilter.addEventListener("change", () => loadMemories());

    if (addForm) {
      addForm.addEventListener("submit", (e) => {
        e.preventDefault();
        handleAddMemory();
      });
    }

    if (refreshLastBtn) {
      refreshLastBtn.addEventListener("click", loadLastUsedMemories);
    }

    // Tasks form handler
    const taskAddForm = document.getElementById("task-add-form");
    if (taskAddForm) {
      taskAddForm.addEventListener("submit", (e) => {
        e.preventDefault();
        handleAddTask();
      });
    }

    loadMemories();
    loadLastUsedMemories();
    loadTasks();
    loadEntities();
    loadCandidates();
    loadJournals();
    loadWeeklyReview();

    // Attach entities form
    const entityForm = document.getElementById("entity-add-form");
    if (entityForm) {
      entityForm.addEventListener("submit", (e) => {
        e.preventDefault();
        handleAddEntity();
      });
    }

    // Attach search / filter for entities
    const entSearch = document.getElementById("entity-search-input");
    const entFilter = document.getElementById("entity-filter-type");
    if (entSearch) entSearch.addEventListener("input", loadEntities);
    if (entFilter) entFilter.addEventListener("change", loadEntities);

    // Refresh buttons
    const inboxRefreshBtn = document.getElementById("inbox-refresh-btn");
    if (inboxRefreshBtn) inboxRefreshBtn.addEventListener("click", loadCandidates);

    const reviewRefreshBtn = document.getElementById("review-refresh-btn");
    if (reviewRefreshBtn) reviewRefreshBtn.addEventListener("click", loadWeeklyReview);

    const genJournalBtn = document.getElementById("journal-generate-today-btn");
    if (genJournalBtn) genJournalBtn.addEventListener("click", handleGenerateJournal);
  }

  // ------------------------------------------------------------------------
  // Memory 2.0: Entities & People Management
  // ------------------------------------------------------------------------
  async function loadEntities() {
    const container = document.getElementById("entity-cards-grid");
    if (!container) return;

    const searchInput = document.getElementById("entity-search-input");
    const filterType = document.getElementById("entity-filter-type");
    const query = searchInput ? searchInput.value.trim().toLowerCase() : "";
    const typeVal = filterType ? filterType.value : "";

    try {
      const url = typeVal ? `/api/memory/entities?type=${encodeURIComponent(typeVal)}` : "/api/memory/entities";
      const res = await fetch(url);
      let entities = await res.json();

      if (query) {
        entities = entities.filter((e) => e.name.toLowerCase().includes(query) || (e.summary && e.summary.toLowerCase().includes(query)));
      }

      const countEl = document.getElementById("entity-total-count");
      if (countEl) countEl.textContent = `${entities.length} entit${entities.length === 1 ? "y" : "ies"}`;

      if (!entities || entities.length === 0) {
        container.innerHTML = `
          <div class="empty-state" style="grid-column:1/-1;">
            <div class="empty-state-icon">👥</div>
            <div class="empty-state-text">No entities tracked yet. Mention people, projects, or places in conversation!</div>
          </div>
        `;
        return;
      }

      const typeBadges = {
        person: "badge-accent",
        project: "badge-green",
        place: "badge-amber",
        org: "badge-red",
        topic: "badge-outline",
      };

      container.innerHTML = entities.map((ent) => `
        <div class="hud-card mem-card">
          <div class="mem-card-header">
            <span class="badge ${typeBadges[ent.type] || 'badge-accent'}">${ent.type.toUpperCase()}</span>
            <span class="mem-card-id">#${ent.id}</span>
          </div>
          <div style="font-weight:700; color:var(--text-bright); font-size:1.05rem; margin-top:6px;">
            ${window.NallaApp.escapeHtml(ent.name)}
          </div>
          <div class="mem-card-text" style="margin-top:6px; font-size:0.85rem; color:var(--text-dim);">
            ${ent.summary ? window.NallaApp.escapeHtml(ent.summary) : '<span style="font-style:italic;">No summary added yet.</span>'}
          </div>
          <div class="mem-card-footer" style="margin-top:12px; display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:0.72rem; color:var(--text-dim);">Last seen: ${ent.last_mentioned.slice(0, 10)}</span>
            <button class="btn btn-xs btn-outline btn-red" onclick="window.NallaPages.memory.deleteEntity(${ent.id})">Delete</button>
          </div>
        </div>
      `).join("");
    } catch (e) {
      container.innerHTML = `<div style="color:var(--red); padding:20px; grid-column:1/-1;">Error loading entities.</div>`;
    }
  }

  async function handleAddEntity() {
    const nameInput = document.getElementById("entity-new-name");
    const typeInput = document.getElementById("entity-new-type");
    const sumInput = document.getElementById("entity-new-summary");
    if (!nameInput || !nameInput.value.trim()) return;

    try {
      const res = await fetch("/api/memory/entities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: nameInput.value.trim(),
          type: typeInput ? typeInput.value : "topic",
          summary: sumInput ? sumInput.value.trim() : "",
        }),
      });
      if (res.ok) {
        nameInput.value = "";
        if (sumInput) sumInput.value = "";
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Entity saved.", "success");
        }
        loadEntities();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Error adding entity.", "alert");
    }
  }

  async function deleteEntity(id) {
    if (!confirm(`Delete entity #${id} and all facts linked to it?`)) return;
    try {
      const res = await fetch(`/api/memory/entities/${id}`, { method: "DELETE" });
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Entity deleted.", "info");
        loadEntities();
        loadMemories();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Delete failed.", "alert");
    }
  }

  // ------------------------------------------------------------------------
  // Memory 2.0: Candidate Inbox Management
  // ------------------------------------------------------------------------
  async function loadCandidates() {
    const container = document.getElementById("inbox-cards-list");
    const badgeCount = document.getElementById("inbox-badge-count");
    if (!container) return;

    try {
      const res = await fetch("/api/memory/candidates?status=pending");
      const candidates = await res.json();

      if (badgeCount) {
        badgeCount.textContent = candidates.length;
        badgeCount.style.display = candidates.length > 0 ? "inline-block" : "none";
      }

      if (!candidates || candidates.length === 0) {
        container.innerHTML = `
          <div class="empty-state">
            <div class="empty-state-icon">📥</div>
            <div class="empty-state-text">Inbox is clear! No candidate facts awaiting approval.</div>
          </div>
        `;
        return;
      }

      container.innerHTML = candidates.map((cand) => `
        <div class="hud-card" style="padding:14px 18px; border-left:3px solid var(--accent); background:rgba(0, 229, 255, 0.03);">
          <div style="display:flex; justify-content:space-between; align-items:flex-start;">
            <div>
              <div style="display:flex; align-items:center; gap:8px;">
                <strong style="color:var(--text-bright); font-size:0.95rem;">${window.NallaApp.escapeHtml(cand.entity_name)}</strong>
                <span class="badge badge-accent">${cand.entity_type}</span>
                <span style="font-size:0.75rem; color:var(--text-dim); font-family:var(--font-mono);">Source: ${cand.source}</span>
              </div>
              <div style="color:var(--text-bright); font-size:0.9rem; margin-top:6px;">
                ${window.NallaApp.escapeHtml(cand.text)}
              </div>
              ${cand.conflicts_with_fact_id ? `
                <div style="margin-top:6px; font-size:0.75rem; color:var(--amber); display:flex; align-items:center; gap:4px;">
                  ⚠️ <strong>Conflict Warning:</strong> Supersedes existing Fact #${cand.conflicts_with_fact_id} upon approval.
                </div>
              ` : ""}
            </div>
            <div style="display:flex; gap:8px;">
              <button class="btn btn-sm btn-primary" onclick="window.NallaPages.memory.approveCandidate(${cand.id})">✓ Approve</button>
              <button class="btn btn-sm btn-outline btn-red" onclick="window.NallaPages.memory.rejectCandidate(${cand.id})">✕ Reject</button>
            </div>
          </div>
        </div>
      `).join("");
    } catch (e) {
      container.innerHTML = `<div style="color:var(--red); padding:20px;">Error loading candidate inbox.</div>`;
    }
  }

  async function approveCandidate(id) {
    try {
      const res = await fetch(`/api/memory/candidates/${id}/approve`, { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast(data.message || "Candidate approved!", "success");
        loadCandidates();
        loadMemories();
        loadEntities();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Approval error.", "alert");
    }
  }

  async function rejectCandidate(id) {
    try {
      const res = await fetch(`/api/memory/candidates/${id}/reject`, { method: "POST" });
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Candidate dismissed.", "info");
        loadCandidates();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Reject error.", "alert");
    }
  }

  // ------------------------------------------------------------------------
  // Memory 2.0: Daily Journal Management
  // ------------------------------------------------------------------------
  async function loadJournals() {
    const container = document.getElementById("journal-entries-list");
    if (!container) return;

    try {
      const res = await fetch("/api/memory/journals?limit=15");
      const journals = await res.json();

      if (!journals || journals.length === 0) {
        container.innerHTML = `
          <div class="empty-state">
            <div class="empty-state-icon">📔</div>
            <div class="empty-state-text">No daily journals recorded yet. Click 'Generate Today's Journal' above!</div>
          </div>
        `;
        return;
      }

      container.innerHTML = journals.map((j) => `
        <div class="hud-card" style="padding:16px 20px;">
          <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border-subtle); padding-bottom:8px; margin-bottom:10px;">
            <strong style="color:var(--accent); font-size:1.0rem;">📅 ${j.date}</strong>
            <button class="btn btn-xs btn-outline btn-red" onclick="window.NallaPages.memory.deleteJournal('${j.date}')">Delete</button>
          </div>
          <div style="white-space:pre-wrap; line-height:1.5; font-size:0.88rem; color:var(--text-bright);">
            ${window.NallaApp.escapeHtml(j.content)}
          </div>
        </div>
      `).join("");
    } catch (e) {
      container.innerHTML = `<div style="color:var(--red); padding:20px;">Error loading journals.</div>`;
    }
  }

  async function handleGenerateJournal() {
    try {
      const res = await fetch("/api/memory/journals/generate", { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Daily journal generated!", "success");
        loadJournals();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Journal generation failed.", "alert");
    }
  }

  async function deleteJournal(dateStr) {
    if (!confirm(`Delete journal entry for ${dateStr}?`)) return;
    try {
      const res = await fetch(`/api/memory/journals/${dateStr}`, { method: "DELETE" });
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast(`Journal for ${dateStr} deleted.`, "info");
        loadJournals();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Delete journal error.", "alert");
    }
  }

  // ------------------------------------------------------------------------
  // Memory 2.0: Weekly Review ("Still True?" flow)
  // ------------------------------------------------------------------------
  async function loadWeeklyReview() {
    const container = document.getElementById("review-facts-list");
    if (!container) return;

    try {
      const res = await fetch("/api/memory/review/unconfirmed?days=30");
      const facts = await res.json();

      if (!facts || facts.length === 0) {
        container.innerHTML = `
          <div class="empty-state">
            <div class="empty-state-icon">✨</div>
            <div class="empty-state-text">All active facts are fresh and confirmed within the last 30 days!</div>
          </div>
        `;
        return;
      }

      container.innerHTML = facts.map((f) => `
        <div class="hud-card" style="padding:14px 18px; border-left:3px solid var(--amber);">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div style="display:flex; align-items:center; gap:8px;">
                <strong style="color:var(--text-bright);">${window.NallaApp.escapeHtml(f.entity_name)}</strong>
                <span class="badge badge-outline">${f.entity_type}</span>
                <span style="font-size:0.75rem; color:var(--text-dim);">Last confirmed: ${f.last_confirmed_at.slice(0, 10)}</span>
              </div>
              <div style="color:var(--text-bright); font-size:0.9rem; margin-top:6px;">
                ${window.NallaApp.escapeHtml(f.text)}
              </div>
            </div>
            <div style="display:flex; gap:8px;">
              <button class="btn btn-sm btn-primary" onclick="window.NallaPages.memory.confirmFact(${f.id})">👍 Still True (Keep)</button>
              <button class="btn btn-sm btn-outline btn-red" onclick="window.NallaPages.memory.deleteFactFromReview(${f.id})">✕ Forget</button>
            </div>
          </div>
        </div>
      `).join("");
    } catch (e) {
      container.innerHTML = `<div style="color:var(--red); padding:20px;">Error checking stale facts.</div>`;
    }
  }

  async function confirmFact(id) {
    try {
      const res = await fetch(`/api/memory/facts/${id}/confirm`, { method: "POST" });
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Fact confirmed as still true!", "success");
        loadWeeklyReview();
        loadMemories();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Confirm error.", "alert");
    }
  }

  async function deleteFactFromReview(id) {
    try {
      const res = await fetch(`/api/memory/facts/${id}`, { method: "DELETE" });
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Fact removed from memory.", "info");
        loadWeeklyReview();
        loadMemories();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) window.NallaApp.showToast("Delete error.", "alert");
    }
  }

  // ------------------------------------------------------------------------
  // Tasks Tab Management
  // ------------------------------------------------------------------------
  async function loadTasks() {
    const container = document.getElementById("task-list-container");
    if (!container) return;

    try {
      const res = await fetch("/api/tasks");
      const tasks = await res.json();

      if (!tasks || tasks.length === 0) {
        container.innerHTML = `
          <div class="empty-state">
            <div class="empty-state-icon">📋</div>
            <div class="empty-state-text">No open tasks right now. All caught up, boss!</div>
          </div>
        `;
        return;
      }

      container.innerHTML = tasks.map((t) => {
        const pColors = { 1: "var(--red)", 2: "var(--amber)", 3: "var(--cyan)" };
        const pNames = { 1: "P1 (High)", 2: "P2 (Med)", 3: "P3 (Low)" };
        const color = pColors[t.priority] || "var(--text-dim)";
        const pName = pNames[t.priority] || `P${t.priority}`;
        const dueText = t.due_at ? `Due: ${t.due_at.slice(0, 10)}` : "";
        const proText = t.project ? `[${t.project}]` : "";

        return `
          <div class="hud-card hud-bracket" style="padding:12px 16px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div style="font-weight:600; color:var(--text-bright); font-size:0.92rem;">
                ${window.NallaApp.escapeHtml(t.title)}
              </div>
              <div style="font-size:0.75rem; color:var(--text-dim); margin-top:4px; display:flex; gap:10px;">
                <span style="color:${color}; font-weight:600;">${pName}</span>
                ${dueText ? `<span>${dueText}</span>` : ""}
                ${proText ? `<span style="color:var(--accent);">${window.NallaApp.escapeHtml(proText)}</span>` : ""}
              </div>
            </div>
            <div style="display:flex; gap:6px; align-items:center;">
              <select style="font-size:0.75rem; padding:2px 6px;" onchange="window.NallaPages.memory.changeTaskPriority(${t.id}, this.value)">
                <option value="1" ${t.priority === 1 ? "selected" : ""}>P1</option>
                <option value="2" ${t.priority === 2 ? "selected" : ""}>P2</option>
                <option value="3" ${t.priority === 3 ? "selected" : ""}>P3</option>
              </select>
              <button class="btn btn-sm btn-green" onclick="window.NallaPages.memory.completeTask(${t.id})">✓ Done</button>
              <button class="btn btn-sm btn-red" onclick="window.NallaPages.memory.deleteTask(${t.id})">✕</button>
            </div>
          </div>
        `;
      }).join("");
    } catch (e) {
      container.innerHTML = `<div style="color:var(--red); padding:16px;">Failed loading tasks.</div>`;
    }
  }

  async function handleAddTask() {
    const titleInp = document.getElementById("task-title-input");
    const prioInp = document.getElementById("task-priority-input");
    const dueInp = document.getElementById("task-due-input");
    const projInp = document.getElementById("task-proj-input");

    const title = titleInp?.value.trim();
    if (!title) return;

    try {
      const res = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title,
          priority: parseInt(prioInp?.value || "2", 10),
          due_at: dueInp?.value || null,
          project: projInp?.value.trim() || null,
        }),
      });
      if (res.ok) {
        if (titleInp) titleInp.value = "";
        if (dueInp) dueInp.value = "";
        if (projInp) projInp.value = "";
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Task added.", "success");
        }
        loadTasks();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) {
        window.NallaApp.showToast("Failed adding task.", "alert");
      }
    }
  }

  async function completeTask(id) {
    try {
      const res = await fetch(`/api/tasks/${id}/done`, { method: "PATCH" });
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Task marked completed!", "success");
        }
        loadTasks();
      }
    } catch (e) {
      // ignore
    }
  }

  async function changeTaskPriority(id, prio) {
    try {
      await fetch(`/api/tasks/${id}/priority`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority: parseInt(prio, 10) }),
      });
      loadTasks();
    } catch (e) {
      // ignore
    }
  }

  async function deleteTask(id) {
    try {
      await fetch(`/api/tasks/${id}`, { method: "DELETE" });
      loadTasks();
    } catch (e) {
      // ignore
    }
  }

  async function loadMemories() {
    const q = document.getElementById("mem-search-input")?.value.trim() || "";
    const cat = document.getElementById("mem-filter-cat")?.value || "";
    const imp = document.getElementById("mem-filter-imp")?.value || "";
    const grid = document.getElementById("mem-cards-grid");
    const countBadge = document.getElementById("mem-total-count");

    let url = `/api/memories?q=${encodeURIComponent(q)}&category=${encodeURIComponent(cat)}`;
    if (imp) {
      url += `&importance=${encodeURIComponent(imp)}`;
    }

    try {
      const res = await fetch(url);
      const data = await res.json();
      currentMemories = data;

      if (countBadge) countBadge.innerText = `${data.length} memories`;

      if (!grid) return;
      if (data.length === 0) {
        grid.innerHTML = `
          <div class="empty-state" style="grid-column: 1/-1;">
            <div class="empty-state-icon">🧠</div>
            <div class="empty-state-text">No durable memories found matching current search and filters.</div>
          </div>
        `;
        return;
      }

      grid.innerHTML = data.map((m) => {
        const catClass = getCategoryBadgeClass(m.category);
        const impColor = getImportanceColor(m.importance);
        return `
          <div class="hud-card hud-bracket" id="mem-card-${m.id}" style="padding:14px; display:flex; flex-direction:column; justify-content:space-between;">
            <div>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span class="badge ${catClass}">${window.NallaApp.escapeHtml(m.category || 'fact')}</span>
                <span style="font-family:var(--font-mono); font-size:0.75rem; color:${impColor}; font-weight:600;">
                  ★ Imp: ${m.importance}/5
                </span>
              </div>
              <div id="mem-text-${m.id}" style="font-size:0.88rem; line-height:1.45; margin-bottom:12px; color:var(--text-bright);">
                ${window.NallaApp.escapeHtml(m.text)}
              </div>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid var(--border-subtle); padding-top:8px; font-size:0.75rem; color:var(--text-dim);">
              <span style="font-family:var(--font-mono);">#${m.id}</span>
              <div style="display:flex; gap:6px;">
                <button class="btn btn-sm btn-outline" onclick="window.NallaPages.memory.startEdit(${m.id})">Edit</button>
                <button class="btn btn-sm btn-red" onclick="window.NallaPages.memory.deleteMemory(${m.id})">Del</button>
              </div>
            </div>
          </div>
        `;
      }).join("");
    } catch (err) {
      console.error("Failed loading memories", err);
    }
  }

  function getCategoryBadgeClass(cat) {
    switch ((cat || "").toLowerCase()) {
      case "preference": return "badge-accent";
      case "fact": return "badge-green";
      case "project": return "badge-amber";
      case "person": return "badge-outline";
      case "task": return "badge-red";
      default: return "badge-accent";
    }
  }

  function getImportanceColor(imp) {
    if (imp >= 5) return "var(--red)";
    if (imp >= 4) return "var(--amber)";
    if (imp >= 3) return "var(--accent)";
    return "var(--text-dim)";
  }

  async function loadLastUsedMemories() {
    const listEl = document.getElementById("mem-last-used-list");
    if (!listEl) return;

    try {
      const res = await fetch("/api/memories/last-used");
      if (res.ok) {
        const data = await res.json();
        const memories = data.memories || [];
        if (memories.length === 0) {
          listEl.innerHTML = `<span style="color:var(--text-dim); font-size:0.8rem; font-style:italic;">No memories were injected into the last conversation turn.</span>`;
          return;
        }

        listEl.innerHTML = memories.map((m) => `
          <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border-subtle); border-radius:var(--radius-sm); padding:6px 10px; display:flex; justify-content:space-between; align-items:center;">
            <div style="font-size:0.83rem; color:var(--text-bright);">
              <span class="badge ${getCategoryBadgeClass(m.category)}" style="font-size:0.65rem; padding:1px 5px; margin-right:6px;">${window.NallaApp.escapeHtml(m.category || 'fact')}</span>
              ${window.NallaApp.escapeHtml(m.text || '')}
            </div>
            <span style="font-family:var(--font-mono); font-size:0.7rem; color:${getImportanceColor(m.importance)}; font-weight:600;">
              Imp: ${m.importance || 3}/5
            </span>
          </div>
        `).join("");
      }
    } catch (err) {
      console.warn("Could not load last used memories:", err);
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
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Memory saved to SQLite store!", "success");
        }
        loadMemories();
      } else {
        const err = await res.json();
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast(err.detail || "Error saving memory", "alert");
        }
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) {
        window.NallaApp.showToast("Network error saving memory.", "alert");
      }
    }
  }

  function startEdit(id) {
    const textDiv = document.getElementById(`mem-text-${id}`);
    if (!textDiv) return;
    const current = textDiv.innerText.trim();

    textDiv.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:6px;">
        <input type="text" id="mem-edit-input-${id}" value="${window.NallaApp.escapeHtml(current)}" style="width:100%; font-size:0.85rem; padding:4px 8px;">
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
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast(`Memory #${id} updated.`, "success");
        }
        loadMemories();
      } else {
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast("Update failed.", "alert");
        }
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) {
        window.NallaApp.showToast("Network error.", "alert");
      }
    }
  }

  async function deleteMemory(id) {
    if (!confirm(`Permanently remove memory #${id} from local SQLite store?`)) return;

    try {
      const res = await fetch(`/api/memories/${id}`, { method: "DELETE" });
      if (res.ok) {
        if (window.NallaApp && window.NallaApp.showToast) {
          window.NallaApp.showToast(`Memory #${id} permanently deleted.`, "info");
        }
        loadMemories();
      }
    } catch (e) {
      if (window.NallaApp && window.NallaApp.showToast) {
        window.NallaApp.showToast("Error deleting memory.", "alert");
      }
    }
  }

  window.NallaPages.memory = {
    init: initMemory,
    load: loadMemories,
    loadTasks: loadTasks,
    completeTask,
    changeTaskPriority,
    deleteTask,
    loadEntities,
    deleteEntity,
    loadCandidates,
    approveCandidate,
    rejectCandidate,
    loadJournals,
    deleteJournal,
    loadWeeklyReview,
    confirmFact,
    deleteFactFromReview,
    onActivate: () => {
      loadMemories();
      loadLastUsedMemories();
      loadTasks();
      loadEntities();
      loadCandidates();
      loadJournals();
      loadWeeklyReview();
    },
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
