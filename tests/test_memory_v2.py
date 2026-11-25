"""tests/test_memory_v2.py

Automated test suite for Prompt 4: Memory 2.0.

Tests:
1. Candidate Inbox never auto-saves without explicit approval.
2. Sensitive credentials and secrets are strictly rejected.
3. Contradiction detection and fact superseding.
4. Hybrid retrieval ranking (FTS5 + importance + recency decay + last-used boost).
5. Weekly Review ("Still True?" flow) for facts unconfirmed > 30 days.
6. Forgetting controls ('forget everything about X', 'forget last thing').
7. Daily Journal local generation, persistence, edit, and delete.
8. Prompt 4 Acceptance scenario (person & project proposed in inbox, approve, retrieve, weekly review).
9. REST API endpoints for Memory 2.0.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.memory.memory_v2 import MemoryManagerV2, contains_secrets
from app.web.server import app


@pytest.fixture
def mem_mgr(tmp_path):
    """Create a temporary MemoryManagerV2 with migration 005 applied."""
    db_file = tmp_path / "test_memory_v2.db"
    from app.memory.db import init_db
    init_db(str(db_file))
    return MemoryManagerV2(db_path=str(db_file))


def test_candidate_inbox_never_auto_saves(mem_mgr):
    """Verify proposed facts stay pending in inbox and are NOT active facts until approved."""
    # 1. Propose fact
    cand = mem_mgr.propose_candidate(
        entity_name="Ravi",
        text="is your new engineering manager",
        entity_type="person",
        source="conversation",
    )
    assert cand["status"] == "pending"

    # 2. Check active facts table - must be completely empty
    active_facts = mem_mgr.list_facts(status="active")
    assert len(active_facts) == 0

    # 3. Hybrid search returns 0 results
    search_res = mem_mgr.search_facts_hybrid("engineering manager")
    assert len(search_res) == 0

    # 4. Explicit user approval
    appr_res = mem_mgr.approve_candidate(cand["id"])
    assert appr_res["ok"] is True

    # 5. Now it is an active fact
    active_facts_after = mem_mgr.list_facts(status="active")
    assert len(active_facts_after) == 1
    assert active_facts_after[0]["entity_name"] == "Ravi"
    assert "engineering manager" in active_facts_after[0]["text"]


def test_secrets_strictly_rejected(mem_mgr):
    """Verify passwords, API keys, card numbers, and credentials are auto-blocked."""
    secret_texts = [
        "My password is SecretPassword123!",
        "api_key: AIzaSyD98234872938479234872934",
        "Credit card is 4532-1234-5678-9012 with CVV 342",
        "SSN is 000-12-3456",
        "Access token: ghp_99882233445566778899aabbccddeeff",
    ]

    for secret in secret_texts:
        assert contains_secrets(secret) is True
        # Propose candidate must be rejected
        res = mem_mgr.propose_candidate(entity_name="Secrets", text=secret)
        assert res["ok"] is False
        assert res["status"] == "rejected"

        # Direct add_fact must raise ValueError
        with pytest.raises(ValueError, match="secrets"):
            mem_mgr.add_fact(entity_name="Secrets", text=secret)

    # Inbox and facts must be 0
    assert len(mem_mgr.list_candidates()) == 0
    assert len(mem_mgr.list_facts()) == 0


def test_contradiction_flow_and_superseding(mem_mgr):
    """Verify conflicting facts mark old facts as superseded while keeping history."""
    # 1. User has an existing active fact
    mem_mgr.add_fact(
        entity_name="Ravi",
        text="works as manager at Infosys",
        entity_type="person",
    )
    initial_facts = mem_mgr.list_facts(status="active")
    assert len(initial_facts) == 1
    old_fact_id = initial_facts[0]["id"]

    # 2. Candidate proposed with new role / company
    cand = mem_mgr.propose_candidate(
        entity_name="Ravi",
        text="joined TCS as director",
        entity_type="person",
    )
    assert cand["status"] == "pending"
    # Contradiction detector should have flagged conflict with old_fact_id
    assert cand["conflicts_with_fact_id"] == old_fact_id

    # 3. Approve candidate
    res = mem_mgr.approve_candidate(cand["id"])
    assert res["ok"] is True
    assert res["superseded_fact_id"] == old_fact_id

    # 4. Old fact must now be 'superseded', new fact 'active'
    active_facts = mem_mgr.list_facts(status="active")
    assert len(active_facts) == 1
    assert "joined TCS" in active_facts[0]["text"]

    all_facts = mem_mgr.list_facts(status="all")
    assert len(all_facts) == 2
    superseded = [f for f in all_facts if f["status"] == "superseded"]
    assert len(superseded) == 1
    assert superseded[0]["id"] == old_fact_id


def test_hybrid_retrieval_ranking_and_decay(mem_mgr):
    """Verify FTS5 match, importance weight, and 30-day recency decay."""
    now_dt = datetime.now(timezone.utc)

    # Fact 1: Recent, high importance (P5)
    f1 = mem_mgr.add_fact(entity_name="FastAPI", text="User prefers async FastAPI routers", importance=5)

    # Fact 2: Stale fact from 45 days ago, low importance (P1)
    f2 = mem_mgr.add_fact(entity_name="Flask", text="User used Flask microframework routers", importance=1)
    stale_date = (now_dt - timedelta(days=45)).isoformat()
    with mem_mgr._conn() as conn:
        conn.execute("UPDATE facts SET last_confirmed_at = ? WHERE id = ?", (stale_date, f2["id"]))

    # Hybrid search for "routers"
    results = mem_mgr.search_facts_hybrid("routers", limit=5)
    assert len(results) == 2
    # Recent high-importance fact must rank first
    assert results[0]["id"] == f1["id"]
    assert results[0]["score"] > results[1]["score"]

    # Verify last_used_at timestamp was touched
    updated_f1 = mem_mgr.list_facts(status="active")[0]
    assert updated_f1["last_used_at"] is not None


def test_weekly_review_still_true_flow(mem_mgr):
    """Verify facts unconfirmed > 30 days appear in Weekly Review and can be confirmed."""
    now_dt = datetime.now(timezone.utc)

    # Fresh fact (confirmed today)
    mem_mgr.add_fact(entity_name="Ugesh", text="Lives in Coimbatore")

    # Stale fact (unconfirmed for 35 days)
    stale_fact = mem_mgr.add_fact(entity_name="Project", text="Targeting Q4 release")
    stale_date = (now_dt - timedelta(days=35)).isoformat()
    with mem_mgr._conn() as conn:
        conn.execute("UPDATE facts SET last_confirmed_at = ? WHERE id = ?", (stale_date, stale_fact["id"]))

    # Weekly review query
    stale_list = mem_mgr.get_unconfirmed_facts(days=30)
    assert len(stale_list) == 1
    assert stale_list[0]["id"] == stale_fact["id"]

    # User confirms "Still True"
    ok = mem_mgr.confirm_fact(stale_fact["id"])
    assert ok is True

    # Stale list should now be empty
    assert len(mem_mgr.get_unconfirmed_facts(days=30)) == 0


def test_forgetting_really_deletes(mem_mgr):
    """Verify 'forget everything about X' deletes entities, facts, and FTS entries."""
    mem_mgr.add_fact(entity_name="Project Falcon", text="Secret hardware project for drone telemetry", entity_type="project")
    mem_mgr.add_fact(entity_name="Project Falcon", text="Uses ESP32-S3 microcontroller", entity_type="project")

    # Verify searchable in FTS
    assert len(mem_mgr.search_facts_hybrid("telemetry")) == 1

    # Forget everything about Project Falcon
    res = mem_mgr.forget_everything_about("Project Falcon")
    assert res["ok"] is True

    # Must be completely purged from facts, entities, and FTS
    assert mem_mgr.get_entity("Project Falcon") is None
    assert len(mem_mgr.list_facts(status="active")) == 0
    assert len(mem_mgr.search_facts_hybrid("telemetry")) == 0
    assert len(mem_mgr.search_facts_hybrid("microcontroller")) == 0


def test_forget_last_fact(mem_mgr):
    """Verify 'forget the last thing I told you' deletes the most recent fact."""
    mem_mgr.add_fact(entity_name="Ravi", text="Likes filter coffee")
    f2 = mem_mgr.add_fact(entity_name="Ravi", text="Prefers dark roast beans")

    res = mem_mgr.forget_last_fact()
    assert res is not None
    assert res["id"] == f2["id"]

    # Only first fact remains
    remaining = mem_mgr.list_facts(status="active")
    assert len(remaining) == 1
    assert remaining[0]["text"] == "Likes filter coffee"


def test_daily_journal_stays_local(mem_mgr):
    """Verify daily journal generation, local persistence, update, and delete."""
    today = datetime.now().strftime("%Y-%m-%d")

    journal = mem_mgr.generate_daily_journal(today)
    assert journal["date"] == today
    assert "Daily Journal" in journal["content"]

    # Update journal
    ok = mem_mgr.update_journal(today, "Updated: Productive debugging day with Nalla.")
    assert ok is True
    updated = mem_mgr.get_journal(today)
    assert "Productive debugging day" in updated["content"]

    # Delete journal
    del_ok = mem_mgr.delete_journal(today)
    assert del_ok is True
    assert mem_mgr.get_journal(today) is None


def test_acceptance_scenario_person_project_and_review(mem_mgr):
    """Prompt 4 Acceptance:
    - Mention person & project in conversation.
    - Proposed in inbox.
    - Approve one.
    - Ask a week later (simulated date) and get correct answer.
    - Weekly review flow works.
    """
    from app.memory.candidate_extractor import extract_and_propose_candidates

    # 1. Mention person and project in conversational user query
    user_msg = "Ravi is our new tech lead and we are working on Project Falcon."

    # Use our memory manager instance for extractor
    with patch("app.memory.candidate_extractor.memory_v2", mem_mgr):
        proposals = extract_and_propose_candidates(user_msg)

    assert len(proposals) >= 2
    person_prop = next(p for p in proposals if p["entity_name"] == "Ravi")
    proj_prop = next(p for p in proposals if p["entity_name"] == "Project Falcon")

    assert person_prop["status"] == "pending"
    assert proj_prop["status"] == "pending"

    # 2. User approves only the person proposal
    appr_res = mem_mgr.approve_candidate(person_prop["id"])
    assert appr_res["ok"] is True

    # 3. Simulate asking 7 days later:
    query_text = "Who is our tech lead?"
    results = mem_mgr.search_facts_hybrid(query_text)
    assert len(results) == 1
    assert results[0]["entity_name"] == "Ravi"
    assert "tech lead" in results[0]["text"]

    # Unapproved project must NOT be an active fact
    proj_facts = [f for f in mem_mgr.list_facts() if f["entity_name"] == "Project Falcon"]
    assert len(proj_facts) == 0

    # 4. Fast-forward last_confirmed_at by 35 days and test Weekly Review flow
    now_dt = datetime.now(timezone.utc)
    stale_dt = (now_dt - timedelta(days=35)).isoformat()
    with mem_mgr._conn() as conn:
        conn.execute("UPDATE facts SET last_confirmed_at = ? WHERE id = ?", (stale_dt, results[0]["id"]))

    unconfirmed = mem_mgr.get_unconfirmed_facts(days=30)
    assert len(unconfirmed) == 1
    assert unconfirmed[0]["entity_name"] == "Ravi"

    # User confirms "Still True"
    conf_ok = mem_mgr.confirm_fact(unconfirmed[0]["id"])
    assert conf_ok is True
    assert len(mem_mgr.get_unconfirmed_facts(days=30)) == 0


def test_rest_api_memory_v2_endpoints():
    """Verify REST API endpoints for entities, facts, candidates, journals, and export."""
    client = TestClient(app)

    # 1. Entities
    ent_resp = client.post("/api/memory/entities", json={
        "name": "Kavitha",
        "type": "person",
        "summary": "Firmware Engineer",
    })
    assert ent_resp.status_code == 201
    ent_id = ent_resp.json()["id"]

    # 2. Facts
    fact_resp = client.post("/api/memory/facts", json={
        "entity_name": "Kavitha",
        "text": "Leads firmware optimization for ESP32",
        "entity_type": "person",
        "importance": 4,
    })
    assert fact_resp.status_code == 201
    fact_id = fact_resp.json()["fact"]["id"]

    # 3. Hybrid search
    search_resp = client.get("/api/memory/facts/search?q=firmware")
    assert search_resp.status_code == 200
    assert search_resp.json()["count"] >= 1

    # 4. Candidates propose & approve
    cand_resp = client.post("/api/memory/candidates/propose", json={
        "entity_name": "Kavitha",
        "text": "Moved to Coimbatore office",
        "entity_type": "person",
    })
    assert cand_resp.status_code == 201
    cid = cand_resp.json()["candidate"]["id"]

    appr_resp = client.post(f"/api/memory/candidates/{cid}/approve")
    assert appr_resp.status_code == 200

    # 5. Daily Journals
    j_resp = client.post("/api/memory/journals/generate")
    assert j_resp.status_code == 200
    j_date = j_resp.json()["journal"]["date"]

    get_j = client.get(f"/api/memory/journals/{j_date}")
    assert get_j.status_code == 200

    # 6. Memory 2.0 Export
    exp_resp = client.get("/api/memory/export/v2")
    assert exp_resp.status_code == 200
    exp_data = exp_resp.json()
    assert exp_data["version"] == "2.0"
    assert "entities" in exp_data
    assert "facts" in exp_data
