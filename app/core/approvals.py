"""app/core/approvals.py

Approval management for tool calls.

Defines the RiskLevel enum (GREEN / AMBER / RED) and an ApprovalManager
that gates AMBER and RED tool executions behind a user-confirmation
callback.  The callback is pluggable so that the terminal, the web
dashboard, and the voice interface can each supply their own
implementation without changing this module.

Decision log is written via the standard logging module so that every
approval/denial is permanently recorded in nalla.log.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Callable

logger = logging.getLogger("nalla.approvals")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class RiskLevel(Enum):
    """Tool risk tier as defined in CONTEXT.md."""
    GREEN = "green"   # Execute immediately, no confirmation needed.
    AMBER = "amber"   # Ask the user once before running.
    RED   = "red"     # Explicit, typed confirmation required every time.


# Type alias for the pluggable confirmation callback.
# Receives a human-readable description of the action and the risk level;
# returns True to approve, False to deny.
ApprovalCallback = Callable[[str, RiskLevel], bool]


def _terminal_callback(description: str, risk: RiskLevel) -> bool:
    """Default approval callback: asks the user via stdin.

    Replace this with a WebSocket or voice callback in later phases.
    """
    tier_label = {
        RiskLevel.AMBER: "⚠️  AMBER",
        RiskLevel.RED:   "🔴 RED — CONFIRM AGAIN",
    }.get(risk, risk.value.upper())

    prompt = (
        f"\n[{tier_label}] Nalla wants to: {description}\n"
        "  Approve? (y/n): "
    )
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    return answer == "y"


class ApprovalManager:
    """Gates AMBER / RED tool executions behind a user-confirmation callback.

    Usage::

        manager = ApprovalManager()
        approved = manager.request("toggle relay 1", RiskLevel.AMBER)
        if not approved:
            # Nalla must report the action did not happen.
    """

    def __init__(self, callback: ApprovalCallback | None = None) -> None:
        self._callback: ApprovalCallback = callback or _terminal_callback

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def request(self, description: str, risk: RiskLevel) -> bool:
        """Return True if the action is approved, False otherwise.

        GREEN actions are always approved without calling the callback.
        AMBER requires one confirmation.
        RED requires one confirmation (the UI should make it feel heavier
        than AMBER — e.g., ask for a typed confirmation phrase; that is
        the responsibility of the callback implementation).
        """
        if risk == RiskLevel.GREEN:
            logger.debug("AUTO-APPROVED (GREEN): %s", description)
            return True

        approved = self._callback(description, risk)
        status = "APPROVED" if approved else "DENIED"
        logger.info("APPROVAL %s [%s]: %s", status, risk.value.upper(), description)
        return approved

    def set_callback(self, callback: ApprovalCallback) -> None:
        """Swap the confirmation callback at runtime (e.g., when dashboard starts)."""
        self._callback = callback
