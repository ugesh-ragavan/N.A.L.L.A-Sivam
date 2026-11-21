"""app/voice/tts.py

Text-to-speech (TTS) interfaces and multi-provider implementations.

Features:
  • EdgeTTS: High-quality online neural voices (Tamil: ta-IN-PallaviNeural, English: en-IN-NeerjaNeural).
  • PiperTTS: Ultra-fast local offline TTS for English.
  • Auto-fallback: Transparently switches from EdgeTTS to Piper when offline.
  • Text sanitization: Strips markdown, emojis, code blocks, and formatting before speech.
  • Dynamic voice discovery: Discovers all EdgeTTS voices and Piper models without hardcoding.
  • Voice Profile support: Speaking rate, pitch shift, and volume controls.
  • Bilingual sentence switching: Routes Tamil text to Tamil voice and English text to English voice.
  • Instant Voice Preview helper.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import re
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from app.config import (
    BASE_DIR,
    TTS_ENGINE,
    TTS_VOICE,
    TTS_VOICE_TA,
    get_active_voice_profile,
)

logger = logging.getLogger("nalla.voice.tts")

# Tamil unicode block range: U+0B80 to U+0BFF
_TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]")

# Emoji detection regex range
_EMOJI_RE = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]",
    flags=re.UNICODE,
)

# In-memory cache for discovered voices
_VOICE_CACHE: dict[str, list[dict[str, Any]]] | None = None


# ---------------------------------------------------------------------------
# Format Helpers for EdgeTTS rate, pitch, and volume
# ---------------------------------------------------------------------------

def format_edge_rate(rate: float | int | str | None) -> str:
    """Format speech rate for edge-tts (e.g. '+0%', '+15%', '-10%')."""
    if rate is None:
        return "+0%"
    if isinstance(rate, str):
        rate = rate.strip()
        if rate.endswith("%"):
            return rate if (rate.startswith("+") or rate.startswith("-")) else f"+{rate}"
        try:
            rate_val = float(rate)
        except ValueError:
            return "+0%"
    else:
        rate_val = float(rate)

    pct = int(round((rate_val - 1.0) * 100)) if rate_val < 5.0 else int(round(rate_val))
    return f"{pct:+d}%"


def format_edge_pitch(pitch: float | int | str | None) -> str:
    """Format pitch shift for edge-tts (e.g. '+0Hz', '+5Hz', '-5Hz')."""
    if pitch is None:
        return "+0Hz"
    if isinstance(pitch, str):
        pitch = pitch.strip()
        if pitch.endswith("Hz"):
            return pitch if (pitch.startswith("+") or pitch.startswith("-")) else f"+{pitch}"
        try:
            pitch_val = float(pitch)
        except ValueError:
            return "+0Hz"
    else:
        pitch_val = float(pitch)

    return f"{int(round(pitch_val)):+d}Hz"


def format_edge_volume(volume: float | int | str | None) -> str:
    """Format volume for edge-tts relative to 100% (e.g. '+0%', '-20%')."""
    if volume is None:
        return "+0%"
    if isinstance(volume, str):
        volume = volume.strip()
        if volume.endswith("%"):
            return volume if (volume.startswith("+") or volume.startswith("-")) else f"+{volume}"
        try:
            vol_val = float(volume)
        except ValueError:
            return "+0%"
    else:
        vol_val = float(volume)

    # 100 is nominal 0% shift. 80 is -20%.
    if vol_val <= 100.0:
        shift = int(round(vol_val - 100.0))
        return f"{shift:+d}%"
    shift = int(round(vol_val - 100.0))
    return f"{shift:+d}%"


# ---------------------------------------------------------------------------
# Text Sanitization
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Dynamic Voice Discovery
# ---------------------------------------------------------------------------

async def discover_voices(force_refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Dynamically discover available voices from edge-tts and local Piper folder.

    Groups voices by language category:
      - 'English (India)'
      - 'English (US/UK)'
      - 'Tamil'
      - 'Piper (Offline)'
      - 'Other'
    """
    global _VOICE_CACHE
    if _VOICE_CACHE is not None and not force_refresh:
        return _VOICE_CACHE

    cache_file = BASE_DIR / "data" / "edge_voices_cache.json"
    grouped: dict[str, list[dict[str, Any]]] = {
        "English (India)": [],
        "English (US/UK)": [],
        "Tamil": [],
        "Piper (Offline)": [],
    }

    # 1. Query EdgeTTS
    edge_voices: list[dict[str, Any]] = []
    try:
        import edge_tts
        edge_voices = await edge_tts.list_voices()
        # Save cache
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(edge_voices), encoding="utf-8")
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Could not list online edge-tts voices (%s). Checking cache file.", exc)
        if cache_file.exists():
            try:
                edge_voices = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                edge_voices = []

    # If edge-tts failed completely and no cache, provide standard fallback presets
    if not edge_voices:
        edge_voices = [
            {"ShortName": "en-IN-NeerjaNeural", "Gender": "Female", "Locale": "en-IN", "FriendlyName": "Microsoft Neerja Online (Natural) - English (India)"},
            {"ShortName": "en-IN-PrabhatNeural", "Gender": "Male", "Locale": "en-IN", "FriendlyName": "Microsoft Prabhat Online (Natural) - English (India)"},
            {"ShortName": "ta-IN-PallaviNeural", "Gender": "Female", "Locale": "ta-IN", "FriendlyName": "Microsoft Pallavi Online (Natural) - Tamil (India)"},
            {"ShortName": "ta-IN-ValluvarNeural", "Gender": "Male", "Locale": "ta-IN", "FriendlyName": "Microsoft Valluvar Online (Natural) - Tamil (India)"},
            {"ShortName": "en-US-JennyNeural", "Gender": "Female", "Locale": "en-US", "FriendlyName": "Microsoft Jenny Online (Natural) - English (US)"},
            {"ShortName": "en-US-GuyNeural", "Gender": "Male", "Locale": "en-US", "FriendlyName": "Microsoft Guy Online (Natural) - English (US)"},
            {"ShortName": "en-GB-SoniaNeural", "Gender": "Female", "Locale": "en-GB", "FriendlyName": "Microsoft Sonia Online (Natural) - English (UK)"},
        ]

    for v in edge_voices:
        short_name = v.get("ShortName", "")
        locale = v.get("Locale", "")
        gender = v.get("Gender", "")
        friendly = v.get("FriendlyName", short_name)

        entry = {
            "id": short_name,
            "name": f"{short_name} ({gender})",
            "locale": locale,
            "gender": gender,
            "friendly_name": friendly,
            "engine": "edge-tts",
        }

        if locale == "en-IN":
            grouped["English (India)"].append(entry)
        elif locale in ("en-US", "en-GB"):
            grouped["English (US/UK)"].append(entry)
        elif locale.startswith("ta-") or locale == "ta":
            grouped["Tamil"].append(entry)

    # 2. Scan Piper voices directory
    piper_dir = BASE_DIR / "data" / "models" / "piper"
    if piper_dir.exists():
        for model_file in piper_dir.glob("*.onnx"):
            stem = model_file.stem
            grouped["Piper (Offline)"].append({
                "id": stem,
                "name": f"{stem} (Offline)",
                "locale": "en-US" if "en" in stem else "local",
                "gender": "Unknown",
                "friendly_name": f"Piper {stem}",
                "engine": "piper",
            })

    # If no piper files installed, provide placeholder for fallback
    if not grouped["Piper (Offline)"]:
        grouped["Piper (Offline)"].append({
            "id": "en_US-lessac-medium",
            "name": "en_US-lessac-medium (Offline)",
            "locale": "en-US",
            "gender": "Female",
            "friendly_name": "Piper en_US-lessac-medium",
            "engine": "piper",
        })

    _VOICE_CACHE = grouped
    return grouped


