"""app/core/notes.py

Markdown notes manager for Prompt 3A & 3C.
Stores notes cleanly in data/notes/ as human-readable Markdown files.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("nalla.notes")

NOTES_DIR = Path("data") / "notes"


def _slugify(title: str) -> str:
    """Create filesystem-safe filename slug from note title."""
    cleaned = re.sub(r"[^\w\s-]", "", title.lower()).strip()
    slug = re.sub(r"[-\s]+", "_", cleaned)
    return slug or "untitled_note"


def ensure_notes_dir() -> Path:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return NOTES_DIR


def create_note(title: str, content: str) -> dict[str, Any]:
    """Create or overwrite a markdown note in data/notes/."""
    d = ensure_notes_dir()
    slug = _slugify(title)
    filename = f"{slug}.md"
    file_path = d / filename

    now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
    frontmatter = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"created_at: \"{now_iso}\"\n"
        f"---\n\n"
    )
    full_text = f"{frontmatter}# {title}\n\n{content}\n"
    file_path.write_text(full_text, encoding="utf-8")
    logger.info("Created note '%s' at %s", title, file_path)
    return {
        "title": title,
        "filename": filename,
        "path": str(file_path),
        "created_at": now_iso,
    }


def append_note(title: str, content: str) -> dict[str, Any]:
    """Append text to an existing note or create it if not present."""
    d = ensure_notes_dir()
    slug = _slugify(title)
    file_path = d / f"{slug}.md"

    if not file_path.exists():
        return create_note(title, content)

    now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
    append_block = f"\n\n## Update ({now_iso})\n{content}\n"
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(append_block)
    logger.info("Appended to note '%s' at %s", title, file_path)
    return {
        "title": title,
        "filename": file_path.name,
        "path": str(file_path),
        "updated_at": now_iso,
    }


def read_note(title: str) -> str | None:
    """Read full markdown text of note by title or slug."""
    d = ensure_notes_dir()
    slug = _slugify(title)
    for p in [d / f"{slug}.md", d / f"{title}.md"]:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


def list_notes() -> list[dict[str, Any]]:
    """List all notes in data/notes/."""
    d = ensure_notes_dir()
    notes = []
    for p in sorted(d.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        notes.append({
            "title": p.stem.replace("_", " ").title(),
            "filename": p.name,
            "path": str(p),
            "size_bytes": p.stat().st_size,
            "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)),
        })
    return notes
