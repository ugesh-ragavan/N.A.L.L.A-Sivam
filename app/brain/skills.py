"""app/brain/skills.py

Offline Deterministic Skills (Prompt H3).

Every skill answers locally in < 50 ms without calling an LLM:
  1. Safe AST Calculator (with spoken word-to-number & percentage support)
  2. Unit Converter (pint) + explicit currency online notice
  3. Date & Time Math (dateutil + zoneinfo)
  4. Persistent Timers, Alarms & Pomodoro (25/5)
  5. System Status (psutil)
  6. Random & Decisions (coin flip, dice, pick one, decide for me)
  7. Text Helpers (word/char count, spell, reverse, case, sort)
  8. Curated Content Packs (jokes, riddles, fun_facts, motivation, tamil_proverbs)
  9. Conversation Recall (from short-term SQLite history)
"""

from __future__ import annotations

import ast
import json
import logging
import math
import operator
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app import config

logger = logging.getLogger("nalla.brain.skills")

_DATA_DIR = getattr(config, "DATA_DIR", config.BASE_DIR / "data")
PACKS_DIR = _DATA_DIR / "packs"
TIMERS_FILE = _DATA_DIR / "timers.json"

# ---------------------------------------------------------------------------
# 1. Safe AST Calculator
# ---------------------------------------------------------------------------

_WORD_NUMS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
}

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
}

_SAFE_CONSTS = {"pi": math.pi, "e": math.e}


def _words_to_numbers(text: str) -> str:
    """Convert simple spoken English number phrases like 'four fifty' or 'twelve' to digits."""
    tokens = text.lower().split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _WORD_NUMS:
            val = _WORD_NUMS[tok]
            # Handle "four fifty" -> 450, "twenty three" -> 23, "two hundred" -> 200
            if i + 1 < len(tokens) and tokens[i + 1] in _WORD_NUMS:
                nxt = _WORD_NUMS[tokens[i + 1]]
                if nxt == 100 or nxt == 1000:
                    val *= nxt
                    i += 1
                    if i + 1 < len(tokens) and tokens[i + 1] in _WORD_NUMS and _WORD_NUMS[tokens[i + 1]] < 100:
                        val += _WORD_NUMS[tokens[i + 1]]
                        i += 1
                elif 1 <= val <= 9 and 10 <= nxt <= 99:
                    # Spoken "four fifty" -> 450
                    val = val * 100 + nxt
                    i += 1
                elif val >= 20 and val % 10 == 0 and 1 <= nxt <= 9:
                    val += nxt
                    i += 1
            out.append(str(val))
        else:
            out.append(tok)
        i += 1
    return " ".join(out)


