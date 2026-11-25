"""tests/logic_eval.py

40-case Logic & Deterministic Tool Evaluation Suite (Prompt H5 & H7).
Tests offline math, percentages, date math, unit conversions, complexity routing,
safety/crisis guard, and deterministic number auditing without network calls.
"""

from __future__ import annotations

import pytest

from app.brain import fast_router
from app.brain.logic_router import classify_complexity
from app.brain.scenarios import check_crisis

LOGIC_CASES = [
    # Arithmetic & Percentages (12)
    ("twelve percent of four fifty", "tool", "54"),
    ("what is 15 times 23", "tool", "345"),
    ("split 1800 among 4", "tool", "450"),
    ("25 + 75", "tool", "100"),
    ("1000 - 250", "tool", "750"),
    ("20 percent of 500", "tool", "100"),
    ("144 / 12", "tool", "12"),
    ("what is 9 times 9", "tool", "81"),
    ("split 900 among 3", "tool", "300"),
    ("50 percent of 80", "tool", "40"),
    ("12 * 12", "tool", "144"),
    ("500 divided by 25", "tool", "20"),
    # Units & Currency refusal (8)
    ("convert 72 fahrenheit to celsius", "tool", "celsius"),
    ("convert 5 km to miles", "tool", "miles"),
    ("convert 100 cm to meters", "tool", "meters"),
    ("convert 2 kg to grams", "tool", "grams"),
    ("convert 1024 mb to gb", "tool", "gb"),
    ("convert 0 celsius to fahrenheit", "tool", "fahrenheit"),
    ("convert 100 usd to inr", "tool", "internet"),
    ("convert 50 eur to usd", "tool", "internet"),
    # Date & Time math (6)
    ("what is the date after 45 days", "tool", "boss"),
    ("how many days till 31 December", "tool", "days until"),
    ("time in Tokyo", "tool", "Tokyo"),
    ("time in London", "tool", "London"),
    ("time in New York", "tool", "New York"),
    ("10 days from now", "tool", "boss"),
    # System / Random / Scenarios (6)
    ("flip a coin", "tool", "boss"),
    ("roll a dice", "tool", "Rolled"),
    ("tell me a joke", "tool", ""),
    ("how are you?", "scenario", ""),
    ("epdi irukka", "scenario", ""),
    ("saapteengala", "scenario", ""),
    # Complexity routing (4 deep, 2 light, 2 crisis)
    ("compare SQLite vs Postgres for embedded Pi", "deep", ""),
    ("think harder about why my ESP32 brownouts", "deep", ""),
    ("plan my sprint with 3 constraints and a deadline", "deep", ""),
    ("explain why async queues avoid blocking audio", "deep", ""),
    ("tell me a story about space", "light", ""),
    ("who is the prime minister of India", "light", ""),
    ("I want to kill myself", "crisis", ""),
    ("having a heart attack right now", "crisis", ""),
]


assert len(LOGIC_CASES) == 40


@pytest.mark.parametrize("query,expected_route,expected_substr", LOGIC_CASES)
def test_logic_eval_40_cases(query, expected_route, expected_substr):
    if expected_route == "crisis":
        assert check_crisis(query).is_crisis is True
        assert fast_router.route(query) is None
        return

    if expected_route in ("tool", "scenario"):
        reply = fast_router.route(query)
        assert reply is not None, f"Expected local {expected_route} hit for: {query}"
        info = fast_router.get_last_route_info()
        assert info["route"] == expected_route
        if expected_substr:
            assert expected_substr.lower() in reply.lower(), f"Expected '{expected_substr}' in '{reply}'"
        return

    # LLM routes (light / deep)
    dec = classify_complexity(query, is_instant=False)
    assert dec.complexity == expected_route
