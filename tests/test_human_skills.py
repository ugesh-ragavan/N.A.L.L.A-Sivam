"""tests/test_human_skills.py

Tests for Human Mode H3 (Offline Deterministic Skills):
  - AST calculator correctness & code injection rejection (__import__, eval, etc.)
  - Unit conversion (pint) & currency offline refusal
  - Date maths & timezone conversion
  - Persistent timers surviving reload
  - Curated content pack rotation without repeats
  - Fast-router integration
"""

from __future__ import annotations

import json

import pytest

from app.brain import fast_router
from app.brain import skills


def test_calculator_spoken_and_math():
    r1 = skills.safe_calculate("twelve percent of four fifty")
    assert r1["ok"] is True
    assert r1["result"] == 54.0

    r2 = skills.safe_calculate("what is 15 times 23")
    assert r2["ok"] is True
    assert r2["result"] == 345.0

    r3 = skills.safe_calculate("split 1800 among 4")
    assert r3["ok"] is True
    assert r3["result"] == 450.0


@pytest.mark.parametrize("bad_expr", [
    "__import__('os').system('echo pwned')",
    "eval('1+1')",
    "open('/etc/passwd').read()",
    "().__class__.__bases__[0]",
    "2 ** 999999",
])
def test_calculator_rejects_injection(bad_expr):
    res = skills.safe_calculate(bad_expr)
    assert res["ok"] is False


def test_unit_conversion_and_currency_refusal():
    res = skills.convert_units(72, "fahrenheit", "celsius")
    assert res["ok"] is True
    assert abs(res["value"] - 22.22) < 0.1

    curr = skills.convert_units(100, "usd", "inr")
    assert curr["ok"] is False
    assert curr.get("needs_online") is True


def test_date_math():
    r1 = skills.date_math("what is the date after 45 days")
    assert r1 is not None and r1["ok"] is True

    r2 = skills.date_math("how many days till 31 December")
    assert r2 is not None and r2["ok"] is True
    assert "days until" in r2["reply"]

    r3 = skills.date_math("time in Tokyo")
    assert r3 is not None and r3["ok"] is True
    assert "Tokyo" in r3["reply"]


def test_timers_survive_restart(tmp_path, monkeypatch):
    tfile = tmp_path / "timers.json"
    monkeypatch.setattr(skills, "TIMERS_FILE", tfile)

    res = skills.set_timer(600, label="Tea")
    assert res["ok"] is True
    assert "10 minutes" in res["reply"]

    # Simulate reload by reading directly from disk
    active = skills.list_timers()
    assert len(active) == 1
    assert active[0]["label"] == "Tea"
    assert active[0]["remaining_s"] > 500


def test_pack_rotation_without_repeats():
    skills._PACK_USED.pop("jokes", None)
    seen = set()
    for _ in range(6):
        j = skills.get_pack_item("jokes")
        assert j is not None
        seen.add(j)
    # All 6 unique jokes served before any repeat
    assert len(seen) == 6


def test_fast_router_acceptance_phrases():
    assert "54" in fast_router.route("twelve percent of four fifty")
    assert "days until" in fast_router.route("how many days till 31 December")
    assert "celsius" in fast_router.route("convert 72 fahrenheit to celsius").lower()
    assert fast_router.route("flip a coin") in ("Heads, boss.", "Tails, boss.")
    assert fast_router.route("tell me a joke") is not None
