"""app/voice/tts_cache.py

Disk caching of pre-synthesized and frequently spoken fixed phrases.

Keyed by SHA-256 of (model, voice, style, text).
Pre-generates canonical phrases at startup:
  • "Yes boss, sollunga!"
  • "Done, boss."
  • "Sorry boss, didn't catch that."
  • "On it, boss."
  • "Mic muted, boss."
  • "Mic is live again, boss."
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

from app.config import BASE_DIR

logger = logging.getLogger("nalla.voice.tts_cache")

_DEFAULT_CACHE_DIR = BASE_DIR / "data" / "cache" / "tts"

# Fixed phrases to pre-warm
FIXED_PHRASES = [
    "Yes boss, sollunga!",
    "Done, boss.",
    "Sorry boss, didn't catch that.",
    "On it, boss.",
    "Mic muted, boss.",
    "Mic is live again, boss.",
]


def make_cache_key(model: str, voice: str, style: str, text: str) -> str:
    """Generate deterministic SHA-256 cache key."""
    norm = f"{model.lower().strip()}|{voice.lower().strip()}|{style.lower().strip()}|{text.strip()}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


class TTSAudioCache:
    """Thread-safe disk cache for TTS audio outputs."""

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, model: str, voice: str, style: str, text: str) -> bytes | None:
        """Retrieve cached audio bytes if available on disk."""
        key = make_cache_key(model, voice, style, text)
        file_path = self.cache_dir / f"{key}.wav"
        if not file_path.exists():
            self._misses += 1
            return None
        try:
            with self._lock:
                data = file_path.read_bytes()
                if len(data) > 44:  # At least valid WAV header
                    self._hits += 1
                    logger.debug("TTS Cache HIT for '%s' (key: %s...)", text[:30], key[:8])
                    return data
        except Exception as exc:
            logger.debug("Could not read TTS cache file %s: %s", file_path, exc)
        self._misses += 1
        return None

    def put(self, model: str, voice: str, style: str, text: str, audio_bytes: bytes) -> None:
        """Save audio bytes to disk cache."""
        if not audio_bytes or len(audio_bytes) < 44:
            return
        key = make_cache_key(model, voice, style, text)
        file_path = self.cache_dir / f"{key}.wav"
        try:
            with self._lock:
                file_path.write_bytes(audio_bytes)
                logger.debug("TTS Cache SAVED for '%s' (key: %s...)", text[:30], key[:8])
        except Exception as exc:
            logger.warning("Could not write TTS cache file %s: %s", file_path, exc)

    def get_stats(self) -> dict[str, Any]:
        """Return cache hit/miss statistics and cached items count."""
        with self._lock:
            cached_count = len(list(self.cache_dir.glob("*.wav")))
            return {
                "hits": self._hits,
                "misses": self._misses,
                "cached_files": cached_count,
            }

    def prewarm(self, tts_provider: Any, phrases: list[str] | None = None) -> None:
        """Pre-generate fixed phrases in a background thread."""
        to_warm = phrases or FIXED_PHRASES

        def _worker():
            logger.info("Starting background pre-warming of %d fixed TTS phrases...", len(to_warm))
            for phrase in to_warm:
                try:
                    # Calling synthesize will check cache, or generate and populate cache
                    tts_provider.synthesize(phrase)
                except Exception as exc:
                    logger.debug("TTS prewarm skipped for '%s': %s", phrase, exc)
            logger.info("Fixed TTS phrases pre-warming complete.")

        threading.Thread(target=_worker, daemon=True, name="TTSPrewarmWorker").start()


# Module-level default audio cache
tts_audio_cache = TTSAudioCache()