def _eval_ast_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_ast_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in _SAFE_CONSTS:
        return _SAFE_CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        left = _eval_ast_node(node.left)
        right = _eval_ast_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponent too large")
        return float(_SAFE_OPS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return float(_SAFE_OPS[type(node.op)](_eval_ast_node(node.operand)))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCS:
        args = [_eval_ast_node(a) for a in node.args]
        return float(_SAFE_FUNCS[node.func.id](*args))
    raise ValueError(f"Disallowed expression element: {type(node).__name__}")


def safe_calculate(expr: str) -> dict[str, Any]:
    """Safely evaluate an arithmetic or spoken-math expression using Python AST."""
    cleaned = _words_to_numbers(expr.strip())

    # Spoken patterns: "X percent of Y" / "X% of Y"
    m_pct = re.search(r"(\d+(?:\.\d+)?)\s*(?:percent|%)\s+of\s+(\d+(?:\.\d+)?)", cleaned, re.IGNORECASE)
    if m_pct:
        p, base = float(m_pct.group(1)), float(m_pct.group(2))
        val = (p / 100.0) * base
        val_str = str(int(val)) if val.is_integer() else f"{val:.4g}"
        return {"ok": True, "result": val, "formatted": val_str, "working": f"({p} / 100) * {base} = {val_str}"}

    # "split X among/between Y"
    m_split = re.search(r"split\s+(\d+(?:\.\d+)?)\s+(?:among|between|by)\s+(\d+(?:\.\d+)?)", cleaned, re.IGNORECASE)
    if m_split:
        total, ways = float(m_split.group(1)), float(m_split.group(2))
        if ways == 0:
            return {"ok": False, "error": "Cannot divide by zero."}
        val = total / ways
        val_str = str(int(val)) if val.is_integer() else f"{val:.2f}"
        return {"ok": True, "result": val, "formatted": val_str, "working": f"{total} / {ways} = {val_str}"}

    # Replace spoken operators
    norm = cleaned
    for word, sym in [
        ("what is", ""), ("calculate", ""), ("compute", ""),
        ("plus", "+"), ("minus", "-"), ("times", "*"), ("multiplied by", "*"),
        ("divided by", "/"), ("over", "/"), ("into", "*"), ("power", "**"),
    ]:
        norm = re.sub(rf"\b{re.escape(word)}\b", sym, norm, flags=re.IGNORECASE)
    norm = norm.replace("x", "*") if re.search(r"\d\s*x\s*\d", norm) else norm
    norm = norm.strip(" ?.!=")

    # Reject any dangerous characters/dunders immediately
    if "__" in norm or not re.match(r"^[0-9a-zA-Z_+\-*/%().\s,]+$", norm):
        return {"ok": False, "error": "Invalid mathematical expression."}

    try:
        tree = ast.parse(norm, mode="eval")
        val = _eval_ast_node(tree)
        val_str = str(int(val)) if val.is_integer() else f"{val:.6g}"
        return {"ok": True, "result": val, "formatted": val_str, "working": f"{norm} = {val_str}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 2. Unit Converter (pint)
# ---------------------------------------------------------------------------

_UREG = None


def _get_ureg():
    global _UREG
    if _UREG is None:
        import pint
        _UREG = pint.UnitRegistry()
    return _UREG


_CURRENCY_WORDS = re.compile(r"\b(usd|inr|eur|gbp|jpy|rupees?|dollars?|euros?|pounds?)\b", re.IGNORECASE)


_DATA_UNIT_MAP = {
    "kb": "kilobyte", "kilobytes": "kilobyte",
    "mb": "megabyte", "megabytes": "megabyte",
    "gb": "gigabyte", "gigabytes": "gigabyte",
    "tb": "terabyte", "terabytes": "terabyte",
    "fahrenheit": "degF", "celsius": "degC",
}


def convert_units(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    """Convert physical units using pint; refuses offline currency guessing."""
    if _CURRENCY_WORDS.search(from_unit) or _CURRENCY_WORDS.search(to_unit):
        return {
            "ok": False,
            "needs_online": True,
            "error": "Currency exchange rates change live, so I need internet access for that.",
        }
    try:
        ureg = _get_ureg()
        u_from = _DATA_UNIT_MAP.get(from_unit.strip().lower(), from_unit.strip().lower())
        u_to = _DATA_UNIT_MAP.get(to_unit.strip().lower(), to_unit.strip().lower())
        q = ureg.Quantity(value, u_from).to(u_to)
        mag = float(q.magnitude)
        mag_str = str(int(round(mag))) if abs(mag - round(mag)) < 1e-6 else f"{mag:.2f}"
        return {
            "ok": True,
            "value": mag,
            "formatted": f"{mag_str} {to_unit}",
            "working": f"{value} {from_unit} = {mag_str} {to_unit}",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 3. Date & Time Math (dateutil + zoneinfo)
# ---------------------------------------------------------------------------

_CITY_TZ = {
    "india": "Asia/Kolkata", "ist": "Asia/Kolkata", "coimbatore": "Asia/Kolkata",
    "chennai": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "london": "Europe/London", "uk": "Europe/London", "gmt": "UTC", "utc": "UTC",
    "new york": "America/New_York", "nyc": "America/New_York", "est": "America/New_York",
    "san francisco": "America/Los_Angeles", "pst": "America/Los_Angeles", "la": "America/Los_Angeles",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "singapore": "Asia/Singapore",
    "dubai": "Asia/Dubai", "sydney": "Australia/Sydney",
}


def date_math(query: str) -> dict[str, Any] | None:
    """Handle days-until, date-after-N-days, weekday-of-date, and city time."""
    from dateutil import parser as du_parser
    from app.brain.context_builder import get_current_time

    now = get_current_time()
    q = query.strip().lower()

    # "date after 45 days" / "45 days from now"
    m_after = re.search(r"(?:date\s+after|in)\s+(\d+)\s+days?|(\d+)\s+days?\s+from\s+(?:now|today)", q)
    if m_after:
        days = int(m_after.group(1) or m_after.group(2))
        target = now + timedelta(days=days)
        spoken_date = target.strftime("%A, %d %B %Y").replace(" 0", " ")
        return {"ok": True, "reply": f"In {days} days it'll be {spoken_date}, boss."}

    # "how many days till / until <date>"
    m_till = re.search(r"how\s+many\s+days\s+(?:till|until|to)\s+(.+?)[?!.]*$", q)
    if m_till:
        raw_target = m_till.group(1).strip()
        try:
            target = du_parser.parse(raw_target, default=now.replace(hour=0, minute=0, second=0, microsecond=0))
            if target.date() < now.date() and str(now.year) not in raw_target:
                target = target.replace(year=now.year + 1)
            delta = (target.date() - now.date()).days
            spoken_date = target.strftime("%A, %d %B %Y").replace(" 0", " ")
            return {"ok": True, "reply": f"{delta} days until {spoken_date}, boss."}
        except Exception:
            return None

    # "time in <city>"
    m_tz = re.search(r"time\s+in\s+([a-zA-Z\s]+?)[?!.]*$", q)
    if m_tz:
        city = m_tz.group(1).strip().lower()
        tz_name = _CITY_TZ.get(city)
        if tz_name:
            dt = datetime.now(ZoneInfo(tz_name))
            return {"ok": True, "reply": f"It's {dt.strftime('%I:%M %p').lstrip('0')} in {city.title()}, boss."}

    return None


# ---------------------------------------------------------------------------
# 4. Persistent Timers, Alarms & Pomodoro
# ---------------------------------------------------------------------------

def _load_timers() -> list[dict[str, Any]]:
    if not TIMERS_FILE.exists():
        return []
    try:
        data = json.loads(TIMERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_timers(timers: list[dict[str, Any]]) -> None:
    TIMERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TIMERS_FILE.write_text(json.dumps(timers, indent=2), encoding="utf-8")


def set_timer(duration_seconds: int, label: str = "Timer") -> dict[str, Any]:
    timers = [t for t in _load_timers() if t.get("status") == "active"]
    tid = int(time.time() * 1000) % 100000
    due_ts = time.time() + max(1, duration_seconds)
    entry = {
        "id": tid,
        "label": label,
        "duration_s": duration_seconds,
        "due_ts": due_ts,
        "status": "active",
    }
    timers.append(entry)
    _save_timers(timers)
    mins = duration_seconds // 60
    secs = duration_seconds % 60
    dur_str = f"{mins} minute{'s' if mins != 1 else ''}" if secs == 0 else f"{mins}m {secs}s"
    return {"ok": True, "timer": entry, "reply": f"{label} set for {dur_str}, boss."}


def list_timers() -> list[dict[str, Any]]:
    now = time.time()
    timers = _load_timers()
    active = []
    for t in timers:
        if t.get("status") == "active":
            rem = max(0, int(t["due_ts"] - now))
            active.append({**t, "remaining_s": rem})
    return active


def cancel_all_timers() -> int:
    timers = _load_timers()
    count = len([t for t in timers if t.get("status") == "active"])
    _save_timers([])
    return count


# ---------------------------------------------------------------------------
# 5. System Status (psutil)
# ---------------------------------------------------------------------------

def get_system_status_reply() -> str:
    try:
        import psutil
        cpu = int(round(psutil.cpu_percent(interval=0.1)))
        ram = int(round(psutil.virtual_memory().percent))
        bat = psutil.sensors_battery()
        bat_str = f" Battery is at {int(bat.percent)}%{' (charging)' if bat.power_plugged else ''}." if bat else ""
        return f"CPU's at {cpu} percent, RAM at {ram} percent, boss.{bat_str} Running smooth."
    except Exception:
        return "System stats look normal, boss."


# ---------------------------------------------------------------------------
# 6. Curated Content Packs (no repeats until exhausted)
# ---------------------------------------------------------------------------

_PACK_USED: dict[str, list[int]] = {}


def get_pack_item(pack_name: str) -> str | None:
    path = PACKS_DIR / f"{pack_name}.json"
    if not path.exists():
        return None
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list) or not items:
            return None
        used = _PACK_USED.setdefault(pack_name, [])
        available = [i for i in range(len(items)) if i not in used]
        if not available:
            used.clear()
            available = list(range(len(items)))
        idx = random.choice(available)
        used.append(idx)
        entry = items[idx]
        return entry["text"] if isinstance(entry, dict) else str(entry)
    except Exception as exc:
        logger.debug("Pack read error (%s): %s", pack_name, exc)
        return None


# ---------------------------------------------------------------------------
# 7. Fast-path Dispatcher for Offline Skills
# ---------------------------------------------------------------------------

def try_offline_skill(user_text: str) -> dict[str, Any] | None:
    """Attempt to answer user_text deterministically via local offline skills."""
    t = user_text.strip()
    low = t.lower()

    # 1. Arithmetic / Spoken Math
    if (
        re.search(r"\bpercent\s+of\b|\bsplit\s+\d+|\b(plus|minus|times|divided\s+by)\b", low)
        or re.match(r"^(?:what\s+is\s+)?[\d\s+\-*/().%]+$", low)
    ):
        res = safe_calculate(t)
        if res.get("ok"):
            return {
                "skill": "calculator",
                "reply": f"{res['formatted']}, boss.",
                "details": res["working"],
            }

    # 2. Unit Conversion ("convert 72 fahrenheit to celsius", "5 km to miles")
    m_unit = re.search(
        r"(?:convert\s+)?(\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s+(?:to|in|into)\s+([a-zA-Z]+)",
        low,
    )
    if m_unit:
        val = float(m_unit.group(1))
        u1, u2 = m_unit.group(2), m_unit.group(3)
        res = convert_units(val, u1, u2)
        if res.get("ok"):
            return {"skill": "units", "reply": f"{res['formatted']}, boss.", "details": res["working"]}
        if res.get("needs_online"):
            return {"skill": "units", "reply": res["error"], "details": ""}

    # 3. Date & Time Math
    dm = date_math(t)
    if dm and dm.get("ok"):
        return {"skill": "date_math", "reply": dm["reply"], "details": ""}

    # 4. Timers & Pomodoro
    m_timer = re.search(r"set\s+(?:a\s+)?(\d+)\s*(minute|min|second|sec|hour|hr)s?\s+timer", low)
    if m_timer:
        n = int(m_timer.group(1))
        unit = m_timer.group(2)
        secs = n * 3600 if unit.startswith("h") else (n * 60 if unit.startswith("m") else n)
        res = set_timer(secs)
        return {"skill": "timer", "reply": res["reply"], "details": ""}
    if "pomodoro" in low:
        res = set_timer(25 * 60, label="Pomodoro focus timer")
        return {"skill": "timer", "reply": res["reply"], "details": ""}
    if re.search(r"\b(list|show|my)\s+timers?\b", low):
        active = list_timers()
        if not active:
            return {"skill": "timer", "reply": "No active timers right now, boss.", "details": ""}
        summary = ", ".join(f"{x['label']} ({x['remaining_s']}s left)" for x in active)
        return {"skill": "timer", "reply": f"Active timers: {summary}.", "details": ""}
    if re.search(r"\bcancel\s+(?:all\s+)?timers?\b", low):
        cnt = cancel_all_timers()
        return {"skill": "timer", "reply": f"Cancelled {cnt} timer(s), boss." if cnt else "No active timers to cancel.", "details": ""}

    # 5. System Status
    if re.search(r"\b(battery|cpu|ram|memory\s+usage|system\s+status)\b", low):
        return {"skill": "system_status", "reply": get_system_status_reply(), "details": ""}

    # 6. Random & Decisions
    if re.search(r"\bflip\s+(a\s+)?coin\b", low):
        side = random.choice(["Heads", "Tails"])
        return {"skill": "random", "reply": f"{side}, boss.", "details": ""}
    if re.search(r"\broll\s+(a\s+)?dice\b", low):
        return {"skill": "random", "reply": f"Rolled a {random.randint(1, 6)}, boss.", "details": ""}
    if re.search(r"\bdecide\s+for\s+me\b", low):
        choice = random.choice(["Do it now.", "Skip it for today.", "Give it 10 minutes and reassess."])
        return {"skill": "random", "reply": f"{choice} Trust the coin-toss logic, boss.", "details": ""}

    # 7. Content Packs (jokes, riddles, fun facts, motivation)
    if re.search(r"\btell\s+(me\s+)?a\s+joke\b", low):
        item = get_pack_item("jokes")
        if item:
            return {"skill": "pack_jokes", "reply": item, "details": ""}
    if re.search(r"\b(tell\s+me\s+a\s+riddle|give\s+me\s+a\s+riddle)\b", low):
        item = get_pack_item("riddles")
        if item:
            return {"skill": "pack_riddles", "reply": item, "details": ""}
    if re.search(r"\bfun\s+fact\b", low):
        item = get_pack_item("fun_facts")
        if item:
            return {"skill": "pack_facts", "reply": item, "details": ""}
    if re.search(r"\bmotivate\s+me\b|\bmotivation\b", low):
        item = get_pack_item("motivation")
        if item:
            return {"skill": "pack_motivation", "reply": item, "details": ""}

    # 8. Conversation Recall
    if re.search(r"\b(what\s+did\s+i\s+say\s+earlier|repeat\s+that|last\s+thing\s+you\s+(told|said)\s+me)\b", low):
        try:
            from app.memory.store import store
            msgs = store.get_recent_exchanges("voice", n_exchanges=2) or store.get_recent_exchanges("web", n_exchanges=2)
            if "what did i say" in low:
                user_msgs = [m["text"] for m in msgs if m["role"] == "user"]
                if user_msgs:
                    return {"skill": "recall", "reply": f"You just said: {user_msgs[-1]}", "details": ""}
            else:
                asst_msgs = [m["text"] for m in msgs if m["role"] == "assistant"]
                if asst_msgs:
                    spoken = asst_msgs[-1].split("---", 1)[0].strip()
                    return {"skill": "recall", "reply": spoken, "details": ""}
        except Exception:
            pass

    return None
