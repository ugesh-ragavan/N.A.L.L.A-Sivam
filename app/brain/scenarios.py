"""app/brain/scenarios.py

Human Mode Scenario Engine & Crisis Guard (Prompts H1 & H2).

Sits BEFORE the LLM in the fast-path pipeline:
  1. Crisis Guard (always runs first) — detects distress, self-harm, grief,
     medical emergency, and danger in English, Tanglish, and Tamil script.
     Never returns canned jokes or fillers; routes to careful online reply or
     static supportive offline message.
  2. Tiered Scenario Matching:
       - Tier 1 (< 5 ms): normalised exact match + regex
       - Tier 2 (< 20 ms): rapidfuzz fuzzy scoring with strictness thresholds
       - Tier 3 (optional, < 60 ms): lightweight char/token n-gram cosine similarity
  3. Real-data Slot Resolution + Variant Anti-Repetition (last 20 variants, last 5 openers)
  4. Multi-turn Follow-up State (20s TTL) + Mood Flag
  5. Local Miss Log (capped at 500 entries)
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from rapidfuzz import fuzz as _rf_fuzz
except ImportError:
    _rf_fuzz = None

from app import config

logger = logging.getLogger("nalla.brain.scenarios")

_DATA_DIR = getattr(config, "DATA_DIR", config.BASE_DIR / "data")
SCENARIOS_ACTIVE_DIR = _DATA_DIR / "scenarios" / "active"
SCENARIOS_DRAFTS_DIR = _DATA_DIR / "scenarios" / "drafts"
TANGLISH_VARIANTS_PATH = _DATA_DIR / "tanglish_variants.json"
MISS_LOG_PATH = _DATA_DIR / "miss_log.json"

_PROCESS_START_TS = time.time()
_REQUESTS_TODAY_COUNT = 0
_CRASHES_TODAY_COUNT = 0
_SYS_CACHE: dict[str, Any] = {"ts": 0.0, "battery": None, "wifi_status": None, "capabilities": None}

# ---------------------------------------------------------------------------
# Crisis Guard (Prompt H1.6)
# ---------------------------------------------------------------------------

# Idiomatic tech / casual near-misses that must NOT trigger Crisis Guard
_CRISIS_NEAR_MISS_RE = re.compile(
    r"\b("
    r"(this|the|that|my)\s+(bug|error|code|issue|query|test|script|build|server|deadline|assignment|exam|traffic|heat|monday)\s+(is\s+)?(killing|murdering)\s+me"
    r"|kill\s+(the\s+)?(server|process|task|pid|port|thread|job|container|app|script)"
    r"|dead\s+(battery|laptop|phone|screen|pixel|link|end|code|lock|deadlock|tired)"
    r"|(code|server|app|script|battery|phone|laptop)\s+(died|is\s+dead|crashed)"
    r"|fatal\s+(error|exception|crash)"
    r"|dying\s+(of\s+laughter|to\s+(know|see|try|eat|sleep)|laughing)"
    r"|heart\s+attack\s+(from|over)\s+(this|the|that)\s+(bug|error|bill|exam|deadline)"
    r"|suicide\s+squad"
    r"|kill\s+two\s+birds"
    r"|killing\s+time"
    r"|drop\s+dead\s+gorgeous"
    r")\b",
    re.IGNORECASE,
)

# True distress / self-harm / grief / medical emergency / danger patterns
_CRISIS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("self_harm", re.compile(
        r"\b("
        r"kill\s+myself|want\s+to\s+die|wanna\s+die|wish\s+i\s+was\s+dead|wish\s+i\s+were\s+dead"
        r"|end\s+my\s+life|end\s+it\s+all|commit\s+suicide|\bsuicide\b|suicidal|self[\s-]harm|hurt\s+myself"
        r"|cut\s+myself|no\s+reason\s+to\s+live|better\s+off\s+dead|can'?t\s+go\s+on\s+living"
        r"|overdose|hang\s+myself|jump\s+off\s+(a\s+)?(bridge|building|roof)"
        r"|saaganum\s+pola|vaazha\s+pidikkala|uyira\s+vidaporen|en\s+uyira"
        r")\b"
        r"|சாகணும்|தற்கொலை|வாழ\s*பிடிக்கல",
        re.IGNORECASE,
    )),
    ("hopelessness", re.compile(
        r"\b("
        r"no\s+hope\s+left|completely\s+hopeless|nothing\s+matters\s+anymore|nobody\s+cares\s+about\s+me"
        r"|i\s+am\s+(a\s+)?burden|everyone\s+would\s+be\s+better\s+without\s+me"
        r"|i\s+can'?t\s+take\s+(this|it)\s+anymore|broken\s+beyond\s+repair"
        r"|yaarum\s+illa\s+enaku|vazhkaiye\s+veruthu"
        r")\b",
        re.IGNORECASE,
    )),
    ("grief", re.compile(
        r"\b("
        r"my\s+(mom|mother|dad|father|brother|sister|friend|wife|husband|son|daughter|grandma|grandpa|pet|dog|cat)\s+(just\s+)?(died|passed\s+away|is\s+gone)"
        r"|lost\s+my\s+(mom|mother|dad|father|brother|sister|best\s+friend|partner)"
        r"|funeral\s+today|grieving\s+the\s+loss"
        r"|amma\s+irandhutaanga|appa\s+irandhutaanga"
        r")\b",
        re.IGNORECASE,
    )),
    ("medical_emergency", re.compile(
        r"\b("
        r"having\s+a\s+(heart\s+attack|stroke|seizure)|can'?t\s+breathe|difficulty\s+breathing"
        r"|chest\s+pain\s+(right\s+)?now|bleeding\s+(heavily|badly|nonstop)|unconscious"
        r"|call\s+(an\s+)?ambulance|medical\s+emergency|swallowed\s+poison"
        r"|moochu\s+vidamudiyala|nenju\s+valikkudhu"
        r")\b"
        r"|நெஞ்சு\s*வலிக்குது|மூச்சு\s*விட\s*முடியல",
        re.IGNORECASE,
    )),
    ("danger", re.compile(
        r"\b("
        r"someone\s+is\s+(breaking\s+in|chasing\s+me|attacking\s+me|threatening\s+to\s+kill)"
        r"|being\s+(abused|assaulted|stalked)|domestic\s+violence|in\s+immediate\s+danger"
        r"|house\s+is\s+on\s+fire|gas\s+leak\s+in\s+(my|the)\s+house"
        r")\b",
        re.IGNORECASE,
    )),
]


@dataclass
class CrisisResult:
    is_crisis: bool
    category: str = ""
    matched_pattern: str = ""


def check_crisis(text: str) -> CrisisResult:
    """Check if text indicates distress, self-harm, grief, medical emergency, or danger."""
    if not text or not text.strip():
        return CrisisResult(is_crisis=False)
    cleaned = text.strip()
    if _CRISIS_NEAR_MISS_RE.search(cleaned):
        return CrisisResult(is_crisis=False)
    for category, pat in _CRISIS_PATTERNS:
        m = pat.search(cleaned)
        if m:
            return CrisisResult(is_crisis=True, category=category, matched_pattern=m.group(0))
    return CrisisResult(is_crisis=False)


def get_crisis_offline_reply(category: str = "") -> str:
    """Static supportive message for offline crisis fallback (never a joke)."""
    helpline = getattr(config, "VERIFIED_HELPLINE_NUMBERS", "").strip()
    base = (
        "I'm really sorry you're going through this. Please reach out to someone you trust "
        "right now — a close friend, family member, or a local professional."
    )
    if category == "medical_emergency" or category == "danger":
        base = (
            "This sounds urgent. Please contact local emergency services or a trusted person "
            "near you right away."
        )
    if helpline:
        return f"{base} Verified helpline(s): {helpline}."
    return base


# ---------------------------------------------------------------------------
# Normalisation & Tanglish Variants
# ---------------------------------------------------------------------------

_FILLER_WORDS = {"uh", "um", "umm", "er", "ah", "like", "pls", "please", "hey", "yo"}
_PUNCT_RE = re.compile(r"[^\w\s\u0B80-\u0BFF]", re.UNICODE)
_REPEAT_CHAR_RE = re.compile(r"(.)\1{2,}")


def load_tanglish_variants() -> dict[str, str]:
    if not TANGLISH_VARIANTS_PATH.exists():
        return {}
    try:
        data = json.loads(TANGLISH_VARIANTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k).lower(): str(v).lower() for k, v in data.items()}
    except Exception as exc:
        logger.debug("Could not load tanglish_variants.json: %s", exc)
    return {}


def normalise_text(text: str, variants: dict[str, str] | None = None, strip_fillers: bool = True) -> str:
    """Normalise user text for scenario matching."""
    t = text.lower().strip()
    # Replace hyphens inside Tanglish suffixes like "tired-a" -> "tired a"
    t = t.replace("-", " ")
    # Collapse 3+ repeated letters ("heyyyy" -> "heyy", "soooo" -> "soo")
    t = _REPEAT_CHAR_RE.sub(r"\1\1", t)
    # Strip punctuation while keeping Tamil script & alphanumeric
    t = _PUNCT_RE.sub(" ", t)
    tokens = [tok for tok in t.split() if tok]
    if strip_fillers and len(tokens) > 1:
        filtered = [tok for tok in tokens if tok not in _FILLER_WORDS]
        if filtered:
            tokens = filtered
    if variants:
        tokens = [variants.get(tok, tok) for tok in tokens]
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Real-data Slot Resolver
# ---------------------------------------------------------------------------

def _format_uptime(seconds: float) -> str:
    mins = int(seconds // 60)
    hrs = mins // 60
    rem_mins = mins % 60
    if hrs > 0:
        return f"{hrs}h {rem_mins}m"
    return f"{max(1, mins)}m"


def resolve_slots(extra_slots: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build real-data slots for scenario reply templates."""
    global _REQUESTS_TODAY_COUNT
    from app.brain.context_builder import get_current_time, get_time_band

    now = get_current_time()
    time_band = get_time_band(now)
    slots: dict[str, Any] = {
        "time": now.strftime("%I:%M %p").lstrip("0"),
        "date": now.strftime("%d %B %Y").lstrip("0"),
        "weekday": now.strftime("%A"),
        "time_band": time_band,
        "owner_name": "boss",
        "requests_today": _REQUESTS_TODAY_COUNT,
        "uptime": _format_uptime(time.time() - _PROCESS_START_TS),
        "crashes_today": _CRASHES_TODAY_COUNT,
        "mode": getattr(config, "PERSONALITY_MODE", "Engineer"),
        "current_voice": getattr(config, "TTS_VOICE", "default"),
    }

    # Tasks & reminders from SQLite store
    try:
        from app.memory.store import store
        open_tasks = store.list_tasks(status="open", limit=10)
        slots["tasks_open"] = len(open_tasks)
        slots["next_task"] = open_tasks[0]["title"] if open_tasks else None
        done_tasks = store.list_tasks(status="done", limit=20)
        slots["tasks_done_today"] = len(done_tasks)
        reminders = store.get_reminders(include_completed=False)
        slots["reminders_today"] = len(reminders)
    except Exception:
        pass

    # System battery / wifi (cached for 30s so Tier 1 stays < 5 ms)
    now_ts = time.time()
    if now_ts - _SYS_CACHE["ts"] > 30.0:
        try:
            import psutil
            bat = psutil.sensors_battery()
            _SYS_CACHE["battery"] = f"{int(bat.percent)}%" if bat is not None else None
            stats = psutil.net_if_stats()
            wifi_up = any(
                v.isup and ("wi-fi" in k.lower() or "wlan" in k.lower() or "wireless" in k.lower())
                for k, v in stats.items()
            )
            _SYS_CACHE["wifi_status"] = "connected" if wifi_up else "offline"
        except Exception:
            pass
        _SYS_CACHE["ts"] = now_ts
    if _SYS_CACHE["battery"] is not None:
        slots["battery"] = _SYS_CACHE["battery"]
    if _SYS_CACHE["wifi_status"] is not None:
        slots["wifi_status"] = _SYS_CACHE["wifi_status"]

    # Capabilities summary built directly from actual tool registry (cached)
    if _SYS_CACHE["capabilities"] is None:
        try:
            from app.brain.tools import registry
            tool_names = [t.name for t in registry.list_tools()]
            groups = []
            if any("task" in n for n in tool_names):
                groups.append("tasks")
            if any("reminder" in n for n in tool_names):
                groups.append("reminders")
            if any("remember" in n or "recall" in n for n in tool_names):
                groups.append("memory")
            if any("note" in n for n in tool_names):
                groups.append("notes")
            if any("browser" in n for n in tool_names):
                groups.append("web browsing")
            if "capture_screen" in tool_names:
                groups.append("screen help")
            if any("calc" in n or "convert" in n or "timer" in n for n in tool_names):
                groups.append("offline maths, conversions & timers")
            _SYS_CACHE["capabilities"] = ", ".join(groups) if groups else "chat, memory, and voice"
        except Exception:
            _SYS_CACHE["capabilities"] = "chat, memory, and tasks"
    slots["capabilities_summary"] = _SYS_CACHE["capabilities"]

    if extra_slots:
        slots.update(extra_slots)
    return slots


