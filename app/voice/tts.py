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
# Dynamic Voice Discovery (Prompt C)
# ---------------------------------------------------------------------------

def _tamil_locale_priority(locale: str | dict[str, Any]) -> int:
    """Sort priority: ta-IN (0), ta-LK (1), ta-SG (2), ta-MY (3), other ta (4)."""
    if isinstance(locale, dict):
        locale_str = locale.get("locale", "")
    else:
        locale_str = locale
    loc = (locale_str or "").lower().strip()
    if loc == "ta-in":
        return 0
    if loc == "ta-lk":
        return 1
    if loc == "ta-sg":
        return 2
    if loc == "ta-my":
        return 3
    if loc.startswith("ta-") or loc == "ta":
        return 4
    return 99


async def discover_voices(force_refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Dynamically discover available voices from edge-tts, Gemini catalog, and local Piper folder.

    Prioritizes:
      - Tamil voices: ta-IN first, then ta-LK, ta-SG, ta-MY.
      - English voices: en-IN first, then en-US, en-GB.
      - Gemini conversational voices.
      - Piper offline installed models dynamically without hardcoding.
    """
    global _VOICE_CACHE
    if _VOICE_CACHE is not None and not force_refresh:
        return _VOICE_CACHE

    cache_file = BASE_DIR / "data" / "edge_voices_cache.json"
    grouped: dict[str, list[dict[str, Any]]] = {
        "Gemini (Conversational)": [],
        "English (India)": [],
        "English (US/UK)": [],
        "Tamil": [],
        "Piper (Offline)": [],
    }

    # 0. Gemini Prebuilt Voices
    try:
        from app.voice.tts_gemini import PREBUILT_VOICES
        for gv in PREBUILT_VOICES:
            grouped["Gemini (Conversational)"].append({
                "id": gv["id"],
                "name": f"{gv['name']} ({gv['character']})",
                "locale": "auto",
                "gender": gv["gender"].title(),
                "friendly_name": f"{gv['name']} - {gv['character']}",
                "engine": "gemini",
                "recommended": gv.get("recommended", False),
                "character": gv.get("character", ""),
                "capabilities": ["en", "ta"],
            })
    except Exception as exc:
        logger.debug("Could not load Gemini prebuilt voices: %s", exc)

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
            {"ShortName": "ta-IN-PallaviNeural", "Gender": "Female", "Locale": "ta-IN", "FriendlyName": "Microsoft Pallavi Online (Natural) - Tamil (India)"},
            {"ShortName": "ta-IN-ValluvarNeural", "Gender": "Male", "Locale": "ta-IN", "FriendlyName": "Microsoft Valluvar Online (Natural) - Tamil (India)"},
            {"ShortName": "ta-LK-KumarNeural", "Gender": "Male", "Locale": "ta-LK", "FriendlyName": "Microsoft Kumar Online (Natural) - Tamil (Sri Lanka)"},
            {"ShortName": "ta-LK-SaranyaNeural", "Gender": "Female", "Locale": "ta-LK", "FriendlyName": "Microsoft Saranya Online (Natural) - Tamil (Sri Lanka)"},
            {"ShortName": "ta-SG-AnbuNeural", "Gender": "Male", "Locale": "ta-SG", "FriendlyName": "Microsoft Anbu Online (Natural) - Tamil (Singapore)"},
            {"ShortName": "ta-SG-VenbaNeural", "Gender": "Female", "Locale": "ta-SG", "FriendlyName": "Microsoft Venba Online (Natural) - Tamil (Singapore)"},
            {"ShortName": "ta-MY-KaniNeural", "Gender": "Female", "Locale": "ta-MY", "FriendlyName": "Microsoft Kani Online (Natural) - Tamil (Malaysia)"},
            {"ShortName": "ta-MY-SuryaNeural", "Gender": "Male", "Locale": "ta-MY", "FriendlyName": "Microsoft Surya Online (Natural) - Tamil (Malaysia)"},
            {"ShortName": "en-IN-NeerjaNeural", "Gender": "Female", "Locale": "en-IN", "FriendlyName": "Microsoft Neerja Online (Natural) - English (India)"},
            {"ShortName": "en-IN-PrabhatNeural", "Gender": "Male", "Locale": "en-IN", "FriendlyName": "Microsoft Prabhat Online (Natural) - English (India)"},
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

        if locale.startswith("ta-") or locale == "ta":
            entry["capabilities"] = ["ta"]
            grouped["Tamil"].append(entry)
        elif locale == "en-IN":
            entry["capabilities"] = ["en"]
            grouped["English (India)"].append(entry)
        elif locale in ("en-US", "en-GB"):
            entry["capabilities"] = ["en"]
            grouped["English (US/UK)"].append(entry)

    # Sort Tamil voices with ta-IN first, then ta-LK, ta-SG, ta-MY
    grouped["Tamil"].sort(key=lambda x: _tamil_locale_priority(x.get("locale", "")))

    # 2. Scan Piper voices directory dynamically
    piper_dir = BASE_DIR / "data" / "models" / "piper"
    if piper_dir.exists():
        for model_file in piper_dir.glob("*.onnx"):
            stem = model_file.stem
            is_ta = "ta" in stem.lower() or "tamil" in stem.lower()
            entry = {
                "id": stem,
                "name": f"{stem} (Offline)",
                "locale": "ta-IN" if is_ta else ("en-US" if "en" in stem else "local"),
                "gender": "Unknown",
                "friendly_name": f"Piper {stem}",
                "engine": "piper",
                "capabilities": ["ta"] if is_ta else ["en"],
            }
            grouped["Piper (Offline)"].append(entry)
            if is_ta:
                grouped["Tamil"].append(entry)

    # If no piper files installed, provide placeholder for fallback
    if not grouped["Piper (Offline)"]:
        grouped["Piper (Offline)"].append({
            "id": "en_US-lessac-medium",
            "name": "en_US-lessac-medium (Offline)",
            "locale": "en-US",
            "gender": "Female",
            "friendly_name": "Piper en_US-lessac-medium",
            "engine": "piper",
            "capabilities": ["en"],
        })

    # Direct convenience slot lists for Two-Slot picker
    grouped["tamil_voices"] = list(grouped["Tamil"])
    for gv in grouped["Gemini (Conversational)"]:
        grouped["tamil_voices"].append({**gv, "friendly_name": f"{gv['name']} (Gemini Multilingual)"})

    grouped["english_voices"] = (
        grouped["Gemini (Conversational)"] +
        grouped["English (India)"] +
        grouped["English (US/UK)"] +
        grouped["Piper (Offline)"]
    )

    # Convenience lowercase keys
    grouped["gemini"] = list(grouped["Gemini (Conversational)"])
    grouped["edge"] = list(grouped["English (India)"] + grouped["English (US/UK)"] + grouped["Tamil"])
    grouped["piper"] = list(grouped["Piper (Offline)"])

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
        default_en = "en-IN-NeerjaNeural"
        default_ta = "ta-IN-PallaviNeural"
        v_en = voice_en or TTS_VOICE
        self.voice_en = v_en if (v_en and "-" in v_en and not v_en.startswith("voice_")) else default_en
        v_ta = voice_ta or TTS_VOICE_TA
        self.voice_ta = v_ta if (v_ta and "-" in v_ta and not v_ta.startswith("voice_")) else default_ta

    @property
    def is_online(self) -> bool:
        return True

    def is_available(self) -> bool:
        return True

    def _select_voice(self, text: str, requested_voice: str | None = None, language: str | None = None) -> str:
        default_en = "en-IN-NeerjaNeural"
        default_ta = "ta-IN-PallaviNeural"
        if requested_voice and "-" in requested_voice and not requested_voice.startswith("voice_"):
            return requested_voice
        # If text contains Tamil characters or language is Tamil, switch to Tamil voice
        if (language and language.lower() in ("ta", "tamil")) or _TAMIL_RE.search(text):
            return self.voice_ta if ("-" in self.voice_ta) else default_ta
        return self.voice_en if ("-" in self.voice_en) else default_en

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

    def is_available(self) -> bool:
        return bool(self.piper_exe and self.model_onnx and self.model_onnx.exists())

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
# Bilingual Splitting & Audio Concatenation Helpers (Prompt C)
# ---------------------------------------------------------------------------

def _cache_audio(engine: str, voice: str, style: str | None, sentence: str, audio: bytes) -> None:
    try:
        from app.voice.tts_cache import tts_audio_cache
        tts_audio_cache.put(engine, voice, style or "", sentence, audio)
    except Exception:
        pass


def split_bilingual_sentences(text: str) -> list[tuple[str, str]]:
    """Split text into sentences/runs tagged with their detected language ('ta' or 'en')."""
    if not text:
        return []

    # Split by standard sentence terminators (.!? and newlines)
    tokens = [s.strip() for s in re.split(r"([.!?]+(?:\s+|\n+)|[\n]+)", text) if s.strip()]
    merged: list[str] = []
    buf = ""
    for token in tokens:
        if token in (".", "!", "?", "...", "?!", "!?"):
            buf = (buf + token) if buf else token
        else:
            if buf:
                merged.append(buf)
            buf = token
    if buf:
        merged.append(buf)

    if not merged:
        merged = [text.strip()]

    result: list[tuple[str, str]] = []
    for sentence in merged:
        is_ta = bool(_TAMIL_RE.search(sentence))
        result.append((sentence, "ta" if is_ta else "en"))
    return result


def combine_audio_chunks(chunks: list[bytes]) -> bytes:
    """Combine multiple audio chunks (MP3 or WAV) into a unified playable audio bytes stream."""
    valid_chunks = [c for c in chunks if c and len(c) > 0]
    if not valid_chunks:
        return b""
    if len(valid_chunks) == 1:
        return valid_chunks[0]

    # Check if all chunks are MP3 (starts with \xff\xfb, \xff\xf3, or ID3)
    all_mp3 = all(c.startswith(b"ID3") or c.startswith(b"\xff\xfb") or c.startswith(b"\xff\xf3") for c in valid_chunks)
    if all_mp3:
        return b"".join(valid_chunks)

    # Check if all chunks are WAV and have identical header parameters
    all_wav = all(c.startswith(b"RIFF") for c in valid_chunks)
    if all_wav:
        try:
            combined_pcm = bytearray()
            nchannels = 1
            sampwidth = 2
            framerate = 24000
            for c in valid_chunks:
                with wave.open(io.BytesIO(c), "rb") as wf:
                    nchannels = wf.getnchannels()
                    sampwidth = wf.getsampwidth()
                    framerate = wf.getframerate()
                    combined_pcm.extend(wf.readframes(wf.getnframes()))

            out_io = io.BytesIO()
            with wave.open(out_io, "wb") as out_wf:
                out_wf.setnchannels(nchannels)
                out_wf.setsampwidth(sampwidth)
                out_wf.setframerate(framerate)
                out_wf.writeframes(bytes(combined_pcm))
            return out_io.getvalue()
        except Exception as exc:
            logger.debug("Fast WAV merge failed (%s); falling back to array decoder.", exc)

    # Mixed formats: decode through PyAV into common PCM WAV
    try:
        from app.voice.audio_io import decode_audio_to_array
        import numpy as np

        arrays = []
        target_rate = 24000
        for c in valid_chunks:
            arr, rate = decode_audio_to_array(c)
            if len(arr) > 0:
                target_rate = rate
                arrays.append(arr)
        if not arrays:
            return b""
        combined_arr = np.concatenate(arrays, axis=0)
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            channels = 1 if combined_arr.ndim == 1 else combined_arr.shape[1]
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(target_rate)
            int16_arr = np.clip(combined_arr, -32768, 32767).astype(np.int16)
            wf.writeframes(int16_arr.tobytes())
        return wav_io.getvalue()
    except Exception as exc:
        logger.warning("Could not merge mixed audio chunks (%s); returning first valid chunk.", exc)
        return valid_chunks[0]


# ---------------------------------------------------------------------------
# TTSManager Facade (Prompt C: Two Voice Slots & Fallback Chains)
# ---------------------------------------------------------------------------

class TTSManager(TextToSpeechProvider):
    """Facade providing Two Voice Slots (English & Tamil) with per-language fallback chains.

    Voice Slots:
      - voice_slot_en: engine (Gemini > Edge-TTS > Piper), voice, rate, volume
      - voice_slot_ta: engine (Edge-TTS > Gemini), voice, rate, volume

    Hard Rules:
      - Never read Tamil text with an English-only voice.
      - Never read English text with a Tamil-only voice if an English voice is available.
      - Fallback per language:
          Tamil: preferred > other Tamil-capable engine > text-only notice.
          English: preferred > any other engine.
      - Keep Gemini TTS quota guard, fixed phrases cache, and telemetry per sentence.
    """

    def __init__(
        self,
        primary_engine: str | None = None,
        gemini_provider: Any = None,
        edge_provider: EdgeTTS | None = None,
        piper_provider: PiperTTS | None = None,
        gemini_tts: Any = None,
        edge_tts: EdgeTTS | None = None,
        piper_tts: PiperTTS | None = None,
    ) -> None:
        self._gemini = gemini_tts or gemini_provider
        self.edge = edge_tts or edge_provider or EdgeTTS()
        self.piper = piper_tts or piper_provider or PiperTTS()
        self._fallback_listeners: list[Callable[..., None]] = []
        self._last_fallback_reason: str = ""
        self._last_turn_engines: list[dict[str, Any]] = []

        if primary_engine is not None:
            self.primary_engine = primary_engine.lower()
        elif self._gemini is not None:
            self.primary_engine = "gemini"
        elif edge_provider is not None or edge_tts is not None:
            self.primary_engine = "edge-tts"
        elif piper_provider is not None or piper_tts is not None:
            self.primary_engine = "piper"
        else:
            self.primary_engine = TTS_ENGINE.lower()

    @property
    def gemini(self) -> Any:
        if self._gemini is None:
            try:
                from app.voice.tts_gemini import gemini_tts
                self._gemini = gemini_tts
            except Exception as exc:
                logger.warning("Could not initialize Gemini TTS provider: %s", exc)
        return self._gemini

    @gemini.setter
    def gemini(self, provider: Any) -> None:
        self._gemini = provider

    @property
    def is_online(self) -> bool:
        if self.primary_engine == "gemini":
            return self.gemini.is_online if self.gemini else False
        if self.primary_engine == "piper":
            return False
        return self.edge.is_online

    @property
    def last_fallback_reason(self) -> str:
        return self._last_fallback_reason

    def add_fallback_listener(self, callback: Callable[..., None]) -> None:
        """Register a callback invoked when TTS falls back to secondary engines."""
        if callback not in self._fallback_listeners:
            self._fallback_listeners.append(callback)

    def _notify_fallback(self, reason: str, primary: str = "gemini", fallback: str = "edge-tts") -> None:
        self._last_fallback_reason = reason
        for cb in list(self._fallback_listeners):
            try:
                import inspect
                sig = inspect.signature(cb)
                if len(sig.parameters) >= 3:
                    cb(primary, fallback, reason)
                else:
                    cb(reason)
            except Exception as exc:
                logger.debug("Error in fallback listener: %s", exc)

    def get_last_turn_engines(self) -> list[dict[str, Any]]:
        """Return which engine and voice actually spoke each sentence in the latest turn."""
        return list(self._last_turn_engines)

    def clear_turn_engines(self) -> None:
        """Clear the per-sentence engine telemetry for the start of a new turn."""
        self._last_turn_engines.clear()

    def _synthesize_sentence(
        self,
        sentence: str,
        target_lang: str,
        slot_cfg: dict[str, Any],
        style: str | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """Synthesize a single sentence through its language-specific fallback chain."""
        from app import config as _cfg
        forced_engine = (getattr(_cfg, "FORCE_TTS_ENGINE", "auto") or "auto").lower()

        pref_engine = (slot_cfg.get("engine") or "gemini").lower()
        if forced_engine in ("gemini", "edge-tts", "piper"):
            pref_engine = forced_engine

        pref_voice = slot_cfg.get("voice") or ("ta-IN-PallaviNeural" if target_lang == "ta" else "achird")
        rate = slot_cfg.get("rate", 1.0)
        pitch = slot_cfg.get("pitch", "+0Hz")
        volume = slot_cfg.get("volume", 100)

        # Check disk cache first for zero-latency fixed phrases and repeat replies
        try:
            from app.voice.tts_cache import tts_audio_cache
            cached_audio = tts_audio_cache.get(pref_engine, pref_voice, style or "", sentence)
            if cached_audio:
                gemini_model = getattr(self.gemini, "model", getattr(_cfg, "GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts"))
                model_name = gemini_model if pref_engine == "gemini" else ("edge-neural" if pref_engine == "edge-tts" else "piper-onnx")
                return cached_audio, {
                    "sentence": sentence,
                    "language": target_lang,
                    "engine": pref_engine,
                    "model": model_name,
                    "voice": pref_voice,
                    "status": "cached",
                    "fallback": False,
                    "fallback_from": "",
                    "notice": "",
                }
        except Exception:
            pass

        # ------------------------------------------------------------------
        # TAMIL FALLBACK CHAIN: preferred > other Tamil-capable engine > text only
        # (HARD RULE 5: Never read Tamil text with an English-only voice!)
        # ------------------------------------------------------------------
        if target_lang == "ta":
            if forced_engine == "piper":
                notice = "Piper has no Tamil voice. Hard Rule 5: displayed as text only."
                logger.warning(notice)
                return b"", {
                    "sentence": sentence,
                    "language": "ta",
                    "engine": "none",
                    "model": "none",
                    "voice": "none",
                    "status": "text_only",
                    "fallback": False,
                    "fallback_from": "",
                    "notice": notice,
                }

            if forced_engine in ("gemini", "edge-tts"):
                chain = [forced_engine]
            else:
                chain = ["edge-tts", "gemini"] if pref_engine != "gemini" else ["gemini", "edge-tts"]

            for eng in chain:
                is_fallback = (eng != (slot_cfg.get("engine") or "gemini").lower())
                fallback_from = slot_cfg.get("engine", "gemini") if is_fallback else ""

                if eng == "edge-tts":
                    try:
                        voice_to_use = pref_voice if ("-" in pref_voice and not pref_voice.startswith("voice_")) else "ta-IN-PallaviNeural"
                        audio = self.edge.synthesize(
                            sentence,
                            voice=voice_to_use,
                            language="ta",
                            rate=rate,
                            pitch=pitch,
                            volume=volume,
                        )
                        if audio:
                            _cache_audio("edge-tts", voice_to_use, style, sentence, audio)
                            return audio, {
                                "sentence": sentence,
                                "language": "ta",
                                "engine": "edge-tts",
                                "model": "edge-neural",
                                "voice": voice_to_use,
                                "status": "spoken",
                                "fallback": is_fallback,
                                "fallback_from": fallback_from,
                                "notice": f"Fallback from {fallback_from}" if is_fallback else "",
                            }
                    except Exception as exc:
                        msg = f"EdgeTTS Tamil failed ({exc})"
                        if len(chain) > 1:
                            logger.warning("%s; attempting Gemini Tamil.", msg)
                            self._notify_fallback(msg, primary="edge-tts", fallback="gemini")
                        else:
                            logger.error("%s (forced engine; no fallback allowed).", msg)

                elif eng == "gemini":
                    try:
                        if self.gemini and self.gemini.is_online:
                            gemini_voice = pref_voice if ("-" not in pref_voice) else "achird"
                            gemini_model = getattr(self.gemini, "model", getattr(_cfg, "GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts"))
                            audio = self.gemini.synthesize(
                                sentence,
                                voice=gemini_voice,
                                language="ta",
                                style=style,
                            )
                            if audio:
                                _cache_audio("gemini", gemini_voice, style, sentence, audio)
                                return audio, {
                                    "sentence": sentence,
                                    "language": "ta",
                                    "engine": "gemini",
                                    "model": gemini_model,
                                    "voice": gemini_voice,
                                    "status": "spoken",
                                    "fallback": is_fallback,
                                    "fallback_from": fallback_from,
                                    "notice": f"Fallback from {fallback_from}" if is_fallback else "",
                                }
                    except Exception as exc:
                        msg = f"Gemini TTS Tamil failed ({exc})"
                        logger.warning(msg)
                        self._notify_fallback(msg, primary="gemini", fallback="edge-tts" if "edge-tts" in chain else "none")

            # HARD RULE 5: If all Tamil engines fail, emit notice and do NOT read with English voice
            notice = "Tamil voice engine unavailable. Reply displayed as text only."
            logger.warning("Tamil synthesis failed on all candidate Tamil engines: %s", notice)
            self._notify_fallback(notice, primary="tamil_engines", fallback="none")
            return b"", {
                "sentence": sentence,
                "language": "ta",
                "engine": "none",
                "model": "none",
                "voice": "none",
                "status": "text_only",
                "fallback": True,
                "fallback_from": pref_engine,
                "notice": notice,
            }

        # ------------------------------------------------------------------
        # ENGLISH FALLBACK CHAIN: preferred > Gemini > Edge-TTS > Piper
        # (HARD RULE 5: Never read English with a Tamil-only voice if English is available!)
        # ------------------------------------------------------------------
        if forced_engine in ("gemini", "edge-tts", "piper"):
            eng_chain = [forced_engine]
        elif pref_engine == "gemini":
            eng_chain = ["gemini", "edge-tts", "piper"]
        elif pref_engine == "piper":
            eng_chain = ["piper", "edge-tts", "gemini"]
        else:
            eng_chain = ["edge-tts", "gemini", "piper"]

        for eng in eng_chain:
            is_fallback = (eng != (slot_cfg.get("engine") or "gemini").lower())
            fallback_from = slot_cfg.get("engine", "gemini") if is_fallback else ""

            if eng == "gemini":
                try:
                    if self.gemini and self.gemini.is_online:
                        g_voice = pref_voice if ("-" not in pref_voice and not pref_voice.startswith("ta-")) else "achird"
                        gemini_model = getattr(self.gemini, "model", getattr(_cfg, "GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts"))
                        audio = self.gemini.synthesize(
                            sentence,
                            voice=g_voice,
                            language="en",
                            style=style,
                        )
                        if audio:
                            _cache_audio("gemini", g_voice, style, sentence, audio)
                            return audio, {
                                "sentence": sentence,
                                "language": "en",
                                "engine": "gemini",
                                "model": gemini_model,
                                "voice": g_voice,
                                "status": "spoken",
                                "fallback": is_fallback,
                                "fallback_from": fallback_from,
                                "notice": f"Fallback from {fallback_from}" if is_fallback else "",
                            }
                except Exception as exc:
                    msg = f"Gemini TTS English failed ({exc})"
                    if len(eng_chain) > 1:
                        logger.warning("%s; switching to Edge-TTS.", msg)
                        self._notify_fallback(msg, primary="gemini", fallback="edge-tts")
                    else:
                        logger.error("%s (forced engine; no fallback allowed).", msg)

            elif eng == "edge-tts":
                try:
                    # Never use a Tamil voice for English text
                    e_voice = pref_voice if ("-" in pref_voice and not pref_voice.startswith("ta-")) else "en-IN-NeerjaNeural"
                    audio = self.edge.synthesize(
                        sentence,
                        voice=e_voice,
                        language="en",
                        rate=rate,
                        pitch=pitch,
                        volume=volume,
                    )
                    if audio:
                        _cache_audio("edge-tts", e_voice, style, sentence, audio)
                        return audio, {
                            "sentence": sentence,
                            "language": "en",
                            "engine": "edge-tts",
                            "model": "edge-neural",
                            "voice": e_voice,
                            "status": "spoken",
                            "fallback": is_fallback,
                            "fallback_from": fallback_from,
                            "notice": f"Fallback from {fallback_from}" if is_fallback else "",
                        }
                except Exception as exc:
                    msg = f"EdgeTTS English failed ({exc}); switching to offline Piper."
                    if len(eng_chain) > 1:
                        logger.warning("%s", msg)
                        self._notify_fallback(msg, primary="edge-tts", fallback="piper")
                    else:
                        logger.error("%s (forced engine; no fallback allowed).", msg)

            elif eng == "piper":
                try:
                    p_voice = pref_voice if (not pref_voice.startswith("ta-") and not pref_voice.startswith("en-")) else "en_US-lessac-medium"
                    p_model = getattr(self.piper, "model_name", "piper-onnx")
                    audio = self.piper.synthesize(
                        sentence,
                        voice=p_voice,
                        language="en",
                        rate=rate,
                        pitch=pitch,
                        volume=volume,
                    )
                    if audio:
                        _cache_audio("piper", p_voice, style, sentence, audio)
                        return audio, {
                            "sentence": sentence,
                            "language": "en",
                            "engine": "piper",
                            "model": p_model,
                            "voice": p_voice,
                            "status": "spoken",
                            "fallback": is_fallback,
                            "fallback_from": fallback_from,
                            "notice": f"Fallback from {fallback_from}" if is_fallback else "",
                        }
                except Exception as exc:
                    logger.warning("Piper offline English synthesis failed: %s", exc)

        notice = f"All candidate engines ({eng_chain}) failed for English sentence."
        return b"", {
            "sentence": sentence,
            "language": "en",
            "engine": "none",
            "model": "none",
            "voice": "none",
            "status": "failed",
            "fallback": True,
            "fallback_from": pref_engine,
            "notice": notice,
        }

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        rate: Any = None,
        pitch: Any = None,
        volume: Any = None,
        style: str | None = None,
    ) -> bytes:
        """Synthesize text into audio bytes using Two Voice Slots and auto-switch routing."""
        cleaned = sanitize_text_for_speech(text)
        if not cleaned:
            return b""

        # Convert Tanglish words to Tamil script for speech (Prompt E)
        from app import config as _cfg
        from app.voice.tamil_pipeline import prepare_text_for_speech
        mode = getattr(_cfg, "TAMIL_TEXT_MODE", "tanglish_display")
        speech_ready = prepare_text_for_speech(cleaned, tamil_text_mode=mode)
        cleaned = speech_ready if speech_ready else cleaned

        active_prof = get_active_voice_profile()
        slot_en = getattr(self, "active_slot_en", None) or active_prof.get("voice_slot_en", {})
        slot_ta = getattr(self, "active_slot_ta", None) or active_prof.get("voice_slot_ta", {})
        auto_switch = active_prof.get("auto_switch_language", True)

        # If explicit voice is passed, override profile
        if voice is not None:
            is_ta = bool(_TAMIL_RE.search(cleaned) or (language and language.lower() in ("ta", "tamil")) or voice.startswith("ta-"))
            target_slot = dict(slot_ta if is_ta else slot_en)
            target_slot["voice"] = voice
            if "-" in voice:
                target_slot["engine"] = "edge-tts"
            audio, info = self._synthesize_sentence(cleaned, "ta" if is_ta else "en", target_slot, style=style)
            self._last_turn_engines.append(info)
            return audio

        # Bilingual auto-switch mode
        if auto_switch:
            segments = split_bilingual_sentences(cleaned)
            if len(segments) <= 1:
                sent, lang = segments[0] if segments else (cleaned, "en")
                slot = slot_ta if lang == "ta" else slot_en
                audio, info = self._synthesize_sentence(sent, lang, slot, style=style)
                self._last_turn_engines.append(info)
                return audio

            # Multiple bilingual sentences: synthesize each in its respective voice
            audio_pieces: list[bytes] = []
            for sent, lang in segments:
                slot = slot_ta if lang == "ta" else slot_en
                chunk_audio, info = self._synthesize_sentence(sent, lang, slot, style=style)
                self._last_turn_engines.append(info)
                if chunk_audio:
                    audio_pieces.append(chunk_audio)
            return combine_audio_chunks(audio_pieces)

        # Auto-switch OFF: speak whole response with preferred English slot
        target_lang = "ta" if (language and language.lower() in ("ta", "tamil")) else "en"
        target_slot = slot_ta if target_lang == "ta" else slot_en
        audio, info = self._synthesize_sentence(cleaned, target_lang, target_slot, style=style)
        self._last_turn_engines.append(info)
        return audio

    def preview(
        self,
        slot: str = "en",
        text: str | None = None,
        engine: str | None = None,
        voice: str | None = None,
        rate: Any = None,
        pitch: Any = None,
        volume: Any = None,
        language: str | None = None,
        style: str | None = None,
    ) -> tuple[bytes, str]:
        """Synthesize a one-off preview sample in that slot's language and return (audio_bytes, mime_type)."""
        active_prof = get_active_voice_profile()
        slot_en = getattr(self, "active_slot_en", None) or active_prof.get("voice_slot_en", {})
        slot_ta = getattr(self, "active_slot_ta", None) or active_prof.get("voice_slot_ta", {})

        is_ta = slot == "ta" or (language and language.lower() in ("ta", "tamil")) or (text and bool(_TAMIL_RE.search(text)))
        target_slot = dict(slot_ta if is_ta else slot_en)
        target_lang = "ta" if is_ta else "en"

        if text is None:
            text = "வணக்கம், நான் நல்ல சிவம். உங்களுக்கு எப்படி உதவ வேண்டும்?" if is_ta else "Yes boss, sollunga!"

        if engine:
            target_slot["engine"] = engine.lower()
        if voice:
            target_slot["voice"] = voice
        if rate is not None:
            target_slot["rate"] = rate
        if volume is not None:
            target_slot["volume"] = volume

        audio, info = self._synthesize_sentence(
            text,
            target_lang=target_lang,
            slot_cfg=target_slot,
            style=style,
        )

        mime = "audio/mpeg" if target_slot.get("engine") == "edge-tts" else "audio/wav"
        return audio, mime


# Module-level default TTS instance
tts_engine = TTSManager()
