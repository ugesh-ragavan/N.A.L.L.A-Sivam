"""app/memory/store.py

Memory store with FTS5 search, hard deletes, sensitive data guardrails,
conversation persistence, and reminder management.

Rules:
  • Hard delete: delete_memory really removes the row from SQLite and FTS5.
  • Sensitive data: NEVER stores passwords, credit cards, or ID numbers.
  • Search ranking: FTS5 rank ordered results with fallback.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.memory.db import get_connection, get_db_path, init_db

logger = logging.getLogger("nalla.memory.store")

VALID_CATEGORIES = {"preference", "fact", "project", "person", "task"}
VALID_SOURCES = {"user_said", "auto_suggested"}

# Regex patterns to detect sensitive data that must never be stored.
_SENSITIVE_PATTERNS = [
    # 13 to 19 digits with optional hyphens/spaces (typical credit/debit card numbers)
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    # Social security number (US: 999-99-9999) or Aadhaar (12 digits)
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}\b"),
    # Passwords, API keys, credentials, secret tokens
    re.compile(r"(?i)\b(?:password|passwd|pin|secret|api[_-]?key|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bmy (?:password|pin|secret|passcode) is\s+\S+"),
]

# Standard English stop words to avoid noise in natural language queries
STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't", "have",
    "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers",
    "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've",
    "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's", "me",
    "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "of", "off", "on",
    "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over",
    "own", "same", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't",
    "so", "some", "such", "than", "that", "that's", "the", "their", "theirs", "them",
    "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll",
    "they're", "they've", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves"
}


def is_sensitive(text: str) -> bool:
    """Return True if text appears to contain passwords, cards, or ID numbers."""
    return any(p.search(text) for p in _SENSITIVE_PATTERNS)


def sanitize_fts_query(query: str) -> str:
    """Sanitize raw text for safe SQLite FTS5 MATCH expressions using OR ranking."""
    raw_tokens = re.findall(r"\w+", query)
    if not raw_tokens:
        return ""

    # Filter out stopwords if meaningful content words exist
    content_tokens = [t for t in raw_tokens if t.lower() not in STOP_WORDS]
    tokens = content_tokens if content_tokens else raw_tokens

    # Use OR to allow ranking by matching term count
    return " OR ".join(f'"{token}"*' for token in tokens)


class MemoryStore:
    """High-level interface to SQLite memory, messages, and reminders."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = db_path if db_path is not None else get_db_path()
        init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    # ------------------------------------------------------------------
    # Memories
    # ------------------------------------------------------------------

    def save_memory(
        self,
        text: str,
        category: str = "fact",
        importance: int = 3,
        source: str = "user_said",
    ) -> int:
        """Store a new memory.

        Raises
        ------
        ValueError: If text contains sensitive data, or if category /
                    importance / source are invalid.
        """
        text = text.strip()
        if not text:
            raise ValueError("Memory text cannot be empty.")

        if is_sensitive(text):
            raise ValueError(
                "Sensitive information (passwords, card or ID numbers) "
                "cannot be stored in memory."
            )

        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{category}'. Must be one of: {sorted(VALID_CATEGORIES)}"
            )

        importance = max(1, min(5, int(importance)))

        if source not in VALID_SOURCES:
            raise ValueError(
                f"Invalid source '{source}'. Must be one of: {sorted(VALID_SOURCES)}"
            )

        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO memories (text, category, importance, source, created_at, updated_at, last_used_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (text, category, importance, source, now, now, None),
                )
                memory_id = cursor.lastrowid
                logger.info("Saved memory #%d (%s, imp=%d)", memory_id, category, importance)
                return memory_id
        finally:
            conn.close()

    def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        """Fetch a single memory by ID or None."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_memory(
        self,
        memory_id: int,
        text: str | None = None,
        category: str | None = None,
        importance: int | None = None,
    ) -> bool:
        """Update fields of an existing memory. Returns True on success."""
        existing = self.get_memory(memory_id)
        if not existing:
            return False

        new_text = existing["text"]
        if text is not None:
            new_text = text.strip()
            if not new_text:
                raise ValueError("Memory text cannot be empty.")
            if is_sensitive(new_text):
                raise ValueError("Sensitive information cannot be stored.")

        new_cat = existing["category"]
        if category is not None:
            if category not in VALID_CATEGORIES:
                raise ValueError(f"Invalid category '{category}'")
            new_cat = category

        new_imp = existing["importance"]
        if importance is not None:
            new_imp = max(1, min(5, int(importance)))

        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE memories
                    SET text = ?, category = ?, importance = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_text, new_cat, new_imp, now, memory_id),
                )
            return True
        finally:
            conn.close()

    def delete_memory(self, memory_id: int) -> bool:
        """Hard delete a memory row. Really removes it from SQLite and FTS."""
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute(
                    "DELETE FROM memories WHERE id = ?", (memory_id,)
                )
                deleted = cursor.rowcount > 0
                if deleted:
                    logger.info("Permanently deleted memory #%d", memory_id)
                return deleted
        finally:
            conn.close()

    def search_memories(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search memories using FTS5 ranking with prefix support and fallback.

        Updates last_used_at for returned results.
        """
        clean_fts = sanitize_fts_query(query)
        conn = self._conn()
        results: list[dict[str, Any]] = []

        try:
            if clean_fts:
                try:
                    rows = conn.execute(
                        """
                        SELECT m.id, m.text, m.category, m.importance, m.source,
                               m.created_at, m.updated_at, m.last_used_at, fts.rank
                        FROM memories_fts fts
                        JOIN memories m ON fts.rowid = m.id
                        WHERE memories_fts MATCH ?
                        ORDER BY fts.rank, m.importance DESC
                        LIMIT ?
                        """,
                        (clean_fts, limit),
                    ).fetchall()
                    results = [dict(r) for r in rows]
                except sqlite3.OperationalError as exc:
                    logger.warning("FTS query '%s' failed: %s; falling back to LIKE", clean_fts, exc)

            # Fallback: if fewer than limit results, search for content words via LIKE
            if len(results) < limit:
                existing_ids = {r["id"] for r in results}
                tokens = [t for t in re.findall(r"\w+", query) if t.lower() not in STOP_WORDS and len(t) > 2]
                for token in tokens:
                    rows = conn.execute(
                        """
                        SELECT id, text, category, importance, source,
                               created_at, updated_at, last_used_at
                        FROM memories
                        WHERE text LIKE ?
                        ORDER BY importance DESC, id DESC
                        LIMIT ?
                        """,
                        (f"%{token}%", limit),
                    ).fetchall()
                    for r in rows:
                        if r["id"] not in existing_ids:
                            results.append(dict(r))
                            existing_ids.add(r["id"])
                            if len(results) >= limit:
                                break
                    if len(results) >= limit:
                        break

            # Update last_used_at for found memories
            if results:
                now = datetime.now(timezone.utc).isoformat()
                ids = [r["id"] for r in results]
                with conn:
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        f"UPDATE memories SET last_used_at = ? WHERE id IN ({placeholders})",
                        [now] + ids,
                    )
            return results
        finally:
            conn.close()

    def list_memories(
        self,
        category: str | None = None,
        min_importance: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List stored memories with optional filtering."""
        clauses = []
        params: list[Any] = []

        if category:
            clauses.append("category = ?")
            params.append(category)
        if min_importance is not None:
            clauses.append("importance >= ?")
            params.append(min_importance)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT id, text, category, importance, source, created_at, updated_at, last_used_at
            FROM memories
            {where}
            ORDER BY importance DESC, id DESC
            LIMIT ?
        """
        params.append(limit)

        conn = self._conn()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def export_memories_json(self) -> str:
        """Export all memories as a JSON string."""
        memories = self.list_memories(limit=10000)
        return json.dumps(memories, indent=2)

    # ------------------------------------------------------------------
    # Conversation History Persistence
    # ------------------------------------------------------------------

    def save_message(self, session_id: str, role: str, text: str) -> int:
        """Persist a conversation message turn."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO messages (session_id, role, text, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (session_id, role, text, now),
                )
                return cursor.lastrowid
        finally:
            conn.close()

    def get_messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve recent conversation messages in chronological order."""
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT id, session_id, role, text, created_at
                FROM (
                    SELECT * FROM messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------

    def save_reminder(self, text: str, due_at: str) -> int:
        """Save a new scheduled reminder."""
        text = text.strip()
        if not text:
            raise ValueError("Reminder text cannot be empty.")

        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO reminders (text, due_at, status, created_at)
                    VALUES (?, ?, 'pending', ?)
                    """,
                    (text, due_at, now),
                )
                reminder_id = cursor.lastrowid
                logger.info("Saved reminder #%d: '%s' due at %s", reminder_id, text, due_at)
                return reminder_id
        finally:
            conn.close()

    def get_due_reminders(self) -> list[dict[str, Any]]:
        """Return all pending reminders whose due_at is on or before now."""
        now_utc = datetime.now(timezone.utc)
        now_local = datetime.now()
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT id, text, due_at, status, created_at
                FROM reminders
                WHERE status = 'pending'
                ORDER BY due_at ASC
                """
            ).fetchall()
            due: list[dict[str, Any]] = []
            for r in rows:
                raw_due = r["due_at"]
                try:
                    dt = datetime.fromisoformat(raw_due)
                    if dt.tzinfo is None:
                        is_due = dt <= now_local
                    else:
                        is_due = dt <= now_utc
                except ValueError:
                    is_due = raw_due <= now_utc.isoformat()
                if is_due:
                    due.append(dict(r))
            return due
        finally:
            conn.close()

    def mark_reminder_done(self, reminder_id: int) -> bool:
        """Mark a pending reminder as done."""
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute(
                    "UPDATE reminders SET status = 'done' WHERE id = ?",
                    (reminder_id,),
                )
                return cursor.rowcount > 0
        finally:
            conn.close()

    def list_reminders(self, include_done: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        """List reminders, optionally including completed ones."""
        status_filter = "" if include_done else "WHERE status = 'pending'"
        query = f"""
            SELECT id, text, due_at, status, created_at
            FROM reminders
            {status_filter}
            ORDER BY due_at ASC
            LIMIT ?
        """
        conn = self._conn()
        try:
            rows = conn.execute(query, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Tasks Management
    # ------------------------------------------------------------------

    def add_task(
        self,
        title: str,
        priority: int = 2,
        due_at: str | None = None,
        project: str | None = None,
    ) -> int:
        """Add a new task. Priority: 1 (high), 2 (medium), 3 (low)."""
        title = title.strip()
        if not title:
            raise ValueError("Task title cannot be empty.")
        if priority not in (1, 2, 3):
            raise ValueError("Task priority must be 1 (high), 2 (medium), or 3 (low).")

        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO tasks (title, status, priority, due_at, project, created_at)
                    VALUES (?, 'open', ?, ?, ?, ?)
                    """,
                    (title, priority, due_at, project, now),
                )
                task_id = cursor.lastrowid
                logger.info("Added task #%d: '%s' (priority %d)", task_id, title, priority)
                return task_id
        finally:
            conn.close()

    def list_tasks(
        self,
        status: str = "open",
        project: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List tasks ordered by priority (1=high, 2=med, 3=low), then due_at, then created_at."""
        clauses = []
        params: list[Any] = []

        if status:
            clauses.append("status = ?")
            params.append(status)
        if project:
            clauses.append("project = ?")
            params.append(project)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT id, title, status, priority, due_at, project, created_at, completed_at
            FROM tasks
            {where}
            ORDER BY priority ASC,
                     CASE WHEN due_at IS NULL OR due_at = '' THEN 1 ELSE 0 END ASC,
                     due_at ASC,
                     created_at ASC
            LIMIT ?
        """
        params.append(limit)

        conn = self._conn()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def next_task(self) -> dict[str, Any] | None:
        """Get the single highest priority open task.

        Ordered by priority (1 before 2 before 3),
        then earliest due date (nulls last),
        then oldest created_at.
        """
        tasks = self.list_tasks(status="open", limit=1)
        return tasks[0] if tasks else None

    def complete_task(self, task_id: int) -> bool:
        """Mark a task as done with completed_at timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'done', completed_at = ?
                    WHERE id = ?
                    """,
                    (now, task_id),
                )
                return cursor.rowcount > 0
        finally:
            conn.close()

    def reprioritise_task(self, task_id: int, new_priority: int) -> bool:
        """Change task priority (1=high, 2=medium, 3=low)."""
        if new_priority not in (1, 2, 3):
            raise ValueError("Priority must be 1, 2, or 3.")
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute(
                    "UPDATE tasks SET priority = ? WHERE id = ?",
                    (new_priority, task_id),
                )
                return cursor.rowcount > 0
        finally:
            conn.close()

    def delete_task(self, task_id: int) -> bool:
        """Delete a task completely."""
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                return cursor.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Conversation Sessions & Background Summary
    # ------------------------------------------------------------------

    def get_session_summary(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve the persisted background summary for older exchanges in this session."""
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT session_id, summary, last_summarized_msg_id, updated_at
                FROM conversation_summaries
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def save_session_summary(self, session_id: str, summary: str, last_summarized_msg_id: int) -> None:
        """Persist or update summary of older conversation messages."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO conversation_summaries (session_id, summary, last_summarized_msg_id, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        summary = excluded.summary,
                        last_summarized_msg_id = excluded.last_summarized_msg_id,
                        updated_at = excluded.updated_at
                    """,
                    (session_id, summary, last_summarized_msg_id, now),
                )
        finally:
            conn.close()

    def get_last_message_timestamp(self, session_id: str) -> datetime | None:
        """Get the datetime of the last message in the session."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT created_at FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if not row or not row["created_at"]:
                return None
            try:
                dt = datetime.fromisoformat(row["created_at"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                return None
        finally:
            conn.close()

    def get_recent_exchanges(
        self,
        session_id: str,
        n_exchanges: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve up to n_exchanges (user+assistant pairs) verbatim in chronological order.

        1 exchange = 1 user message + 1 assistant message (up to 2*n_exchanges messages).
        """
        limit_messages = max(2, n_exchanges * 2)
        return self.get_messages(session_id, limit=limit_messages)

    def delete_last_exchange(self, session_id: str) -> int:
        """Remove the last exchange (assistant + user messages) from history."""
        conn = self._conn()
        try:
            with conn:
                rows = conn.execute(
                    "SELECT id FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 2",
                    (session_id,),
                ).fetchall()
                if not rows:
                    return 0
                ids = [r["id"] for r in rows]
                placeholders = ",".join("?" for _ in ids)
                cursor = conn.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    ids,
                )
                return cursor.rowcount
        finally:
            conn.close()

    def clear_short_term_memory(self, session_id: str) -> int:
        """Clear all messages for this session."""
        conn = self._conn()
        try:
            with conn:
                cursor = conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM conversation_summaries WHERE session_id = ?", (session_id,))
                return cursor.rowcount
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
store = MemoryStore()
