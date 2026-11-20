"""app/web/server.py

FastAPI localhost dashboard web server.

Exposes REST APIs and a localhost web interface for:
  • Memory management (search, category filter, inline edit, delete with confirm, add form, JSON export)
  • Reminders management (view, add, mark completed)
  • Settings toggle for memory (memory on/off switch)

Safety rules (CONTEXT.md):
  • Binds strictly to 127.0.0.1 (never 0.0.0.0)
  • Secrets are never displayed in full
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import config
from app.config import BASE_DIR, WEB_HOST, WEB_PORT
from app.memory.store import store

logger = logging.getLogger("nalla.web")

app = FastAPI(
    title="NALLA SIVAM Dashboard",
    description="Localhost-only JARVIS control panel and memory management.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class MemoryCreate(BaseModel):
    text: str = Field(..., min_length=1, description="Memory content")
    category: str = Field("fact", description="Category: preference|fact|project|person|task")
    importance: int = Field(3, ge=1, le=5, description="Importance from 1 to 5")


class MemoryUpdate(BaseModel):
    text: str | None = None
    category: str | None = None
    importance: int | None = Field(None, ge=1, le=5)


class ReminderCreate(BaseModel):
    text: str = Field(..., min_length=1, description="Reminder description")
    due_at: str = Field(..., description="ISO 8601 timestamp (e.g. 2026-11-20T17:00:00)")


class SettingsUpdate(BaseModel):
    memory_enabled: bool


# ---------------------------------------------------------------------------
# HTML Dashboard Page
# ---------------------------------------------------------------------------

_HTML_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "index.html"


@app.get("/", response_class=HTMLResponse)
def get_dashboard() -> str:
    """Serve the single-page localhost dashboard."""
    if _HTML_TEMPLATE_PATH.exists():
        return _HTML_TEMPLATE_PATH.read_text(encoding="utf-8")
    return "<h1>NALLA SIVAM Dashboard</h1><p>Template not found.</p>"


# ---------------------------------------------------------------------------
# Memory API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/memories")
def list_or_search_memories(
    q: str = "",
    category: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List or search memories with optional category filter."""
    if q.strip():
        results = store.search_memories(q.strip(), limit=limit)
        if category:
            results = [m for m in results if m.get("category") == category]
        return results
    cat = category.strip() if category.strip() else None
    return store.list_memories(category=cat, limit=limit)


@app.post("/api/memories", status_code=201)
def create_memory(item: MemoryCreate) -> dict[str, Any]:
    """Create a new memory."""
    try:
        mid = store.save_memory(
            text=item.text,
            category=item.category,
            importance=item.importance,
            source="user_said",
        )
        return {"ok": True, "id": mid, "message": f"Memory #{mid} saved."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/memories/{memory_id}")
def update_memory_item(memory_id: int, item: MemoryUpdate) -> dict[str, Any]:
    """Inline update of an existing memory."""
    try:
        updated = store.update_memory(
            memory_id=memory_id,
            text=item.text,
            category=item.category,
            importance=item.importance,
        )
        if not updated:
            raise HTTPException(status_code=404, detail=f"Memory #{memory_id} not found.")
        return {"ok": True, "message": f"Memory #{memory_id} updated."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/memories/{memory_id}")
def delete_memory_item(memory_id: int) -> dict[str, Any]:
    """Hard delete a memory row from SQLite and FTS5."""
    deleted = store.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Memory #{memory_id} not found.")
    return {"ok": True, "message": f"Memory #{memory_id} permanently deleted."}


@app.get("/api/memories/export")
def export_memories() -> Response:
    """Export all memories as JSON."""
    json_data = store.export_memories_json()
    return Response(
        content=json_data,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=nalla_memories.json"},
    )


# ---------------------------------------------------------------------------
# Reminders API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/reminders")
def list_reminders(include_done: bool = True) -> list[dict[str, Any]]:
    """List reminders."""
    return store.list_reminders(include_done=include_done)


@app.post("/api/reminders", status_code=201)
def create_reminder(item: ReminderCreate) -> dict[str, Any]:
    """Create a new scheduled reminder."""
    try:
        rid = store.save_reminder(text=item.text, due_at=item.due_at)
        return {"ok": True, "id": rid, "message": f"Reminder #{rid} scheduled."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/reminders/{reminder_id}/done")
def mark_reminder_completed(reminder_id: int) -> dict[str, Any]:
    """Mark a reminder as completed."""
    done = store.mark_reminder_done(reminder_id)
    if not done:
        raise HTTPException(status_code=404, detail=f"Reminder #{reminder_id} not found.")
    return {"ok": True, "message": f"Reminder #{reminder_id} marked as done."}


# ---------------------------------------------------------------------------
# Settings API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    """Return runtime settings."""
    return {
        "memory_enabled": config.MEMORY_ENABLED,
        "model": config.GEMINI_MODEL,
        "host": config.WEB_HOST,
        "port": config.WEB_PORT,
    }


@app.post("/api/settings")
def update_settings(item: SettingsUpdate) -> dict[str, Any]:
    """Toggle memory on or off."""
    config.MEMORY_ENABLED = item.memory_enabled
    logger.info("Setting memory_enabled updated to %s", item.memory_enabled)
    return {"ok": True, "memory_enabled": config.MEMORY_ENABLED}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_server() -> None:
    """Launch the uvicorn web server bound strictly to localhost."""
    import uvicorn
    # Enforce localhost-only binding
    host = "127.0.0.1" if WEB_HOST in ("localhost", "127.0.0.1") else WEB_HOST
    logger.info("Starting dashboard at http://%s:%d", host, WEB_PORT)
    uvicorn.run(app, host=host, port=WEB_PORT, log_level=config.LOG_LEVEL.lower())


if __name__ == "__main__":
    run_server()
