"""app/memory/candidate_extractor.py

Candidate Memory Extractor (Prompt 4).

Extracts candidate facts about people, projects, places, and organizations
from conversation turns and proposes them to the Candidate Inbox.

Guarantees & Identity Guard rules:
- Proposes facts to candidate_memories table with status='pending'.
- NEVER auto-saves directly to active memory; user must approve.
- STRICTLY rejects secrets, passwords, tokens, financial details, or card numbers.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.memory.memory_v2 import contains_secrets, memory_v2

logger = logging.getLogger("nalla.memory_extractor")

# Heuristic patterns for conversational fact extraction
_PERSON_ROLE = re.compile(
    r"\b([A-Z][a-z]+)\s+is\s+(?:my|our)\s+(?:new\s+)?(manager|lead|boss|colleague|friend|tech lead|director|engineer|mentor)\b",
    re.IGNORECASE,
)

_PERSON_COMPANY = re.compile(
    r"\b([A-Z][a-z]+)\s+(?:joined|works at|is at|started at)\s+([A-Z][A-Za-z0-9\s&]+)\b",
    re.IGNORECASE,
)

_PROJECT_NAME = re.compile(
    r"\b(?:working on|building|launching)\s+((?:Project\s+)?[A-Z][A-Za-z0-9_-]+)\b",
    re.IGNORECASE,
)

_MOVED_LOCATION = re.compile(
    r"\b([A-Z][a-z]+|I)\s+(?:moved to|lives in|relocated to)\s+([A-Z][a-z]+)\b",
    re.IGNORECASE,
)


def extract_and_propose_candidates(user_text: str) -> list[dict[str, Any]]:
    """Scan user text for factual statements about entities and propose to Candidate Inbox."""
    if not user_text or len(user_text) < 5 or contains_secrets(user_text):
        return []

    proposals: list[dict[str, Any]] = []

    # 1. Person & role
    m_role = _PERSON_ROLE.search(user_text)
    if m_role:
        name = m_role.group(1).title()
        role = m_role.group(2).lower()
        if name.lower() not in ("what", "who", "when", "how", "this", "that"):
            fact_text = f"is your new {role}" if "new" in user_text.lower() else f"is your {role}"
            res = memory_v2.propose_candidate(entity_name=name, text=fact_text, entity_type="person", source="conversation")
            if res.get("id"):
                proposals.append(res)

    # 2. Person & company/org
    m_comp = _PERSON_COMPANY.search(user_text)
    if m_comp:
        name = m_comp.group(1).title()
        org = m_comp.group(2).strip()
        if name.lower() not in ("what", "who", "when", "how", "this", "that"):
            fact_text = f"works at {org}"
            res = memory_v2.propose_candidate(entity_name=name, text=fact_text, entity_type="person", source="conversation")
            if res.get("id") and not any(p.get("id") == res.get("id") for p in proposals):
                proposals.append(res)

    # 3. Project name
    m_proj = _PROJECT_NAME.search(user_text)
    if m_proj:
        proj_name = m_proj.group(1).strip()
        if proj_name.lower() not in ("this", "that", "something", "everything", "nothing", "what"):
            fact_text = "is an active project you are working on"
            res = memory_v2.propose_candidate(entity_name=proj_name, text=fact_text, entity_type="project", source="conversation")
            if res.get("id") and not any(p.get("id") == res.get("id") for p in proposals):
                proposals.append(res)

    # 4. Location move
    m_loc = _MOVED_LOCATION.search(user_text)
    if m_loc:
        who = m_loc.group(1)
        place = m_loc.group(2).title()
        ent_name = "User" if who.lower() == "i" else who.title()
        fact_text = f"moved to {place}"
        res = memory_v2.propose_candidate(entity_name=ent_name, text=fact_text, entity_type="person" if ent_name != "User" else "topic", source="conversation")
        if res.get("id") and not any(p.get("id") == res.get("id") for p in proposals):
            proposals.append(res)

    return proposals