def get_available_voices_sync(force_refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Synchronous helper to retrieve available voices."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, discover_voices(force_refresh)).result(timeout=10.0)
    return asyncio.run(discover_voices(force_refresh))


# ---------------------------------------------------------------------------
# TTS Provider Abstract Class
# ---------------------------------------------------------------------------

class TextToSpeechProvider(ABC):
    """Abstract interface for speech synthesis engines."""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        rate: Any = None,
        pitch: Any = None,
        volume: Any = None,
    ) -> bytes:
        """Synthesize text into audio bytes (MP3 or WAV)."""
        ...

    @property
    @abstractmethod
    def is_online(self) -> bool:
        """Return True if this provider requires an active internet connection."""
        ...


# ---------------------------------------------------------------------------
# EdgeTTS Implementation
# ---------------------------------------------------------------------------

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

    def _select_voice(self, text: str, requested_voice: str | None = None, language: str | None = None) -> str:
        if requested_voice:
            return requested_voice
        # If text contains Tamil characters or language is Tamil, switch to Tamil voice
        if (language and language.lower() in ("ta", "tamil")) or _TAMIL_RE.search(text):
            return self.voice_ta
        return self.voice_en

    async def _synthesize_async(
        self,
        text: str,
        voice: str,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
    ) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
        )
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        rate: Any = None,
        pitch: Any = None,
        volume: Any = None,
    ) -> bytes:
        """Synchronously synthesize text to MP3 audio bytes with rate/pitch/volume controls."""
        text = sanitize_text_for_speech(text)
        if not text:
            return b""

        selected_voice = self._select_voice(text, requested_voice=voice, language=language)
        fmt_rate = format_edge_rate(rate)
        fmt_pitch = format_edge_pitch(pitch)
        fmt_vol = format_edge_volume(volume)

        logger.debug(
            "EdgeTTS synthesizing with voice '%s' (rate=%s, pitch=%s, vol=%s): %s",
            selected_voice, fmt_rate, fmt_pitch, fmt_vol, text[:60],
        )

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(
                        asyncio.run,
                        self._synthesize_async(text, selected_voice, fmt_rate, fmt_pitch, fmt_vol),
                    ).result(timeout=15.0)
            return asyncio.run(self._synthesize_async(text, selected_voice, fmt_rate, fmt_pitch, fmt_vol))
        except Exception as exc:
            logger.warning("EdgeTTS synthesis failed: %s", exc)
            raise RuntimeError(f"EdgeTTS network error: {exc}") from exc


