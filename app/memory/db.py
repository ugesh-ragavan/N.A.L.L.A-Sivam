"""app/memory/db.py

SQLite database manager, connection helper, and schema migrations.

Manages data/nalla.db with:
  • messages: Conversation history across restarts.
  • memories: User preferences, facts, projects, persons, tasks with FTS5 search.
  • reminders: Scheduled reminders with status tracking.
  • schema_migrations: Version tracking for schema migrations.

Enforces SQLite best practices:
  • PRAGMA foreign_keys = ON
  • PRAGMA journal_mode = WAL (file-based DB)
  • FTS5 with Porter stemmer
  • Thread-safe connections
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable

from app.config import BASE_DIR

logger = logging.getLogger("nalla.memory.db")

DEFAULT_DB_PATH = BASE_DIR / "data" / "nalla.db"


def get_db_path() -> Path:
    """Return the resolved database path, ensuring its parent directory exists."""
    path = DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create and return a configured sqlite3 connection.

    Row factory is set to sqlite3.Row for dict-like access.
    Foreign keys are enforced, and WAL mode is enabled for disk databases.
    """
    if db_path is None:
        db_path = get_db_path()
    elif isinstance(db_path, str) and db_path != ":memory:":
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        isolation_level=None,  # autocommit mode; transactions handled explicitly
    )
    conn.row_factory = sqlite3.Row

    with conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        if str(db_path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")

    return conn


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

def _migration_001_initial_schema(conn: sqlite3.Connection) -> None:
    """Migration 1: Base tables, FTS5 virtual table with Porter stemmer, and sync triggers."""
    with conn:
        # 1. messages table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, created_at);
        """)

        # 2. memories table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                category TEXT NOT NULL CHECK (
                    category IN ('preference', 'fact', 'project', 'person', 'task')
                ),
                importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
                source TEXT NOT NULL CHECK (source IN ('user_said', 'auto_suggested')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);
        """)

        # 3. reminders table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK (
                    status IN ('pending', 'done', 'cancelled')
                ),
                created_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reminders_status_due
            ON reminders(status, due_at);
        """)

        # 4. FTS5 virtual table for memories (with porter stemmer)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                text,
                content='memories',
                content_rowid='id',
                tokenize='porter unicode61'
            );
        """)

        # 5. Sync triggers between memories and memories_fts
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
                INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
            END;
        """)


def _migration_002_fts_porter_tokenizer(conn: sqlite3.Connection) -> None:
    """Migration 2: Ensure memories_fts uses porter stemmer."""
    with conn:
        conn.execute("DROP TRIGGER IF EXISTS memories_ai;")
        conn.execute("DROP TRIGGER IF EXISTS memories_ad;")
        conn.execute("DROP TRIGGER IF EXISTS memories_au;")
        conn.execute("DROP TABLE IF EXISTS memories_fts;")
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                text,
                content='memories',
                content_rowid='id',
                tokenize='porter unicode61'
            );
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
                INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
            END;
        """)
        conn.execute("INSERT INTO memories_fts(rowid, text) SELECT id, text FROM memories;")


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_initial_schema),
    (2, _migration_002_fts_porter_tokenizer),
]


def init_db(db_path: Path | str | None = None) -> None:
    """Run pending schema migrations against the target database."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
            """)

        applied_versions = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        for version, migration_fn in MIGRATIONS:
            if version not in applied_versions:
                logger.info("Applying database migration %03d...", version)
                migration_fn(conn)
                from datetime import datetime, timezone
                now_iso = datetime.now(timezone.utc).isoformat()
                with conn:
                    conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, now_iso),
                    )
                logger.info("Migration %03d applied successfully.", version)
    finally:
        conn.close()
