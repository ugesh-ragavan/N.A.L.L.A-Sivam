"""app/voice/stt.py

Speech-to-text (STT) interface and faster-whisper implementation.

Features:
  • Runs on CPU with int8 quantization.
  • Base model by default, configurable to small.
  • Language auto-detection, English ("en"), or Tamil ("ta").
  • Outro hallucination cleaner (filtering common Whisper artifacts).
  • Normalized confidence estimation.
"""

from __future__ import annotations

import io
import logging
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.config import BASE_DIR, WHISPER_BEAM_SIZE, WHISPER_LANGUAGE, WHISPER_MODEL

logger = logging.getLogger("nalla.voice.stt")

# Regex to remove subtitle artifacts and YouTube outro hallucinations
_HALLUCINATION_PATTERNS = [
    re.compile(r"\[\s*(music|applause|laughter|silence|sound|inaudible)[^\]]*\]", re.IGNORECASE),
    re.compile(r"\(\s*(music|applause|laughter|silence|sound|inaudible)[^\)]*\)", re.IGNORECASE),
    re.compile(r"thank\s+you\s+for\s+watching", re.IGNORECASE),
    re.compile(r"thanks\s+for\s+watching", re.IGNORECASE),
    re.compile(r"subscribe\s+to\s+(the|my|our)\s+channel", re.IGNORECASE),
    re.compile(r"please\s+like\s+and\s+subscribe", re.IGNORECASE),
]


def clean_transcription_text(text: str) -> str:
    """Strip common Whisper hallucinations and bracketed annotations."""
    cleaned = text
    for pattern in _HALLUCINATION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass
class STTResult:
    """Structured return value from speech-to-text recognition."""
    text: str
    confidence: float
    language: str
    duration: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not bool(self.text.strip())


class SpeechToTextProvider(ABC):
    """Abstract interface for speech-to-text engines."""

    @abstractmethod
    def transcribe(
        self,
        audio_bytes: bytes,
        language: str | None = None,
    ) -> STTResult:
        """Transcribe PCM16 16kHz audio bytes to text."""
        ...


class FasterWhisperSTT(SpeechToTextProvider):
    """Local offline speech-to-text engine using faster-whisper on CPU."""

    def __init__(
        self,
        model_size: str | None = None,
        language: str | None = None,
        download_root: Path | None = None,
    ) -> None:
        self.model_size = model_size or WHISPER_MODEL
        self.language = language or WHISPER_LANGUAGE
        self.download_root = download_root or (BASE_DIR / "data" / "models" / "whisper")
        self.beam_size = WHISPER_BEAM_SIZE
        self._model = None  # Lazy loaded

    def warmup(self) -> None:
        """Pre-load Whisper model into memory to eliminate first-turn latency."""
        self._get_model()

    def _get_model(self):
        """Lazy loader for WhisperModel to prevent startup lag."""
        if self._model is None:
            from faster_whisper import WhisperModel
            self.download_root.mkdir(parents=True, exist_ok=True)
            logger.info(
                "Loading faster-whisper '%s' model (int8 on CPU)...",
                self.model_size,
            )
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
                download_root=str(self.download_root),
                cpu_threads=4,
            )
            logger.info("Whisper model loaded successfully.")
        return self._model

    def transcribe(
        self,
        audio_bytes: bytes,
        language: str | None = None,
    ) -> STTResult:
        """Transcribe 16 kHz 16-bit PCM mono audio bytes."""
        if not audio_bytes:
            return STTResult(text="", confidence=0.0, language=self.language or "auto")

        try:
            model = self._get_model()

            # Convert 16-bit PCM to float32 array in range [-1.0, 1.0]
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            lang = language or self.language
            lang_param = None if lang in ("auto", "", None) else lang

            segments, info = model.transcribe(
                audio_array,
                language=lang_param,
                beam_size=self.beam_size,
                vad_filter=False,
            )

            text_parts = []
            logprobs = []
            for seg in segments:
                text_parts.append(seg.text)
                if hasattr(seg, "avg_logprob") and seg.avg_logprob is not None:
                    logprobs.append(seg.avg_logprob)

            raw_text = "".join(text_parts).strip()
            cleaned_text = clean_transcription_text(raw_text)

            # Compute normalized confidence from average logprob
            confidence = 0.85
            if logprobs:
                avg_lp = sum(logprobs) / len(logprobs)
                confidence = max(0.0, min(1.0, 1.0 + (avg_lp / 2.5)))

            detected_lang = getattr(info, "language", lang_param or "en")
            duration = getattr(info, "duration", len(audio_bytes) / 32000.0)

            logger.info(
                "STT transcribed: '%s' (lang: %s, conf: %.2f)",
                cleaned_text,
                detected_lang,
                confidence,
            )
            return STTResult(
                text=cleaned_text,
                confidence=confidence,
                language=detected_lang,
                duration=duration,
            )

        except Exception as exc:
            logger.error("FasterWhisperSTT transcription error: %s", exc)
            return STTResult(text="", confidence=0.0, language="unknown")


# Module-level default STT provider
stt_engine: SpeechToTextProvider = FasterWhisperSTT()
