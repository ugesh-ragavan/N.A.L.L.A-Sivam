"""app/memory/memory_v2.py

Memory 2.0: Structured Entities, Facts, Candidate Inbox, Contradiction Detection,
Hybrid Retrieval, Daily Journal, and Weekly Review.

Identity Guard & Persona Alignment:
- ENTITIES: person | project | place | org | topic linked to facts.
- CANDIDATE INBOX: Facts proposed from conversation land in inbox first.
  Nothing becomes memory without explicit user confirmation.
- SECRETS REJECTION: Passwords, tokens, API keys, card numbers, Aadhaar/SSN,
  health or financial numbers are strictly blocked from being stored.
- CONTRADICTIONS: Conflicting facts flag the conflict and mark old facts 'superseded'.
- HYBRID RETRIEVAL: FTS5 match + importance + recency decay + recently-used boost.
- DAILY JOURNAL: Created locally from completed tasks, notes, and topics; stays local.
- WEEKLY REVIEW: 2-minute 'still true?' flow for facts unconfirmed > 30 days.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from app.memory.db import get_connection

logger = logging.getLogger("nalla.memory_v2")

# ---------------------------------------------------------------------------
# Secret & Sensitive Data Filter
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"\b(password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),  # Long token/hash-like string
    re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"),  # 16-digit card number
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"),  # 12-digit Aadhaar / ID
    re.compile(r"\b(cvv|cvc)\s*[:=]?\s*\d{3,4}\b", re.IGNORECASE),
]


def contains_secrets(text: str) -> bool:
    """Return True if text appears to contain passwords, credentials, or card numbers."""
    lower = text.lower()
    if any(k in lower for k in ("my password is", "my api key is", "my secret is", "credit card", "debit card")):
        return True
    return any(p.search(text) for p in _SECRET_PATTERNS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# MemoryManagerV2 Class
# ---------------------------------------------------------------------------

class MemoryManagerV2:
    """Core business logic for Memory 2.0."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def _conn(self):
        return get_connection(self._db_path)

    # ------------------------------------------------------------------
    # 1. ENTITIES
    # ------------------------------------------------------------------

    def get_or_create_entity(
        self,
        name: str,
        type: str = "topic",
        aliases: list[str] | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        """Fetch existing entity by name or create a new one."""
        name_clean = name.strip()
        type_clean = type.lower().strip()
        if type_clean not in ("person", "project", "place", "org", "topic"):
            type_clean = "topic"

        aliases_json = json.dumps(aliases or [])
        now = _now_iso()

        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE name = ? COLLATE NOCASE", (name_clean,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE entities SET last_mentioned = ? WHERE id = ?", (now, row["id"])
                )
                return dict(row)

            cur = conn.execute(
                """INSERT INTO entities (type, name, aliases, summary, created_at, last_mentioned)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (type_clean, name_clean, aliases_json, summary, now, now),
            )
            entity_id = cur.lastrowid
            created = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
            logger.info("Created entity #%d: '%s' (%s)", entity_id, name_clean, type_clean)
            return dict(created)

    def list_entities(self, type: str | None = None) -> list[dict[str, Any]]:
        """List entities, optionally filtered by type."""
        with self._conn() as conn:
            if type:
                rows = conn.execute(
                    "SELECT * FROM entities WHERE type = ? COLLATE NOCASE ORDER BY last_mentioned DESC",
                    (type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM entities ORDER BY last_mentioned DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_entity(self, entity_id_or_name: int | str) -> dict[str, Any] | None:
        """Fetch entity by ID or exact name."""
        with self._conn() as conn:
            if isinstance(entity_id_or_name, int) or str(entity_id_or_name).isdigit():
                row = conn.execute("SELECT * FROM entities WHERE id = ?", (int(entity_id_or_name),)).fetchone()
            else:
                row = conn.execute("SELECT * FROM entities WHERE name = ? COLLATE NOCASE", (str(entity_id_or_name).strip(),)).fetchone()
            return dict(row) if row else None

    def update_entity(
        self,
        entity_id: int,
        name: str | None = None,
        aliases: list[str] | None = None,
        summary: str | None = None,
        type: str | None = None,
    ) -> bool:
        """Update entity fields."""
        fields = []
        vals = []
        if name is not None:
            fields.append("name = ?")
            vals.append(name.strip())
        if aliases is not None:
            fields.append("aliases = ?")
            vals.append(json.dumps(aliases))
        if summary is not None:
            fields.append("summary = ?")
            vals.append(summary.strip())
        if type is not None and type in ("person", "project", "place", "org", "topic"):
            fields.append("type = ?")
            vals.append(type)

        if not fields:
            return False

        vals.append(entity_id)
        with self._conn() as conn:
            cur = conn.execute(f"UPDATE entities SET {', '.join(fields)} WHERE id = ?", vals)
            return cur.rowcount > 0

    def delete_entity(self, entity_id_or_name: int | str) -> bool:
        """Delete entity and all linked facts (cascaded)."""
        ent = self.get_entity(entity_id_or_name)
        if not ent:
            return False
        with self._conn() as conn:
            conn.execute("DELETE FROM entities WHERE id = ?", (ent["id"],))
            logger.info("Deleted entity '%s' (#%d) and all its facts.", ent["name"], ent["id"])
            return True

    # ------------------------------------------------------------------
    # 2. FACTS & HYBRID RETRIEVAL
    # ------------------------------------------------------------------

    def add_fact(
        self,
        entity_name: str,
        text: str,
        entity_type: str = "topic",
        source: str = "conversation",
        importance: int = 2,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Add a verified active fact linked to an entity."""
        if contains_secrets(text):
            raise ValueError("Fact contains secrets or sensitive credentials; rejected by Identity Guard.")

        ent = self.get_or_create_entity(name=entity_name, type=entity_type)
        now = _now_iso()

        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO facts (entity_id, text, source, confidence, importance, created_at, last_confirmed_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
                (ent["id"], text.strip(), source, confidence, max(1, min(5, importance)), now, now),
            )
            fact_id = cur.lastrowid
            fact = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
            logger.info("Saved Fact #%d for '%s': %s", fact_id, ent["name"], text[:50])
            res = dict(fact)
            res["entity_name"] = ent["name"]
            res["entity_type"] = ent["type"]
            return res

    def list_facts(
        self,
        entity_id: int | None = None,
        status: str = "active",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List facts, optionally filtered by entity and status."""
        with self._conn() as conn:
            query = """
                SELECT f.*, e.name AS entity_name, e.type AS entity_type
                FROM facts f
                JOIN entities e ON f.entity_id = e.id
                WHERE 1=1
            """
            params: list[Any] = []
            if entity_id is not None:
                query += " AND f.entity_id = ?"
                params.append(entity_id)
            if status != "all":
                query += " AND f.status = ?"
                params.append(status)
            query += " ORDER BY f.last_confirmed_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def update_fact(
        self,
        fact_id: int,
        text: str | None = None,
        importance: int | None = None,
        status: str | None = None,
    ) -> bool:
        """Update an existing fact."""
        fields = []
        vals = []
        if text is not None:
            if contains_secrets(text):
                raise ValueError("Fact update contains secrets; rejected.")
            fields.append("text = ?")
            vals.append(text.strip())
        if importance is not None:
            fields.append("importance = ?")
            vals.append(max(1, min(5, importance)))
        if status in ("active", "superseded", "deleted"):
            fields.append("status = ?")
            vals.append(status)

        if not fields:
            return False

        vals.append(fact_id)
        with self._conn() as conn:
            cur = conn.execute(f"UPDATE facts SET {', '.join(fields)} WHERE id = ?", vals)
            return cur.rowcount > 0

    def delete_fact(self, fact_id: int) -> bool:
        """Permanently delete a fact from facts and FTS."""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            return cur.rowcount > 0

    def search_facts_hybrid(
        self,
        query: str,
        limit: int = 5,
        touch_used: bool = True,
    ) -> list[dict[str, Any]]:
        """Hybrid ranking: FTS5 BM25 match + importance + recency decay + recently-used boost."""
        query_clean = query.strip()
        if not query_clean:
            return self.list_facts(status="active", limit=limit)

        tokens = [t for t in re.findall(r"\w+", query_clean) if t]
        if not tokens:
            return self.list_facts(status="active", limit=limit)

        fts_query = " OR ".join(f'"{t}"' for t in tokens)

        with self._conn() as conn:
            # 1. FTS search
            try:
                fts_rows = conn.execute(
                    """
                    SELECT f.id, f.entity_id, f.text, f.importance, f.confidence,
                           f.created_at, f.last_confirmed_at, f.last_used_at, f.status,
                           e.name AS entity_name, e.type AS entity_type,
                           bm25(facts_fts) AS bm25_rank
                    FROM facts_fts
                    JOIN facts f ON facts_fts.rowid = f.id
                    JOIN entities e ON f.entity_id = e.id
                    WHERE facts_fts MATCH ? AND f.status = 'active'
                    ORDER BY bm25_rank ASC
                    LIMIT 20
                    """,
                    (fts_query,),
                ).fetchall()
            except sqlite3.OperationalError:
                # Fallback to simple LIKE query if FTS expression encounters an issue
                like_pat = f"%{tokens[0]}%"
                fts_rows = conn.execute(
                    """
                    SELECT f.id, f.entity_id, f.text, f.importance, f.confidence,
                           f.created_at, f.last_confirmed_at, f.last_used_at, f.status,
                           e.name AS entity_name, e.type AS entity_type,
                           0.0 AS bm25_rank
                    FROM facts f
                    JOIN entities e ON f.entity_id = e.id
                    WHERE (f.text LIKE ? OR e.name LIKE ?) AND f.status = 'active'
                    LIMIT 20
                    """,
                    (like_pat, like_pat),
                ).fetchall()

            now_dt = datetime.now(timezone.utc)
            scored: list[tuple[float, dict[str, Any]]] = []

            for r in fts_rows:
                item = dict(r)
                # Parse last_confirmed_at for recency decay
                try:
                    conf_dt = datetime.fromisoformat(item["last_confirmed_at"])
                    if conf_dt.tzinfo is None:
                        conf_dt = conf_dt.replace(tzinfo=timezone.utc)
                    days_unconfirmed = max(0.0, (now_dt - conf_dt).total_seconds() / 86400.0)
                except Exception:
                    days_unconfirmed = 0.0

                # Untouched facts lose priority gradually over 30 days
                recency_score = math.exp(-days_unconfirmed / 30.0)

                # Recently-used boost (if used within last 24h)
                used_boost = 0.0
                if item.get("last_used_at"):
                    try:
                        used_dt = datetime.fromisoformat(item["last_used_at"])
                        if used_dt.tzinfo is None:
                            used_dt = used_dt.replace(tzinfo=timezone.utc)
                        if (now_dt - used_dt).total_seconds() < 86400.0:
                            used_boost = 1.0
                    except Exception:
                        pass

                # BM25 rank is negative (lower = better match in sqlite FTS5)
                # Transform to 0..1 scale
                bm25_val = abs(item["bm25_rank"])
                match_score = 1.0 / (1.0 + bm25_val)

                importance_norm = item.get("importance", 2) / 5.0

                # Combined score
                composite = (
                    match_score * 0.45
                    + importance_norm * 0.25
                    + recency_score * 0.20
                    + used_boost * 0.10
                )
                item["score"] = round(composite, 3)
                scored.append((composite, item))

            scored.sort(key=lambda x: x[0], reverse=True)
            results = [s[1] for s in scored[:limit]]

            # Update last_used_at timestamp if requested
            if touch_used and results:
                now_str = _now_iso()
                fact_ids = [r["id"] for r in results]
                conn.execute(
                    f"UPDATE facts SET last_used_at = ? WHERE id IN ({','.join('?' * len(fact_ids))})",
                    [now_str] + fact_ids,
                )

            return results

    # ------------------------------------------------------------------
    # 3. CANDIDATE INBOX & CONTRADICTIONS
    # ------------------------------------------------------------------

    def propose_candidate(
        self,
        entity_name: str,
        text: str,
        entity_type: str = "topic",
        source: str = "learned",
    ) -> dict[str, Any]:
        """Propose a learned fact to the Candidate Inbox. Checks secrets & contradictions."""
        # 1. Identity Guard Secret Filter
        if contains_secrets(text):
            logger.warning("Secret detected in candidate fact '%s' for '%s'; auto-rejected.", text[:30], entity_name)
            return {
                "ok": False,
                "status": "rejected",
                "reason": "secrets_blocked",
                "message": "Secrets, credentials, or personal numbers cannot be stored.",
            }

        ent_name = entity_name.strip()
        text_clean = text.strip()
        now = _now_iso()

        # 2. Contradiction Detection against existing active facts for this entity
        conflict_id = None
        existing_facts = []
        ent = self.get_entity(ent_name)
        if ent:
            existing_facts = self.list_facts(entity_id=ent["id"], status="active")

        # Check for semantic / keyword clash (e.g. role, company, team, location change)
        job_keywords = {"works", "joined", "manager", "director", "lead", "engineer", "role", "company", "at", "started"}
        loc_keywords = {"moved", "lives", "relocated", "in", "to"}

        for ef in existing_facts:
            ef_words = set(re.findall(r"\w+", ef["text"].lower()))
            new_words = set(re.findall(r"\w+", text_clean.lower()))
            overlap = ef_words.intersection(new_words)
            has_job_clash = bool((ef_words & job_keywords) and (new_words & job_keywords))
            has_loc_clash = bool((ef_words & loc_keywords) and (new_words & loc_keywords))
            if len(overlap) >= 2 or has_job_clash or has_loc_clash:
                conflict_id = ef["id"]
                break

        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO candidate_memories (entity_name, entity_type, text, source, status, conflicts_with_fact_id, created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (ent_name, entity_type, text_clean, source, conflict_id, now),
            )
            cid = cur.lastrowid
            row = conn.execute("SELECT * FROM candidate_memories WHERE id = ?", (cid,)).fetchone()
            logger.info("Proposed candidate memory #%d for '%s' (Conflict: %s)", cid, ent_name, conflict_id)
            return dict(row)

    def list_candidates(self, status: str = "pending") -> list[dict[str, Any]]:
        """List candidate inbox memories."""
        with self._conn() as conn:
            query = "SELECT * FROM candidate_memories"
            params = []
            if status != "all":
                query += " WHERE status = ?"
                params.append(status)
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def approve_candidate(self, candidate_id: int) -> dict[str, Any]:
        """User approves candidate memory -> converts to active fact (superseding any conflict)."""
        with self._conn() as conn:
            cand = conn.execute("SELECT * FROM candidate_memories WHERE id = ?", (candidate_id,)).fetchone()
            if not cand:
                raise ValueError(f"Candidate #{candidate_id} not found.")
            if cand["status"] == "approved":
                return {"ok": True, "message": "Already approved."}

            now = _now_iso()
            # If conflicting fact exists, mark it superseded
            if cand["conflicts_with_fact_id"]:
                conn.execute(
                    "UPDATE facts SET status = 'superseded' WHERE id = ?",
                    (cand["conflicts_with_fact_id"],),
                )
                logger.info("Fact #%d marked superseded by candidate #%d.", cand["conflicts_with_fact_id"], candidate_id)

            # Mark candidate approved
            conn.execute(
                "UPDATE candidate_memories SET status = 'approved', resolved_at = ? WHERE id = ?",
                (now, candidate_id),
            )

        # Add as active fact
        fact = self.add_fact(
            entity_name=cand["entity_name"],
            text=cand["text"],
            entity_type=cand["entity_type"],
            source=cand["source"],
            importance=3,
        )
        return {
            "ok": True,
            "candidate_id": candidate_id,
            "fact": fact,
            "superseded_fact_id": cand["conflicts_with_fact_id"],
            "message": f"Approved! Remembered that {cand['entity_name']} {cand['text']}.",
        }

    def reject_candidate(self, candidate_id: int) -> bool:
        """User rejects candidate memory -> dismissed without adding to memory."""
        now = _now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE candidate_memories SET status = 'rejected', resolved_at = ? WHERE id = ?",
                (now, candidate_id),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # 4. WEEKLY REVIEW ("Still True?" Flow)
    # ------------------------------------------------------------------

    def get_unconfirmed_facts(self, days: int = 30) -> list[dict[str, Any]]:
        """List active facts where last_confirmed_at is older than N days."""
        now_dt = datetime.now(timezone.utc)
        cutoff_dt = now_dt - timedelta(days=days)
        cutoff_str = cutoff_dt.isoformat()

        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT f.*, e.name AS entity_name, e.type AS entity_type
                FROM facts f
                JOIN entities e ON f.entity_id = e.id
                WHERE f.status = 'active' AND f.last_confirmed_at < ?
                ORDER BY f.last_confirmed_at ASC
                """,
                (cutoff_str,),
            ).fetchall()
            return [dict(r) for r in rows]

    def confirm_fact(self, fact_id: int) -> bool:
        """Mark fact confirmed as still true; resets last_confirmed_at to now."""
        now = _now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE facts SET last_confirmed_at = ? WHERE id = ?", (now, fact_id)
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # 5. VOICE SHORTCUTS: FORGETTING
    # ------------------------------------------------------------------

    def forget_everything_about(self, entity_name: str) -> dict[str, Any]:
        """Voice shortcut: 'forget everything about X'."""
        ent = self.get_entity(entity_name)
        if not ent:
            return {"ok": False, "message": f"I don't have any memories about '{entity_name}', boss."}

        with self._conn() as conn:
            # Delete facts and entity
            conn.execute("DELETE FROM facts WHERE entity_id = ?", (ent["id"],))
            conn.execute("DELETE FROM entities WHERE id = ?", (ent["id"],))
            # Delete candidates
            conn.execute("DELETE FROM candidate_memories WHERE entity_name = ? COLLATE NOCASE", (entity_name,))

        logger.info("Forgotten everything about '%s'.", entity_name)
        return {
            "ok": True,
            "entity": entity_name,
            "message": f"Done boss, completely forgot everything about {entity_name}.",
        }

    def forget_last_fact(self) -> dict[str, Any] | None:
        """Voice shortcut: 'forget the last thing I told you'."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT f.*, e.name AS entity_name
                   FROM facts f
                   JOIN entities e ON f.entity_id = e.id
                   ORDER BY f.id DESC LIMIT 1"""
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM facts WHERE id = ?", (row["id"],))
            logger.info("Forgotten last fact #%d: '%s'", row["id"], row["text"])
            return dict(row)

    # ------------------------------------------------------------------
    # 6. DAILY JOURNAL (Personal & Local)
    # ------------------------------------------------------------------

    def generate_daily_journal(self, date_str: str | None = None) -> dict[str, Any]:
        """Create a local summary journal entry from completed tasks, notes, and topics."""
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        now = _now_iso()

        # 1. Pull completed tasks for date
        completed_tasks = []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'done' AND completed_at LIKE ?",
                (f"{date_str}%",),
            ).fetchall()
            completed_tasks = [dict(r) for r in rows]

        # 2. Pull notes
        from app.core.notes import list_notes
        all_notes = list_notes()
        day_notes = [n for n in all_notes if date_str in n.get("modified_at", "")]

        tasks_summary = ", ".join(t["title"] for t in completed_tasks) if completed_tasks else "No tasks closed."
        notes_summary = ", ".join(n["title"] for n in day_notes) if day_notes else "No notes modified."

        content_lines = [
            f"Daily Journal for {date_str}:",
            f"- Tasks Closed ({len(completed_tasks)}): {tasks_summary}",
            f"- Notes Active ({len(day_notes)}): {notes_summary}",
        ]
        content = "\n".join(content_lines)

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO daily_journals (date, content, tasks_summary, notes_summary, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                       content = excluded.content,
                       tasks_summary = excluded.tasks_summary,
                       notes_summary = excluded.notes_summary,
                       updated_at = excluded.updated_at""",
                (date_str, content, tasks_summary, notes_summary, now, now),
            )
            row = conn.execute("SELECT * FROM daily_journals WHERE date = ?", (date_str,)).fetchone()
            return dict(row)

    def list_journals(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM daily_journals ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_journal(self, date_str: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM daily_journals WHERE date = ?", (date_str,)).fetchone()
            return dict(row) if row else None

    def update_journal(self, date_str: str, content: str) -> bool:
        now = _now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE daily_journals SET content = ?, updated_at = ? WHERE date = ?",
                (content.strip(), now, date_str),
            )
            return cur.rowcount > 0

    def delete_journal(self, date_str: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM daily_journals WHERE date = ?", (date_str,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # 7. EXPORT
    # ------------------------------------------------------------------

    def export_memory_2_0_json(self) -> str:
        """Export all entities, facts, candidate memories, and journals as JSON."""
        with self._conn() as conn:
            entities = [dict(r) for r in conn.execute("SELECT * FROM entities").fetchall()]
            facts = [dict(r) for r in conn.execute("SELECT * FROM facts").fetchall()]
            candidates = [dict(r) for r in conn.execute("SELECT * FROM candidate_memories").fetchall()]
            journals = [dict(r) for r in conn.execute("SELECT * FROM daily_journals").fetchall()]

        return json.dumps({
            "exported_at": _now_iso(),
            "version": "2.0",
            "entities": entities,
            "facts": facts,
            "candidates": candidates,
            "journals": journals,
        }, indent=2)


# Global singleton instance
memory_v2 = MemoryManagerV2()