# ---------------------------------------------------------------------------
# PiperTTS Implementation
# ---------------------------------------------------------------------------

class PiperTTS(TextToSpeechProvider):
    """Offline local neural TTS using Piper."""

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_dir = BASE_DIR / "data" / "models" / "piper"
        self.model_path = model_path or (self.model_dir / "en_US-lessac-medium.onnx")
        self._voice = None

    @property
    def is_online(self) -> bool:
        return False

    def _get_voice(self, voice_name: str | None = None):
        target_path = self.model_path
        if voice_name:
            cand = self.model_dir / f"{voice_name}.onnx"
            if cand.exists():
                target_path = cand

        if self._voice is None or (voice_name and target_path != self.model_path):
            if not target_path.exists():
                logger.warning(
                    "Piper voice model not found at %s. (Using synthetic offline fallback).",
                    target_path,
                )
                return None
            try:
                from piper import PiperVoice
                self._voice = PiperVoice.load(str(target_path))
                self.model_path = target_path
                logger.info("Piper voice loaded from %s", target_path)
            except Exception as exc:
                logger.error("Failed to load Piper voice: %s", exc)
                return None
        return self._voice

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        rate: Any = None,
        pitch: Any = None,
        volume: Any = None,
    ) -> bytes:
        """Synthesize text into WAV audio bytes offline."""
        text = sanitize_text_for_speech(text)
        if not text:
            return b""

        voice_obj = self._get_voice(voice)
        if voice_obj is not None:
            try:
                wav_io = io.BytesIO()
                with wave.open(wav_io, "wb") as wav_file:
                    voice_obj.synthesize(text, wav_file)
                return wav_io.getvalue()
            except Exception as exc:
                logger.error("Piper synthesis failed: %s", exc)

        # Fallback offline tone/beep if model not installed
        logger.info("Piper model not present; generating minimal offline confirmation tone.")
        return _generate_offline_beep_wav()


def _generate_offline_beep_wav() -> bytes:
    """Generate a clean 200ms confirmation beep in WAV format for offline mode."""
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
            t = float(i) / sample_rate
            env = math.sin(math.pi * (i / num_samples))
            sample = int(32767.0 * 0.3 * env * math.sin(2.0 * math.pi * freq * t))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wf.writeframes(bytes(frames))
    return wav_io.getvalue()


