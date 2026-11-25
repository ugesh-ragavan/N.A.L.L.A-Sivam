"""app/brain/logic_router.py

Complexity Router, Topic Stack, and Deterministic Audit (Prompt H5).

1. Complexity Router (< 10 ms): classifies turns as 'instant', 'light', or 'deep'.
   Supports explicit override "think harder" -> forces 'deep'.
2. Model & Thinking Routing: maps 'light' vs 'deep' to model + thinking_level,
   falling back to 'light' when daily quota is near cap.
3. Topic Stack (last 3 topics/entities) for follow-up pronoun resolution.
4. Deterministic Number Post-Check: flags replies containing numbers that did not
   come from a deterministic tool on math/unit/date queries.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from app import config

logger = logging.getLogger("nalla.brain.logic_router")

_DEEP_PATTERNS = re.compile(
    r"\b("
    r"think\s+harder|compare|trade[\s-]?off|pros\s+and\s+cons|architecture|design"
    r"|debug|refactor|optimize|explain\s+why|why\s+(does|did|is|are|would|should)"
    r"|step\s+by\s+step|plan\s+(my|the|a)|schedule\s+conflict|deduce|analyze|analyse"
    r")\b",
    re.IGNORECASE,
)

_MATH_QUERY_RE = re.compile(
    r"\b(percent|%|calculate|compute|multiply|divide|plus|minus|sum|total|emi|interest|convert)\b|\d+\s*[+\-*/]\s*\d+",
    re.IGNORECASE,
)

_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")


@dataclass
class RouteDecision:
    complexity: str  # "instant" | "light" | "deep"
    model: str
    thinking_level: str
    forced_deep: bool = False
    quota_fallback: bool = False
    latency_ms: float = 0.0


class TopicStack:
    """Tracks the last 3 topics/entities for resolving follow-up pronouns."""

    def __init__(self, maxlen: int = 3) -> None:
        self._stack: deque[str] = deque(maxlen=maxlen)

    def push(self, topic: str) -> None:
        clean = topic.strip()
        if not clean or len(clean) < 2:
            return
        if clean in self._stack:
            self._stack.remove(clean)
        self._stack.append(clean)

    def current(self) -> str | None:
        return self._stack[-1] if self._stack else None

    def as_list(self) -> list[str]:
        return list(self._stack)

    def clear(self) -> None:
        self._stack.clear()

    def build_hint(self, user_text: str) -> str:
        """If user_text looks like a short follow-up ('and tomorrow?', 'why?'), provide topic hint."""
        if not self._stack:
            return ""
        low = user_text.strip().lower()
        words = low.split()
        is_followup = (
            len(words) <= 6
            and (
                low.startswith(("and ", "what about ", "how about ", "why", "which one"))
                or any(p in words for p in ("it", "that", "this", "them", "tomorrow"))
            )
        )
        if not is_followup:
            return ""
        return f"Active topic context (most recent last): {', '.join(self._stack)}."


topic_stack = TopicStack()


def classify_complexity(user_text: str, is_instant: bool = False) -> RouteDecision:
    """Classify turn complexity in < 10 ms and select model + thinking_level."""
    t0 = time.perf_counter()
    if is_instant:
        return RouteDecision(
            complexity="instant",
            model="local",
            thinking_level="NONE",
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        )

    text = user_text.strip()
    low = text.lower()
    forced_deep = "think harder" in low

    words = text.split()
    is_deep = forced_deep or bool(_DEEP_PATTERNS.search(text)) or len(words) >= 22

    light_model = getattr(config, "GEMINI_COMMAND_MODEL", getattr(config, "GEMINI_CHAT_MODEL", "gemini-2.5-flash"))
    deep_model = getattr(config, "GEMINI_CHAT_MODEL", "gemini-2.5-flash")

    if not is_deep:
        return RouteDecision(
            complexity="light",
            model=light_model,
            thinking_level="LOW",
            forced_deep=False,
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        )

    # Check quota guard before routing to deep
    quota_fallback = False
    try:
        from app.core.quota import quota
        status = quota.get_status() if hasattr(quota, "get_status") else {}
        pct = float(status.get("percent_used", 0.0))
        if pct >= 85.0:
            quota_fallback = True
    except Exception:
        pass

    if quota_fallback:
        return RouteDecision(
            complexity="light",
            model=light_model,
            thinking_level="LOW",
            forced_deep=forced_deep,
            quota_fallback=True,
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        )

    return RouteDecision(
        complexity="deep",
        model=deep_model,
        thinking_level="MEDIUM",
        forced_deep=forced_deep,
        quota_fallback=False,
        latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
    )


def audit_unverified_numbers(
    user_text: str,
    reply_text: str,
    tools_used: list[str] | None = None,
) -> dict[str, Any]:
    """Post-check: flag math/unit replies that contain numbers without deterministic tool use."""
    if not _MATH_QUERY_RE.search(user_text):
        return {"flagged": False, "numbers": []}
    used = set(tools_used or [])
    if used & {"calculate", "convert_units", "get_time", "set_timer"}:
        return {"flagged": False, "numbers": []}
    nums = _NUMBER_RE.findall(reply_text)
    if nums:
        return {"flagged": True, "numbers": nums, "reason": "Numbers in math reply without deterministic tool."}
    return {"flagged": False, "numbers": []}
