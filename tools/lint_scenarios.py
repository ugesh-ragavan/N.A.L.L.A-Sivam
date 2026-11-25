"""tools/lint_scenarios.py

Honesty & Quality Linter for NALLA SIVAM scenarios (Prompt H2 & H7).

Rejects scenarios/drafts that:
  (a) claim eating, sleeping, a body, family, or real feelings for Nalla
  (b) state live/volatile facts (prices, live weather, breaking news)
  (c) exceed 18 words per reply
  (d) contain banned AI phrases
  (e) have fewer than 4 reply variants or fewer than 5 trigger phrases
  (f) reference unknown slots
  (g) duplicate or conflict with existing triggers
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.brain.scenarios import normalise_text, load_tanglish_variants  # noqa: E402

VALID_SLOTS = {
    "time", "date", "weekday", "time_band", "owner_name", "requests_today",
    "uptime", "crashes_today", "tasks_open", "next_task", "tasks_done_today",
    "reminders_today", "battery", "wifi_status", "capabilities_summary",
    "mode", "current_voice",
}

BANNED_PHRASES = [
    "as an ai",
    "as a language model",
    "i'd be happy to help",
    "certainly!",
    "great question!",
    "i hope this helps",
    "feel free to ask",
]

# Claims of biological experiences or real feelings by Nalla himself
HUMAN_PRETEND_RE = re.compile(
    r"\b("
    r"i\s+(just\s+)?(ate|eaten|had\s+(breakfast|lunch|dinner|coffee|tea)|slept|woke\s+up|feel\s+(sad|happy|hungry|tired|sick|lonely|pain))"
    r"|i\s+am\s+(hungry|sleepy|sick|eating|sleeping|bleeding|crying)"
    r"|i'?m\s+(hungry|sleepy|sick|eating|sleeping|bleeding|crying)"
    r"|my\s+(mom|mother|dad|father|brother|sister|family|stomach|headache|body|heart\s+aches)"
    r"|naan\s+(saapten|thoonginen|pasikkudhu)"
    r")\b",
    re.IGNORECASE,
)

LIVE_FACT_RE = re.compile(
    r"\b("
    r"(bitcoin|btc|nifty|sensex|gold|petrol|diesel|iphone)\s+(price\s+is|is\s+currently|costs)\s+\d+"
    r"|today'?s\s+temperature\s+is\s+\d+"
    r"|breaking\s+news"
    r")\b",
    re.IGNORECASE,
)

SLOT_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def lint_scenario(
    sc: dict[str, Any],
    seen_triggers: dict[str, str] | None = None,
    variants_map: dict[str, str] | None = None,
    strict_counts: bool = True,
) -> list[str]:
    """Return a list of lint error strings for a single scenario dict."""
    errors: list[str] = []
    sid = sc.get("id", "<missing-id>")

    if not sc.get("id"):
        errors.append("Missing scenario 'id'")
    if not sc.get("category"):
        errors.append(f"{sid}: Missing 'category'")

    # Triggers check
    triggers = sc.get("triggers") or {}
    all_trigs: list[str] = []
    for k in ("en", "tanglish", "ta"):
        vals = triggers.get(k) or []
        all_trigs.extend(str(v).strip() for v in vals if str(v).strip())

    if strict_counts and len(all_trigs) < 5 and not sc.get("followup_group"):
        errors.append(f"{sid}: Fewer than 5 trigger phrases ({len(all_trigs)})")

    if seen_triggers is not None:
        group = sc.get("followup_group", "")
        for t in all_trigs:
            norm = normalise_text(t, variants_map)
            key = f"{group}::{norm}"
            if key in seen_triggers and seen_triggers[key] != sid:
                errors.append(f"{sid}: Duplicate trigger '{t}' conflicts with {seen_triggers[key]}")
            else:
                seen_triggers[key] = sid

    # Replies check
    replies = sc.get("replies") or []
    if strict_counts and len(replies) < 4 and not sc.get("followup_group"):
        errors.append(f"{sid}: Fewer than 4 reply variants ({len(replies)})")

    for idx, r in enumerate(replies):
        text = str(r.get("text", "")).strip() if isinstance(r, dict) else str(r).strip()
        if not text:
            errors.append(f"{sid}: Reply #{idx + 1} is empty")
            continue

        words = text.split()
        if len(words) > 18:
            errors.append(f"{sid}: Reply #{idx + 1} exceeds 18 words ({len(words)} words)")

        low = text.lower()
        for banned in BANNED_PHRASES:
            if banned in low:
                errors.append(f"{sid}: Reply #{idx + 1} contains banned phrase '{banned}'")

        if HUMAN_PRETEND_RE.search(text):
            errors.append(f"{sid}: Reply #{idx + 1} claims human body/meal/sleep/feelings: '{text}'")

        if LIVE_FACT_RE.search(text):
            errors.append(f"{sid}: Reply #{idx + 1} states volatile/live fact: '{text}'")

        for slot_name in SLOT_RE.findall(text):
            if slot_name not in VALID_SLOTS:
                errors.append(f"{sid}: Reply #{idx + 1} uses unknown slot '{{{slot_name}}}'")

    return errors


def lint_directory(target_dir: Path, strict_counts: bool = True) -> dict[str, Any]:
    """Lint all YAML files in target_dir and return a structured report."""
    variants_map = load_tanglish_variants()
    seen_triggers: dict[str, str] = {}
    report: dict[str, Any] = {"files": {}, "total_scenarios": 0, "total_errors": 0}

    if not target_dir.exists():
        return report

    for path in sorted(target_dir.glob("*.yaml")):
        items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        file_errors: list[str] = []
        if not isinstance(items, list):
            file_errors.append(f"{path.name}: Root YAML must be a list")
            items = []
        for sc in items:
            report["total_scenarios"] += 1
            file_errors.extend(lint_scenario(sc, seen_triggers, variants_map, strict_counts=strict_counts))
        report["files"][path.name] = {
            "count": len(items),
            "errors": file_errors,
        }
        report["total_errors"] += len(file_errors)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint Nalla Sivam scenario YAML files.")
    parser.add_argument("--dir", default="data/scenarios/drafts", help="Directory to lint")
    parser.add_argument("--no-strict-counts", action="store_true", help="Allow <4 variants / <5 triggers")
    args = parser.parse_args()

    target = BASE_DIR / args.dir
    rep = lint_directory(target, strict_counts=not args.no_strict_counts)
    print(f"Linted {rep['total_scenarios']} scenarios in {target} -> {rep['total_errors']} errors")
    for fname, info in rep["files"].items():
        status = "OK" if not info["errors"] else f"{len(info['errors'])} ERRORS"
        print(f"  {fname} ({info['count']} scenarios): {status}")
        for err in info["errors"]:
            print(f"    - {err}")
    return 1 if rep["total_errors"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