# ---------------------------------------------------------------------------
# TTSManager Facade
# ---------------------------------------------------------------------------

class TTSManager(TextToSpeechProvider):
    """Facade providing primary TTS with automatic offline fallback and bilingual switching."""

    def __init__(
        self,
        primary_engine: str | None = None,
        edge_provider: EdgeTTS | None = None,
        piper_provider: PiperTTS | None = None,
    ) -> None:
        self.primary_engine = (primary_engine or TTS_ENGINE).lower()
        self.edge = edge_provider or EdgeTTS()
        self.piper = piper_provider or PiperTTS()
        self._fallback_listeners: list[Callable[[str], None]] = []

    @property
    def is_online(self) -> bool:
        return self.edge.is_online

    def add_fallback_listener(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked when TTS falls back from online to offline."""
        if callback not in self._fallback_listeners:
            self._fallback_listeners.append(callback)

    def _notify_fallback(self, reason: str) -> None:
        for cb in list(self._fallback_listeners):
            try:
                cb(reason)
            except Exception as exc:
                logger.debug("Error in fallback listener: %s", exc)

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        rate: Any = None,
        pitch: Any = None,
        volume: Any = None,
    ) -> bytes:
        """Synthesize text, automatically routing language and falling back to Piper if EdgeTTS fails."""
        cleaned = sanitize_text_for_speech(text)
        if not cleaned:
            return b""

        # Resolve voice profile defaults if not explicitly passed
        active_prof = get_active_voice_profile()
        engine = (active_prof.get("engine") or self.primary_engine).lower()

        is_tamil = bool(_TAMIL_RE.search(cleaned) or (language and language.lower() in ("ta", "tamil")))

        if voice is None:
            if is_tamil:
                voice = active_prof.get("voice_ta") or TTS_VOICE_TA
            else:
                voice = active_prof.get("voice") or TTS_VOICE

        # Resolve rate, pitch, volume from active profile if not provided
        if rate is None:
            rate = active_prof.get("rate", "+0%")
        if pitch is None:
            pitch = active_prof.get("pitch", "+0Hz")
        if volume is None:
            volume = active_prof.get("volume", "+0%")

        # If primary engine is piper, run offline Piper directly
        if engine == "piper":
            return self.piper.synthesize(cleaned, voice=voice, language=language, rate=rate, pitch=pitch, volume=volume)

        # Default: attempt online EdgeTTS first
        try:
            return self.edge.synthesize(
                cleaned,
                voice=voice,
                language=language,
                rate=rate,
                pitch=pitch,
                volume=volume,
            )
        except Exception as exc:
            msg = f"EdgeTTS connection failed ({exc}). Switched automatically to offline Piper."
            logger.warning(msg)
            self._notify_fallback(msg)
            return self.piper.synthesize(cleaned, voice=voice, language=language, rate=rate, pitch=pitch, volume=volume)

    def preview(
        self,
        text: str = "Yes boss, sollunga!",
        engine: str | None = None,
        voice: str | None = None,
        rate: Any = None,
        pitch: Any = None,
        volume: Any = None,
        language: str | None = None,
    ) -> tuple[bytes, str]:
        """Synthesize a one-off preview sample and return (audio_bytes, mime_type)."""
        engine = (engine or self.primary_engine).lower()
        if engine == "piper":
            audio = self.piper.synthesize(text, voice=voice, language=language, rate=rate, pitch=pitch, volume=volume)
            return audio, "audio/wav"
        else:
            try:
                audio = self.edge.synthesize(text, voice=voice, language=language, rate=rate, pitch=pitch, volume=volume)
                return audio, "audio/mpeg"
            except Exception as exc:
                msg = f"EdgeTTS preview failed ({exc}); using offline fallback."
                self._notify_fallback(msg)
                audio = self.piper.synthesize(text, voice=voice, language=language, rate=rate, pitch=pitch, volume=volume)
                return audio, "audio/wav"


# Module-level default TTS instance
tts_engine = TTSManager()