def _render_reply(template: str, needs: list[str], slots: dict[str, Any]) -> str | None:
    """Render reply template if all required slots are present and non-empty."""
    for req in needs:
        val = slots.get(req)
        if val is None or val == "":
            return None
    try:
        return template.format(**slots)
    except KeyError:
        return None


# ---------------------------------------------------------------------------
# Miss Log (local, capped at 500 entries)
# ---------------------------------------------------------------------------

def log_miss(utterance: str, route: str = "llm") -> None:
    if not getattr(config, "MISS_LOG_ENABLED", True):
        return
    text = utterance.strip()
    if not text or len(text) < 2:
        return
    try:
        entries: list[dict[str, Any]] = []
        if MISS_LOG_PATH.exists():
            entries = json.loads(MISS_LOG_PATH.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                entries = []
        entries.append({
            "text": text[:300],
            "route": route,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })
        if len(entries) > 500:
            entries = entries[-500:]
        MISS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        MISS_LOG_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("Failed to log unmatched utterance: %s", exc)


def get_miss_log() -> list[dict[str, Any]]:
    if not MISS_LOG_PATH.exists():
        return []
    try:
        data = json.loads(MISS_LOG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def clear_miss_log() -> int:
    count = len(get_miss_log())
    if MISS_LOG_PATH.exists():
        MISS_LOG_PATH.unlink()
    return count


# ---------------------------------------------------------------------------
# Scenario Match Result & Engine
# ---------------------------------------------------------------------------

@dataclass
class ScenarioMatch:
    scenario_id: str
    category: str
    reply: str
    tier: int
    confidence: float
    latency_ms: float
    matched_trigger: str
    tone: str = "friendly"
    followup_expect: str | None = None
    followup_ttl_s: float = 20.0
    actions: list[dict[str, Any]] = field(default_factory=list)


def _cosine_ngram(a: str, b: str, n: int = 3) -> float:
    """Lightweight CPU-only character n-gram cosine similarity for Tier 3."""
    if not a or not b:
        return 0.0
    a_pad = f" {a} "
    b_pad = f" {b} "
    va = Counter(a_pad[i:i + n] for i in range(max(1, len(a_pad) - n + 1)))
    vb = Counter(b_pad[i:i + n] for i in range(max(1, len(b_pad) - n + 1)))
    dot = sum(va[k] * vb[k] for k in va if k in vb)
    if dot == 0:
        return 0.0
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    return (dot / (na * nb)) * 100.0 if na and nb else 0.0


class ScenarioEngine:
    """Loads YAML scenarios, performs tiered matching, and tracks anti-repetition."""

    STRICTNESS_THRESHOLDS = {
        "strict": 90.0,
        "balanced": 82.0,
        "loose": 74.0,
    }

    def __init__(self, active_dir: Path | None = None) -> None:
        self.active_dir = active_dir or SCENARIOS_ACTIVE_DIR
        self.scenarios: list[dict[str, Any]] = []
        self.variants_map: dict[str, str] = {}
        self.conflicts: list[dict[str, Any]] = []
        self._recent_variants: deque[str] = deque(maxlen=20)
        self._recent_openers: deque[str] = deque(maxlen=5)
        self._last_fired: dict[str, float] = {}
        self._active_followup: dict[str, Any] | None = None
        self.last_user_mood: str | None = None
        self.tier3_enabled: bool = False
        self.reload()

    def reload(self) -> int:
        self.variants_map = load_tanglish_variants()
        loaded: list[dict[str, Any]] = []
        if self.active_dir.exists():
            for path in sorted(self.active_dir.glob("*.yaml")):
                try:
                    items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict) and item.get("id") and item.get("triggers") and item.get("replies"):
                                loaded.append(self._prepare_scenario(item, path.name))
                except Exception as exc:
                    logger.warning("Failed to load scenario file %s: %s", path.name, exc)
        self.scenarios = loaded
        self.conflicts = self._detect_conflicts()
        return len(self.scenarios)

    def _prepare_scenario(self, item: dict[str, Any], source_file: str) -> dict[str, Any]:
        triggers_raw = item.get("triggers", {})
        all_phrases: list[str] = []
        regex_patterns: list[re.Pattern[str]] = []
        for lang_key in ("en", "tanglish", "ta", "patterns"):
            vals = triggers_raw.get(lang_key, []) or []
            for v in vals:
                s = str(v).strip()
                if not s:
                    continue
                if s.startswith("re:") or lang_key == "patterns":
                    pat_str = s[3:] if s.startswith("re:") else s
                    try:
                        regex_patterns.append(re.compile(pat_str, re.IGNORECASE))
                    except re.error:
                        pass
                else:
                    all_phrases.append(s)

        norm_phrases = [
            (p, normalise_text(p, self.variants_map))
            for p in all_phrases
        ]
        return {
            **item,
            "_source": source_file,
            "_norm_phrases": norm_phrases,
            "_regexes": regex_patterns,
        }

    def _detect_conflicts(self) -> list[dict[str, Any]]:
        seen: dict[str, str] = {}
        conflicts: list[dict[str, Any]] = []
        for sc in self.scenarios:
            sid = sc["id"]
            group = sc.get("followup_group", "")
            for raw, norm in sc["_norm_phrases"]:
                key = f"{group}::{norm}"
                if key in seen and seen[key] != sid:
                    conflicts.append({
                        "trigger": raw,
                        "normalised": norm,
                        "scenario_a": seen[key],
                        "scenario_b": sid,
                    })
                else:
                    seen[key] = sid
        return conflicts

    def register_runtime_scenario(self, scenario_dict: dict[str, Any]) -> None:
        """Dynamically register an offline skill or runtime scenario."""
        sid = scenario_dict.get("id")
        self.scenarios = [s for s in self.scenarios if s.get("id") != sid]
        self.scenarios.append(self._prepare_scenario(scenario_dict, "runtime"))

    def clear_followup(self) -> None:
        self._active_followup = None

    def _check_conditions(self, cond: dict[str, Any] | None, slots: dict[str, Any]) -> bool:
        if not cond:
            return True
        if "time_band" in cond and cond["time_band"] != slots.get("time_band"):
            return False
        if "mode" in cond and str(cond["mode"]).lower() != str(slots.get("mode", "")).lower():
            return False
        if "requests_today_max" in cond:
            if int(slots.get("requests_today", 0)) > int(cond["requests_today_max"]):
                return False
        if "mood" in cond and cond["mood"] != self.last_user_mood:
            return False
        return True

    def _select_reply(self, scenario: dict[str, Any], slots: dict[str, Any]) -> tuple[str, str] | None:
        """Filter replies by conditions & slots, weight by personality, avoid repetition."""
        candidates: list[tuple[str, str, float]] = []
        sarcasm = getattr(config, "PERSONALITY_SARCASM", 5)
        warmth = getattr(config, "PERSONALITY_WARMTH", 6)
        tamil_mix = getattr(config, "PERSONALITY_TAMIL_MIX", 25)

        for r in scenario.get("replies", []):
            if not self._check_conditions(r.get("conditions"), slots):
                continue
            rendered = _render_reply(r["text"], r.get("needs", []) or [], slots)
            if not rendered:
                continue
            tone = r.get("tone", "friendly")
            w = float(r.get("weight", 1.0))
            if tone == "dry":
                w *= 0.6 + (sarcasm / 10.0)
            elif tone == "warm":
                w *= 0.6 + (warmth / 10.0)
            elif tone == "tanglish":
                w *= 0.5 + (tamil_mix / 50.0)
            candidates.append((rendered, tone, max(0.1, w)))

        if not candidates:
            return None

        # Anti-repetition: avoid last 20 variants and last 5 opening words
        fresh = [
            c for c in candidates
            if c[0] not in self._recent_variants
            and (c[0].split()[0].lower().strip(".,!?") not in self._recent_openers if c[0].split() else True)
        ]
        if not fresh:
            # Relax opener constraint, keep immediate repeat guard
            last_one = self._recent_variants[-1] if self._recent_variants else None
            fresh = [c for c in candidates if c[0] != last_one]
        if not fresh:
            fresh = candidates

        texts = [c[0] for c in fresh]
        tones = [c[1] for c in fresh]
        weights = [c[2] for c in fresh]
        chosen_idx = random.choices(range(len(fresh)), weights=weights, k=1)[0]
        chosen_text = texts[chosen_idx]
        chosen_tone = tones[chosen_idx]

        self._recent_variants.append(chosen_text)
        if chosen_text.split():
            self._recent_openers.append(chosen_text.split()[0].lower().strip(".,!?"))
        return chosen_text, chosen_tone

    def match(
        self,
        user_text: str,
        strictness: str | None = None,
        extra_slots: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> ScenarioMatch | None:
        """Match user_text against active scenarios across Tiers 1-3."""
        global _REQUESTS_TODAY_COUNT
        t0 = time.perf_counter()

        if not getattr(config, "SCENARIO_ENGINE_ENABLED", True) and not dry_run:
            return None

        norm_input = normalise_text(user_text, self.variants_map)
        if not norm_input:
            return None

        now_ts = time.time()
        slots = resolve_slots(extra_slots)

        # Check active follow-up group first if within TTL
        active_group = None
        if self._active_followup and now_ts <= self._active_followup["expires_at"]:
            active_group = self._active_followup["expect"]
        elif self._active_followup:
            self._active_followup = None

        candidates_pool = self.scenarios
        if active_group:
            followup_pool = [s for s in self.scenarios if s.get("followup_group") == active_group]
            hit = self._match_pool(user_text, norm_input, followup_pool, strictness, now_ts, slots, t0)
            if hit:
                self._active_followup = None
                if not dry_run:
                    _REQUESTS_TODAY_COUNT += 1
                    self._apply_actions(hit.actions)
                return hit
            # Drop out of follow-up mode if answer doesn't fit
            self._active_followup = None

        # Normal pool (exclude followup_group-only scenarios)
        normal_pool = [s for s in candidates_pool if not s.get("followup_group")]
        hit = self._match_pool(user_text, norm_input, normal_pool, strictness, now_ts, slots, t0)
        if hit and not dry_run:
            _REQUESTS_TODAY_COUNT += 1
            self._last_fired[hit.scenario_id] = now_ts
            self._apply_actions(hit.actions)
            if hit.followup_expect:
                self._active_followup = {
                    "expect": hit.followup_expect,
                    "expires_at": now_ts + hit.followup_ttl_s,
                }
        return hit

    def _apply_actions(self, actions: list[dict[str, Any]]) -> None:
        for act in actions:
            if "set_mood" in act:
                self.last_user_mood = str(act["set_mood"])

    def _match_pool(
        self,
        raw_text: str,
        norm_input: str,
        pool: list[dict[str, Any]],
        strictness: str | None,
        now_ts: float,
        slots: dict[str, Any],
        t0: float,
    ) -> ScenarioMatch | None:
        eff_strict = strictness or getattr(config, "SCENARIO_STRICTNESS", "balanced")
        threshold = self.STRICTNESS_THRESHOLDS.get(eff_strict, 82.0)

        # Filter eligible scenarios (conditions + cooldown)
        eligible: list[dict[str, Any]] = []
        for sc in pool:
            cd = float(sc.get("cooldown_s", 0))
            if cd > 0 and (now_ts - self._last_fired.get(sc["id"], 0.0)) < cd:
                continue
            if not self._check_conditions(sc.get("conditions"), slots):
                continue
            eligible.append(sc)

        # --- TIER 1: Normalised exact match or regex (< 5 ms) ---
        tier1_hits: list[tuple[dict[str, Any], str, float]] = []
        for sc in eligible:
            for raw_trig, norm_trig in sc["_norm_phrases"]:
                if norm_input == norm_trig:
                    tier1_hits.append((sc, raw_trig, 100.0))
                    break
            else:
                for pat in sc["_regexes"]:
                    if pat.search(raw_text) or pat.search(norm_input):
                        tier1_hits.append((sc, pat.pattern, 100.0))
                        break

        if tier1_hits:
            tier1_hits.sort(key=lambda x: (x[0].get("priority", 0), len(x[1])), reverse=True)
            best_sc, trig, conf = tier1_hits[0]
            return self._build_match(best_sc, trig, 1, conf, slots, t0)

        # --- TIER 2: RapidFuzz fuzzy keyword scoring (< 20 ms) ---
        tier2_hits: list[tuple[dict[str, Any], str, float]] = []
        for sc in eligible:
            best_score = 0.0
            best_trig = ""
            for raw_trig, norm_trig in sc["_norm_phrases"]:
                if _rf_fuzz is not None:
                    score = float(_rf_fuzz.ratio(norm_input, norm_trig))
                    if eff_strict != "strict" and abs(len(norm_input.split()) - len(norm_trig.split())) <= 1:
                        score = max(score, float(_rf_fuzz.token_sort_ratio(norm_input, norm_trig)))
                else:
                    score = _cosine_ngram(norm_input, norm_trig)
                if score > best_score:
                    best_score = score
                    best_trig = raw_trig
            if best_score >= threshold:
                tier2_hits.append((sc, best_trig, best_score))

        if tier2_hits:
            tier2_hits.sort(key=lambda x: (x[2], x[0].get("priority", 0), len(x[1])), reverse=True)
            if len(tier2_hits) > 1 and abs(tier2_hits[0][2] - tier2_hits[1][2]) < 2.0:
                logger.info(
                    "Scenario tie/conflict: %s (%.1f) vs %s (%.1f) for '%s'",
                    tier2_hits[0][0]["id"], tier2_hits[0][2],
                    tier2_hits[1][0]["id"], tier2_hits[1][2],
                    raw_text[:40],
                )
            best_sc, trig, conf = tier2_hits[0]
            return self._build_match(best_sc, trig, 2, round(conf, 1), slots, t0)

        # --- TIER 3 (optional): Local character n-gram similarity (< 60 ms) ---
        if self.tier3_enabled:
            tier3_threshold = max(threshold, 85.0)
            tier3_hits: list[tuple[dict[str, Any], str, float]] = []
            for sc in eligible:
                best_score = 0.0
                best_trig = ""
                for raw_trig, norm_trig in sc["_norm_phrases"]:
                    score = _cosine_ngram(norm_input, norm_trig)
                    if score > best_score:
                        best_score = score
                        best_trig = raw_trig
                if best_score >= tier3_threshold:
                    tier3_hits.append((sc, best_trig, best_score))
            if tier3_hits:
                tier3_hits.sort(key=lambda x: (x[2], x[0].get("priority", 0)), reverse=True)
                best_sc, trig, conf = tier3_hits[0]
                return self._build_match(best_sc, trig, 3, round(conf, 1), slots, t0)

        return None

    def _build_match(
        self,
        sc: dict[str, Any],
        trig: str,
        tier: int,
        conf: float,
        slots: dict[str, Any],
        t0: float,
    ) -> ScenarioMatch | None:
        sel = self._select_reply(sc, slots)
        if sel is None:
            return None
        reply_text, tone = sel
        fu = sc.get("followup") or {}
        return ScenarioMatch(
            scenario_id=sc["id"],
            category=sc.get("category", "general"),
            reply=reply_text,
            tier=tier,
            confidence=conf,
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            matched_trigger=trig,
            tone=tone,
            followup_expect=fu.get("expect"),
            followup_ttl_s=float(fu.get("ttl_s", 20.0)),
            actions=sc.get("actions", []) or [],
        )


# Module-level singleton
scenario_engine = ScenarioEngine()
