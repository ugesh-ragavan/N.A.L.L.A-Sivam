"""app/perf.py

Performance telemetry ring-buffer for NALLA SIVAM.

Records per-turn timing spans and exposes them via GET /api/perf.
Thread-safe. Zero overhead when no consumer is attached.

Spans captured per turn:
  stt_ms          — faster-whisper transcription
  memory_ms       — SQLite memory search
  prompt_build_ms — persona + tool schema assembly
  llm_first_ms    — time-to-first-token from Gemini
  llm_total_ms    — full LLM response (including tool calls)
  tool_calls_ms   — total time spent inside all tool calls
  tts_first_ms    — time from speech-end to first audio byte from TTS
  total_ms        — entire turn, end-to-end
  fast_path       — True when the fast-path router handled the turn
  model           — which model was used
  user_text       — first 60 chars of the user utterance
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any


_MAX_RECORDS = 50


@dataclass
class TurnRecord:
    """Timing record for one complete voice/chat turn."""
    turn_id: int
    ts: float = field(default_factory=time.time)
    user_text: str = ""
    fast_path: bool = False
    model: str = ""
    stt_ms: float = 0.0
    memory_ms: float = 0.0
    prompt_build_ms: float = 0.0
    llm_first_ms: float = 0.0
    llm_total_ms: float = 0.0
    tool_calls_ms: float = 0.0
    tts_first_ms: float = 0.0
    total_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts_human"] = time.strftime("%H:%M:%S", time.localtime(self.ts))
        return d


class PerfTracker:
    """Thread-safe ring-buffer of turn performance records."""

    def __init__(self, maxlen: int = _MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._records: deque[TurnRecord] = deque(maxlen=maxlen)
        self._counter = 0

    def new_turn(self, user_text: str = "") -> TurnRecord:
        """Create and register a new TurnRecord. Caller fills in spans."""
        with self._lock:
            self._counter += 1
            rec = TurnRecord(turn_id=self._counter, user_text=user_text[:60])
            self._records.append(rec)
        return rec

    def records(self) -> list[dict[str, Any]]:
        """Return all stored records as dicts, newest last."""
        with self._lock:
            return [r.to_dict() for r in self._records]

    def summary_table(self) -> str:
        """ASCII table for logging — BEFORE/AFTER comparison."""
        recs = self.records()
        if not recs:
            return "No turns recorded yet."
        hdr = (
            f"{'ID':>3} {'User text':<30} {'FP':<4} "
            f"{'STT':>6} {'Mem':>5} {'Prompt':>7} "
            f"{'LLM1st':>7} {'LLMtot':>7} {'Tools':>6} "
            f"{'TTS1st':>7} {'Total':>7}"
        )
        sep = "-" * len(hdr)
        rows = [hdr, sep]
        for r in recs:
            rows.append(
                f"{r['turn_id']:>3} {r['user_text']:<30} {'Y' if r['fast_path'] else 'N':<4} "
                f"{r['stt_ms']:>6.0f} {r['memory_ms']:>5.0f} {r['prompt_build_ms']:>7.0f} "
                f"{r['llm_first_ms']:>7.0f} {r['llm_total_ms']:>7.0f} {r['tool_calls_ms']:>6.0f} "
                f"{r['tts_first_ms']:>7.0f} {r['total_ms']:>7.0f}"
            )
        rows.append(sep)
        # Averages
        def avg(key: str) -> float:
            vals = [r[key] for r in recs if r[key] > 0]
            return sum(vals) / len(vals) if vals else 0.0
        rows.append(
            f"{'AVG':>3} {'(ms across all turns)':<30} {'':<4} "
            f"{avg('stt_ms'):>6.0f} {avg('memory_ms'):>5.0f} {avg('prompt_build_ms'):>7.0f} "
            f"{avg('llm_first_ms'):>7.0f} {avg('llm_total_ms'):>7.0f} {avg('tool_calls_ms'):>6.0f} "
            f"{avg('tts_first_ms'):>7.0f} {avg('total_ms'):>7.0f}"
        )
        return "\n".join(rows)


# Global singleton
perf = PerfTracker()
