"""app/voice/tts.py

Text-to-speech (TTS) interfaces and multi-provider implementations.

Features:
  • EdgeTTS: High-quality online neural voices (Tamil: ta-IN-PallaviNeural, English: en-IN-NeerjaNeural).
  • PiperTTS: Ultra-fast local offline TTS for English.
  • Auto-fallback: Transparently switches from EdgeTTS to Piper when offline.
  • Text sanitization: Strips markdown, emojis, code blocks, and formatting before speech.
  • Automatic language voice switching: Detects Tamil unicode script and routes to Tamil voice.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.config import (
    BASE_DIR,
    TTS_ENGINE,
    TTS_VOICE,
    TTS_VOICE_TA,
)

logger = logging.getLogger("nalla.voice.tts")

# Tamil unicode block range: U+0B80 to U+0BFF
_TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]")

# Emoji detection regex range
_EMOJI_RE = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]",
    flags=re.UNICODE,
)


def sanitize_text_for_speech(text: str) -> str:
    """Sanitize and clean text for natural audio speech synthesis."""
    if not text:
        return ""

    # 1. Strip fenced code blocks
    cleaned = re.sub(r"```[\s\S]*?```", " [code block omitted] ", text)

    # 2. Strip inline code
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)

    # 3. Strip markdown links: [label](url) -> label
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)

    # 4. Strip markdown bold / italic / headers / bullet marks
    cleaned = re.sub(r"[*_~#]+", "", cleaned)
    cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.MULTILINE)

    # 5. Remove emojis
    cleaned = _EMOJI_RE.sub("", cleaned)

    # 6. Normalize whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


class TextToSpeechProvider(ABC):
    """Abstract interface for speech synthesis engines."""

    @abstractmethod
    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Synthesize text into audio bytes (MP3 or WAV)."""
        ...

    @property
    @abstractmethod
    def is_online(self) -> bool:
        """Return True if this provider requires an active internet connection."""
        ...


class EdgeTTS(TextToSpeechProvider):
    """Online Microsoft Edge neural TTS with Tamil and English voices."""

    def __init__(
        self,
        voice_en: str | None = None,
        voice_ta: str | None = None,
    ) -> None:
        self.voice_en = voice_en or TTS_VOICE
        self.voice_ta = voice_ta or TTS_VOICE_TA

    @property
    def is_online(self) -> bool:
        return True

    def _select_voice(self, text: str, requested_voice: str | None = None) -> str:
        if requested_voice:
            return requested_voice
        # If text contains Tamil characters, switch to Tamil voice
        if _TAMIL_RE.search(text):
            return self.voice_ta
        return self.voice_en

    async def _synthesize_async(self, text: str, voice: str) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Synchronously synthesize text to MP3 audio bytes."""
        text = sanitize_text_for_speech(text)
        if not text:
            return b""

        selected_voice = self._select_voice(text, voice)
        logger.debug("EdgeTTS synthesizing with voice '%s': %s", selected_voice, text[:60])

        try:
            # Run in new or current asyncio loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        return pool.submit(asyncio.run, self._synthesize_async(text, selected_voice)).result(timeout=15.0)
                return loop.run_until_complete(self._synthesize_async(text, selected_voice))
            except RuntimeError:
                return asyncio.run(self._synthesize_async(text, selected_voice))
        except Exception as exc:
            logger.warning("EdgeTTS synthesis failed: %s", exc)
            raise RuntimeError(f"EdgeTTS network error: {exc}") from exc


class PiperTTS(TextToSpeechProvider):
    """Offline local neural TTS using Piper."""

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_dir = BASE_DIR / "data" / "models" / "piper"
        self.model_path = model_path or (self.model_dir / "en_US-lessac-medium.onnx")
        self._voice = None

    @property
    def is_online(self) -> bool:
        return False

    def _get_voice(self):
        if self._voice is None:
            if not self.model_path.exists():
                logger.warning(
                    "Piper voice model not found at %s. (Using synthetic offline fallback).",
                    self.model_path,
                )
                return None
            try:
                from piper import PiperVoice
                self._voice = PiperVoice.load(str(self.model_path))
                logger.info("Piper voice loaded from %s", self.model_path)
            except Exception as exc:
                logger.error("Failed to load Piper voice: %s", exc)
                return None
        return self._voice

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Synthesize text into WAV audio bytes offline."""
        text = sanitize_text_for_speech(text)
        if not text:
            return b""

        voice_obj = self._get_voice()
        if voice_obj is not None:
            try:
                wav_io = io.BytesIO()
                with wave.open(wav_io, "wb") as wav_file:
                    voice_obj.synthesize(text, wav_file)
                return wav_io.getvalue()
            except Exception as exc:
                logger.error("Piper synthesis failed: %s", exc)

        # Fallback offline tone/beep or silence if model not installed
        logger.info("Piper model not present; generating minimal offline confirmation tone.")
        return _generate_offline_beep_wav()


def _generate_offline_beep_wav() -> bytes:
    """Generate a clean 200ms confirmation beep in WAV format for offline mode."""
    import math
    sample_rate = 16000
    duration = 0.2
    freq = 600.0
    num_samples = int(sample_rate * duration)
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(num_samples):
            # Soft sine wave envelope
            t = float(i) / sample_rate
            env = math.sin(math.pi * (i / num_samples))
            sample = int(32767.0 * 0.3 * env * math.sin(2.0 * math.pi * freq * t))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wf.writeframes(bytes(frames))
    return wav_io.getvalue()


class TTSManager(TextToSpeechProvider):
    """Facade that provides primary TTS with automatic offline fallback."""

    def __init__(
        self,
        primary_engine: str | None = None,
        edge_provider: EdgeTTS | None = None,
        piper_provider: PiperTTS | None = None,
    ) -> None:
        self.primary_engine = (primary_engine or TTS_ENGINE).lower()
        self.edge = edge_provider or EdgeTTS()
        self.piper = piper_provider or PiperTTS()

    @property
    def is_online(self) -> bool:
        return self.edge.is_online

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Synthesize text, automatically falling back to offline Piper if EdgeTTS fails."""
        cleaned = sanitize_text_for_speech(text)
        if not cleaned:
            return b""

        if self.primary_engine == "piper":
            return self.piper.synthesize(cleaned, voice)

        # Default: attempt online EdgeTTS first
        try:
            return self.edge.synthesize(cleaned, voice)
        except Exception as exc:
            logger.warning("Online EdgeTTS failed (%s) — falling back to offline Piper TTS.", exc)
            return self.piper.synthesize(cleaned, voice)


# Module-level default TTS instance
tts_engine = TTSManager()
