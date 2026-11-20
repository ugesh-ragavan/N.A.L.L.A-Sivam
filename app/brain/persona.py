"""app/brain/persona.py

Persona loader and system-prompt builder.

Reads ``persona/nalla_master.md`` and builds the full system prompt
that is prepended to every LLM call.  The file is hot-reloaded
automatically whenever its modification time changes, so you can edit
the persona without restarting the app.

The final prompt appended on every call:
  • The raw persona markdown text.
  • Current date and time.
  • List of currently connected tools (from ToolRegistry.summary()).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nalla.persona")

# Default location relative to the project root (BASE_DIR).
_DEFAULT_PERSONA_PATH = Path(__file__).resolve().parent.parent.parent / "persona" / "nalla_master.md"


class Persona:
    """Loads and hot-reloads the master persona markdown file.

    Parameters
    ----------
    path: Path to the persona markdown file.  Defaults to
          ``persona/nalla_master.md`` relative to the project root.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = Path(path) if path else _DEFAULT_PERSONA_PATH
        self._cached_text: str = ""
        self._cached_mtime: float = 0.0
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_system_prompt(self, tool_summary: str = "(no tools connected)") -> str:
        """Return the full system prompt for one LLM call.

        Hot-reloads the persona file if it has been modified on disk.

        Parameters
        ----------
        tool_summary: One-line-per-tool description from ToolRegistry.summary().
        """
        self._reload_if_changed()
        now = datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")
        return (
            f"{self._cached_text}\n\n"
            f"---\n"
            f"Current date/time: {now}\n\n"
            f"Currently connected tools:\n{tool_summary}\n"
        )

    @property
    def path(self) -> Path:
        """Absolute path to the persona file being watched."""
        return self._path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load the persona file from disk and update the cache."""
        if not self._path.exists():
            logger.warning(
                "Persona file not found at %s — using empty persona.", self._path
            )
            self._cached_text = ""
            self._cached_mtime = 0.0
            return
        try:
            self._cached_text = self._path.read_text(encoding="utf-8").strip()
            self._cached_mtime = self._path.stat().st_mtime
            logger.debug("Persona loaded from %s", self._path)
        except OSError as exc:
            logger.error("Failed to read persona file: %s", exc)

    def _reload_if_changed(self) -> None:
        """Check the file's mtime and reload if it has changed."""
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if mtime != self._cached_mtime:
            logger.info("Persona file changed — hot-reloading %s", self._path)
            self._load()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

persona = Persona()
