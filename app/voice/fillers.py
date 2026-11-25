"""app/voice/fillers.py

Thinking Sounds & Natural Filler System (Prompt H4).

Plays short, natural filler audio ("hmm", "let me think", "on it, boss", "seri")
ONLY when an online LLM answer is taking noticeable time (> T1, default 700ms),
with:
  - Rolling median latency predictor per (model, profile)
  - Strict exclusions (distress/crisis, Quick mode, Very short verbosity, local/scenario)
  - 8-turn anti-repetition across categories
  - Voice-hash keyed WAV cache in data/fillers/<voice_hash>/<category>/
  - Perceived vs real latency metrics
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import statistics
import time
from collections import deque
from pathlib import Path
from typing import Any

from app import config

logger = logging.getLogger("nalla.voice.fillers")

_DATA_DIR = getattr(config, "DATA_DIR", config.BASE_DIR / "data")
FILLERS_DIR = _DATA_DIR / "fillers"

DEFAULT_FILLER_PHRASES: dict[str, list[str]] = {
    "thinking": [
        "Hmm.",
        "Hmm, let me think.",
        "Okay, so.",
        "Yeah, one sec.",
        "Let me see.",
        "Right, give me a moment.",
        "Let me check that.",
        "Hmm, interesting.",
    ],
    "acknowledge": ["Yeah.", "Right.", "Got it.", "Okay.", "Sure."],
    "working": ["On it, boss.", "Checking.", "Give me a second.", "Digging into that."],
    "long_wait": ["Still on it, boss.", "This one's a bit heavy, bear with me."],
    "tanglish": ["Seri.", "Hmm, paakalam.", "Oru nimisham boss.", "Yosikkiren."],
    "reaction": ["Ooh.", "Ha.", "Interesting."],
}


def get_active_voice_hash() -> str:
    key = f"{getattr(config, 'FORCE_TTS_ENGINE', 'auto')}:{getattr(config, 'TTS_VOICE', 'default')}:{getattr(config, 'TTS_VOICE_TA', 'default')}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


class FillerManager:
    """Manages filler selection, latency prediction, anti-repetition, and WAV cache."""

    def __init__(self) -> None:
        self._latency_samples: dict[str, deque[float]] = {}
        self._recent_phrases: deque[str] = deque(maxlen=8)
        self._recent_turn_played: deque[bool] = deque(maxlen=2)
        self._usage_counts: dict[str, int] = {}
        self._total_turns: int = 0
        self._filler_turns: int = 0
        self._perceived_latencies: deque[float] = deque(maxlen=100)
        self._real_latencies: deque[float] = deque(maxlen=100)

    def record_latency(self, model: str, profile: str, first_token_ms: float) -> None:
        key = f"{model}:{profile}"
        buf = self._latency_samples.setdefault(key, deque(maxlen=30))
        buf.append(float(first_token_ms))

    def predict_first_token_ms(self, model: str, profile: str) -> float:
        key = f"{model}:{profile}"
        buf = self._latency_samples.get(key)
        if not buf:
            # Conservative default > 600ms so T1 timer is armed on cold start
            return 900.0
        return float(statistics.median(buf))

    def get_t1_seconds(self) -> float | None:
        style = getattr(config, "THINKING_STYLE", "natural").lower()
        if style == "off":
            return None
        if style == "subtle":
            return 1.5
        if style == "chatty":
            return 0.45
        return 0.7

    def should_play_filler(
        self,
        *,
        is_crisis: bool = False,
        is_local: bool = False,
        is_offline: bool = False,
        model: str = "default",
        profile: str = "chitchat",
    ) -> tuple[bool, str]:
        """Decide whether a thinking filler is allowed for this turn."""
        if is_crisis:
            return False, "crisis_guard"
        if is_local:
            return False, "local_instant"
        if is_offline:
            return False, "offline_mode"
        if getattr(config, "QUICK_MODE", False):
            return False, "quick_mode"
        if str(getattr(config, "PERSONALITY_VERBOSITY", "normal")).lower() == "very short":
            return False, "very_short_verbosity"
        if getattr(config, "THINKING_STYLE", "natural").lower() == "off":
            return False, "thinking_style_off"
        if len(self._recent_turn_played) == 2 and all(self._recent_turn_played):
            return False, "recent_turns_cap"
        if self.predict_first_token_ms(model, profile) < 600.0:
            return False, "predicted_fast"
        return True, "ok"

    def choose_category(self, profile: str, is_tanglish: bool = False) -> str:
        if is_tanglish:
            return "tanglish"
        if profile in ("task_command",):
            return "working"
        if profile in ("chitchat", "quick_fact"):
            return random.choice(["acknowledge", "thinking"])
        return "thinking"

    def pick_phrase(self, category: str) -> str:
        pool = DEFAULT_FILLER_PHRASES.get(category, DEFAULT_FILLER_PHRASES["thinking"])
        fresh = [p for p in pool if p not in self._recent_phrases]
        if not fresh:
            fresh = pool
        choice = random.choice(fresh)
        self._recent_phrases.append(choice)
        self._usage_counts[choice] = self._usage_counts.get(choice, 0) + 1
        return choice

    def record_turn_metrics(
        self,
        played: bool,
        perceived_ms: float | None = None,
        real_ms: float | None = None,
    ) -> None:
        self._total_turns += 1
        if played:
            self._filler_turns += 1
        self._recent_turn_played.append(played)
        if perceived_ms is not None:
            self._perceived_latencies.append(perceived_ms)
        if real_ms is not None:
            self._real_latencies.append(real_ms)

    def get_metrics(self) -> dict[str, Any]:
        share = round((self._filler_turns / self._total_turns) * 100.0, 1) if self._total_turns else 0.0
        med_perceived = round(statistics.median(self._perceived_latencies), 1) if self._perceived_latencies else 0.0
        med_real = round(statistics.median(self._real_latencies), 1) if self._real_latencies else 0.0
        return {
            "total_turns": self._total_turns,
            "filler_turns": self._filler_turns,
            "filler_share_pct": share,
            "median_perceived_ms": med_perceived,
            "median_real_ms": med_real,
            "voice_hash": get_active_voice_hash(),
            "usage_counts": dict(self._usage_counts),
        }

    def get_cached_wav_path(self, category: str, phrase: str) -> Path:
        vh = get_active_voice_hash()
        slug = hashlib.md5(phrase.encode("utf-8")).hexdigest()[:8]
        return FILLERS_DIR / vh / category / f"{slug}.wav"

    def ensure_filler_audio(self, tts_engine: Any, category: str, phrase: str) -> bytes:
        """Load cached WAV for active voice or synthesize & cache it."""
        path = self.get_cached_wav_path(category, phrase)
        if path.exists():
            try:
                return path.read_bytes()
            except Exception:
                pass
        if tts_engine is None:
            return b""
        try:
            audio = tts_engine.synthesize(phrase)
            if audio:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(audio)
            return audio or b""
        except Exception as exc:
            logger.debug("Filler synthesis failed for '%s': %s", phrase, exc)
            return b""

    def list_library(self) -> dict[str, Any]:
        vh = get_active_voice_hash()
        base = FILLERS_DIR / vh
        items: list[dict[str, Any]] = []
        for cat, phrases in DEFAULT_FILLER_PHRASES.items():
            for p in phrases:
                wav = self.get_cached_wav_path(cat, p)
                items.append({
                    "category": cat,
                    "phrase": p,
                    "cached": wav.exists(),
                    "usage_count": self._usage_counts.get(p, 0),
                })
        return {"voice_hash": vh, "items": items, "metrics": self.get_metrics()}


filler_manager = FillerManager()
