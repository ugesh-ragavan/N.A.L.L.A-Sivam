"""app/voice/tts_gemini.py

Google Gemini TTS implementation of TextToSpeechProvider.

Features:
  • Models: gemini-3.8-flash-lite-tts (default, conversational speed) and gemini-3.8-flash-tts.
  • Unary & Streaming audio modes (24 kHz, 16-bit mono little-endian PCM / WAV).
  • Automatic language detection (supports English and Tamil out of the box).
  • Pronunciation map injection for Tamil/Tanglish words before synthesis.
  • Fixed-phrases disk cache (zero-latency hits for greetings and confirmations).
  • Daily quota guard with automatic cap and 429 backoff detection.
  • 4-second first-audio timeout guard to trigger fallback on network lag.
  • Extended Voice Library browsing and AI Studio custom voices (voice_...) support.
"""

from __future__ import annotations

import base64
import concurrent.futures
import io
import logging
import re
import time
import wave
from typing import Any, Generator

from app.config import GEMINI_API_KEY
from app.voice.tts import TextToSpeechProvider, sanitize_text_for_speech
from app.voice.pronunciation import apply_pronunciation_map
from app.voice.quota_guard import QuotaGuard, QuotaExceededError, quota_guard as default_quota_guard
from app.voice.tts_cache import TTSAudioCache, tts_audio_cache as default_tts_cache

logger = logging.getLogger("nalla.voice.gemini_tts")

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="GeminiTTSWorker")

DEFAULT_TTS_MODEL = "gemini-3.8-flash-lite-tts"
HIGH_FIDELITY_TTS_MODEL = "gemini-3.8-flash-tts"
DEFAULT_VOICE = "achird"
DEFAULT_STYLE = "calm, confident, dry wit, conversational"
SAMPLE_RATE = 24000
TIMEOUT_FIRST_AUDIO_S = 4.0


def limit_expressive_tags(text: str, max_tags: int = 1) -> str:
    """Ensure at most `max_tags` expressive bracket tags remain in the text, removing extra tags."""
    tag_pattern = re.compile(
        r"(\[(?:chuckle|sigh|whisper|whispering|laughs|gasp|clears throat|pause|snicker)\])",
        re.IGNORECASE,
    )
    matches = list(tag_pattern.finditer(text))
    if len(matches) <= max_tags:
        return text
    keep_indices = set(m.start() for m in matches[:max_tags])
    parts = []
    last_idx = 0
    for m in matches:
        parts.append(text[last_idx:m.start()])
        if m.start() in keep_indices:
            parts.append(m.group(0))
        last_idx = m.end()
    parts.append(text[last_idx:])
    return "".join(parts)


def strip_expressive_tags(text: str) -> str:
    """Strip all expressive audio tags for strict or plain TTS engines."""
    return re.sub(
        r"\[(?:chuckle|sigh|whisper|whispering|laughs|gasp|clears throat|pause|snicker)\]",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

# 30 prebuilt voices specified in Google Speech Generation API documentation
PREBUILT_VOICES: list[dict[str, Any]] = [
    {"id": "achird", "name": "Achird", "character": "Friendly, approachable, warm", "gender": "male", "recommended": True},
    {"id": "zubenelgenubi", "name": "Zubenelgenubi", "character": "Casual, relaxed", "gender": "male", "recommended": True},
    {"id": "puck", "name": "Puck", "character": "Upbeat, energetic", "gender": "male", "recommended": True},
    {"id": "algenib", "name": "Algenib", "character": "Gravelly, rugged", "gender": "male", "recommended": True},
    {"id": "sulafat", "name": "Sulafat", "character": "Warm, reassuring", "gender": "female", "recommended": True},
    {"id": "zephyr", "name": "Zephyr", "character": "Bright, clear", "gender": "female", "recommended": False},
    {"id": "charon", "name": "Charon", "character": "Informative, steady", "gender": "male", "recommended": False},
    {"id": "kore", "name": "Kore", "character": "Firm, direct", "gender": "female", "recommended": False},
    {"id": "fenrir", "name": "Fenrir", "character": "Excitable, dynamic", "gender": "male", "recommended": False},
    {"id": "leda", "name": "Leda", "character": "Youthful, lively", "gender": "female", "recommended": False},
    {"id": "orus", "name": "Orus", "character": "Firm, resonant", "gender": "male", "recommended": False},
    {"id": "aoede", "name": "Aoede", "character": "Breezy, natural", "gender": "female", "recommended": False},
    {"id": "callirrhoe", "name": "Callirrhoe", "character": "Easy-going, melodic", "gender": "female", "recommended": False},
    {"id": "autonoe", "name": "Autonoe", "character": "Bright, cheerful", "gender": "female", "recommended": False},
    {"id": "enceladus", "name": "Enceladus", "character": "Breathy, gentle", "gender": "male", "recommended": False},
    {"id": "iapetus", "name": "Iapetus", "character": "Clear, articulate", "gender": "male", "recommended": False},
    {"id": "umbriel", "name": "Umbriel", "character": "Easy-going, calm", "gender": "male", "recommended": False},
    {"id": "algieba", "name": "Algieba", "character": "Smooth, refined", "gender": "male", "recommended": False},
    {"id": "despina", "name": "Despina", "character": "Smooth, pleasant", "gender": "female", "recommended": False},
    {"id": "erinome", "name": "Erinome", "character": "Clear, expressive", "gender": "female", "recommended": False},
    {"id": "rasalgethi", "name": "Rasalgethi", "character": "Informative, grounded", "gender": "male", "recommended": False},
    {"id": "laomedeia", "name": "Laomedeia", "character": "Upbeat, positive", "gender": "female", "recommended": False},
    {"id": "achernar", "name": "Achernar", "character": "Soft, soothing", "gender": "female", "recommended": False},
    {"id": "alnilam", "name": "Alnilam", "character": "Firm, commanding", "gender": "male", "recommended": False},
    {"id": "schedar", "name": "Schedar", "character": "Even, balanced", "gender": "female", "recommended": False},
    {"id": "gacrux", "name": "Gacrux", "character": "Mature, authoritative", "gender": "male", "recommended": False},
    {"id": "pulcherrima", "name": "Pulcherrima", "character": "Forward, direct", "gender": "female", "recommended": False},
    {"id": "vindemiatrix", "name": "Vindemiatrix", "character": "Gentle, polite", "gender": "female", "recommended": False},
    {"id": "sadachbia", "name": "Sadachbia", "character": "Lively, spirited", "gender": "male", "recommended": False},
    {"id": "sadaltager", "name": "Sadaltager", "character": "Knowledgeable, academic", "gender": "male", "recommended": False},
]

# Curated Extended Catalog for filtering across language, gender, pitch, and context
EXTENDED_CATALOG: list[dict[str, Any]] = [
    {"id": "ta-IN-Standard-A", "name": "Pallavi (ta-IN)", "display_name": "Pallavi", "language_code": "ta-IN", "accent": "Tamil (India)", "gender": "female", "pitch": "medium", "context": "conversational", "description": "Tamil Southern Conversational Female"},
    {"id": "ta-IN-Standard-B", "name": "Valluvar (ta-IN)", "display_name": "Valluvar", "language_code": "ta-IN", "accent": "Tamil (India)", "gender": "male", "pitch": "low", "context": "conversational", "description": "Tamil Natural Male"},
    {"id": "ta-IN-Wavenet-A", "name": "Kavitha (ta-IN)", "display_name": "Kavitha", "language_code": "ta-IN", "accent": "Tamil (India)", "gender": "female", "pitch": "high", "context": "assistant", "description": "Tamil Assistant Female"},
    {"id": "ta-IN-Wavenet-B", "name": "Murugan (ta-IN)", "display_name": "Murugan", "language_code": "ta-IN", "accent": "Tamil (India)", "gender": "male", "pitch": "low", "context": "news", "description": "Tamil News Announcer Male"},
    {"id": "ta-IN-Neural2-A", "name": "Meenakshi (ta-IN)", "display_name": "Meenakshi", "language_code": "ta-IN", "accent": "Tamil (India)", "gender": "female", "pitch": "medium", "context": "casual", "description": "Tamil Warm Neural Female"},
    {"id": "ta-IN-Neural2-B", "name": "Anbarasan (ta-IN)", "display_name": "Anbarasan", "language_code": "ta-IN", "accent": "Tamil (India)", "gender": "male", "pitch": "low", "context": "storytelling", "description": "Tamil Resonant Male"},
    {"id": "en-IN-Standard-A", "name": "Neerja (en-IN)", "display_name": "Neerja", "language_code": "en-IN", "accent": "English (India)", "gender": "female", "pitch": "medium", "context": "conversational", "description": "Indian English Conversational Female"},
    {"id": "en-IN-Standard-B", "name": "Prabhat (en-IN)", "display_name": "Prabhat", "language_code": "en-IN", "accent": "English (India)", "gender": "male", "pitch": "low", "context": "conversational", "description": "Indian English Natural Male"},
    {"id": "en-IN-Wavenet-A", "name": "Aditi (en-IN)", "display_name": "Aditi", "language_code": "en-IN", "accent": "English (India)", "gender": "female", "pitch": "high", "context": "assistant", "description": "Indian English Assistant Female"},
    {"id": "en-IN-Wavenet-B", "name": "Ravi (en-IN)", "display_name": "Ravi", "language_code": "en-IN", "accent": "English (India)", "gender": "male", "pitch": "low", "context": "news", "description": "Indian English Broadcast Male"},
    {"id": "en-IN-Neural2-A", "name": "Pooja (en-IN)", "display_name": "Pooja", "language_code": "en-IN", "accent": "English (India)", "gender": "female", "pitch": "medium", "context": "casual", "description": "Indian English Expressive Female"},
    {"id": "en-IN-Neural2-B", "name": "Karan (en-IN)", "display_name": "Karan", "language_code": "en-IN", "accent": "English (India)", "gender": "male", "pitch": "low", "context": "storytelling", "description": "Indian English Warm Male"},
    {"id": "achird", "name": "Achird", "display_name": "Achird", "language_code": "en-US", "accent": "American", "gender": "male", "pitch": "medium", "context": "conversational", "description": "Friendly, approachable, warm"},
    {"id": "zubenelgenubi", "name": "Zubenelgenubi", "display_name": "Zubenelgenubi", "language_code": "en-US", "accent": "American", "gender": "male", "pitch": "medium", "context": "casual", "description": "Casual, relaxed"},
    {"id": "puck", "name": "Puck", "display_name": "Puck", "language_code": "en-US", "accent": "American", "gender": "male", "pitch": "high", "context": "upbeat", "description": "Upbeat, energetic"},
    {"id": "algenib", "name": "Algenib", "display_name": "Algenib", "language_code": "en-US", "accent": "American", "gender": "male", "pitch": "low", "context": "storytelling", "description": "Gravelly, rugged"},
    {"id": "sulafat", "name": "Sulafat", "display_name": "Sulafat", "language_code": "en-US", "accent": "American", "gender": "female", "pitch": "medium", "context": "conversational", "description": "Warm, reassuring"},
    {"id": "zephyr", "name": "Zephyr", "display_name": "Zephyr", "language_code": "en-US", "accent": "American", "gender": "female", "pitch": "high", "context": "assistant", "description": "Bright, clear"},
    {"id": "charon", "name": "Charon", "display_name": "Charon", "language_code": "en-US", "accent": "American", "gender": "male", "pitch": "low", "context": "news", "description": "Informative, steady"},
    {"id": "fenrir", "name": "Fenrir", "display_name": "Fenrir", "language_code": "en-US", "accent": "American", "gender": "male", "pitch": "high", "context": "upbeat", "description": "Excitable, dynamic"},
    {"id": "en-GB-Neural2-A", "name": "Arthur (en-GB)", "display_name": "Arthur", "language_code": "en-GB", "accent": "British", "gender": "female", "pitch": "medium", "context": "conversational", "description": "British Received Pronunciation Female"},
    {"id": "en-GB-Neural2-B", "name": "Oliver (en-GB)", "display_name": "Oliver", "language_code": "en-GB", "accent": "British", "gender": "male", "pitch": "low", "context": "news", "description": "British Received Pronunciation Male"},
]


def pcm_to_wav(pcm_data: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit mono PCM bytes in a valid RIFF WAV header."""
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return wav_io.getvalue()


class GeminiTTS(TextToSpeechProvider):
    """Google Gemini Speech Generation TTS provider."""

    def __init__(
        self,
        api_key: str | None = None,
        tts_model: str | None = None,
        voice: str = DEFAULT_VOICE,
        style: str | None = None,
        streaming: bool = True,
        expressive_tags: bool | None = None,
        quota: QuotaGuard | None = None,
        cache: TTSAudioCache | None = None,
        client: Any = None,
        quota_guard: QuotaGuard | None = None,
        audio_cache: TTSAudioCache | None = None,
    ) -> None:
        import app.config as _cfg
        self.api_key = api_key or getattr(_cfg, "GEMINI_API_KEY", "")
        self.tts_model = tts_model or getattr(_cfg, "GEMINI_TTS_MODEL", DEFAULT_TTS_MODEL)
        self.voice = voice
        self.style = style or getattr(_cfg, "GEMINI_TTS_STYLE", DEFAULT_STYLE)
        self.streaming = streaming
        self.expressive_tags = expressive_tags if expressive_tags is not None else getattr(_cfg, "EXPRESSIVE_TAGS", True)
        self.quota = quota_guard or quota or default_quota_guard
        self.cache = audio_cache or cache or default_tts_cache
        self._client = client

    @property
    def is_online(self) -> bool:
        return True

    def is_available(self) -> bool:
        """Check if Gemini TTS has valid credentials or active client."""
        return bool(self.api_key or self._client is not None)

    def _get_client(self) -> Any:
        """Initialize and return the genai client with v1beta endpoint and timeout."""
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to your .env file.")
        from google import genai
        self._client = genai.Client(
            api_key=self.api_key,
            http_options={"api_version": "v1beta", "timeout": 4000},
        )
        return self._client

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        rate: Any = None,
        pitch: Any = None,
        volume: Any = None,
        style: str | None = None,
        streaming: bool | None = None,
    ) -> bytes:
        """Synthesize text into WAV audio bytes using Gemini TTS.

        Checks disk cache first, applies pronunciation mappings, checks quota,
        and enforces a 4-second first-audio timeout guard.
        """
        import app.config as _cfg
        raw_text = sanitize_text_for_speech(text)
        if not raw_text:
            return b""

        use_tags = self.expressive_tags if self.expressive_tags is not None else getattr(_cfg, "EXPRESSIVE_TAGS", True)
        if use_tags:
            raw_text = limit_expressive_tags(raw_text, max_tags=1)
        else:
            raw_text = strip_expressive_tags(raw_text)

        active_voice = (voice or self.voice).strip()
        active_style = (style or self.style or getattr(_cfg, "GEMINI_TTS_STYLE", DEFAULT_STYLE)).strip()
        active_model = (getattr(self, "tts_model", None) or getattr(_cfg, "GEMINI_TTS_MODEL", DEFAULT_TTS_MODEL)).strip()
        use_streaming = self.streaming if streaming is None else streaming

        # 1. Check disk cache first (bypasses quota & network completely)
        cached_wav = self.cache.get(
            model=active_model,
            voice=active_voice,
            style=active_style,
            text=raw_text,
        )
        if cached_wav is not None:
            return cached_wav

        # 2. Check and increment daily quota guard
        self.quota.check_and_increment()

        # 3. Apply pronunciation map (Tamil phonetics)
        mapped_text = apply_pronunciation_map(raw_text)

        # 4. Synthesize via streaming or unary
        if use_streaming:
            wav_bytes = self._synthesize_streaming_full(
                text=mapped_text,
                voice=active_voice,
                style=active_style,
                model=active_model,
            )
        else:
            wav_bytes = self._synthesize_unary(
                text=mapped_text,
                voice=active_voice,
                style=active_style,
                model=active_model,
            )

        if not wav_bytes or len(wav_bytes) < 44:
            raise RuntimeError(f"Gemini TTS returned empty audio for: {raw_text[:40]}")

        # 5. Populate disk cache for future instant replay
        self.cache.put(
            model=active_model,
            voice=active_voice,
            style=active_style,
            text=raw_text,
            audio_bytes=wav_bytes,
        )
        return wav_bytes

    def synthesize_unary(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        style: str | None = None,
    ) -> bytes:
        """Public unary synthesis method with caching and quota guards."""
        return self.synthesize(text=text, voice=voice, language=language, style=style, streaming=False)

    def synthesize_stream(
        self,
        text: str,
        voice: str | None = None,
        style: str | None = None,
    ) -> Generator[bytes, None, None]:
        """Yield raw 24 kHz 16-bit mono PCM chunks as they arrive from Gemini TTS.

        Enforces a 4.0-second first-audio timeout guard.
        """
        import app.config as _cfg
        raw_text = sanitize_text_for_speech(text)
        if not raw_text:
            return

        use_tags = self.expressive_tags if self.expressive_tags is not None else getattr(_cfg, "EXPRESSIVE_TAGS", True)
        if use_tags:
            raw_text = limit_expressive_tags(raw_text, max_tags=1)
        else:
            raw_text = strip_expressive_tags(raw_text)

        active_voice = (voice or self.voice).strip()
        active_style = (style or self.style or getattr(_cfg, "GEMINI_TTS_STYLE", DEFAULT_STYLE)).strip()
        active_model = (getattr(self, "tts_model", None) or getattr(_cfg, "GEMINI_TTS_MODEL", DEFAULT_TTS_MODEL)).strip()

        # Check quota
        self.quota.check_and_increment()

        mapped_text = apply_pronunciation_map(raw_text)
        client = self._get_client()

        t0 = time.perf_counter()
        first_chunk_received = False

        try:
            future = _EXECUTOR.submit(
                client.interactions.create,
                model=active_model,
                input=[{
                    "type": "user_input",
                    "content": [{
                        "type": "text",
                        "text": mapped_text,
                        "annotations": [{"type": "speech_metadata", "style": active_style}],
                    }],
                }],
                stream=True,
                response_format={
                    "type": "audio",
                    "mime_type": "audio/l16",
                    "sample_rate": SAMPLE_RATE,
                },
                generation_config={"speech_config": [{"voice": active_voice}]},
            )
            try:
                stream = future.result(timeout=TIMEOUT_FIRST_AUDIO_S)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise TimeoutError(f"Gemini TTS stream start timed out after {TIMEOUT_FIRST_AUDIO_S}s") from exc

            for event in stream:
                elapsed = time.perf_counter() - t0
                if not first_chunk_received and elapsed > TIMEOUT_FIRST_AUDIO_S:
                    raise TimeoutError(
                        f"Gemini TTS first audio chunk timed out after {elapsed:.2f}s (> {TIMEOUT_FIRST_AUDIO_S}s)"
                    )

                step = getattr(event, "step", None)
                if step and hasattr(step, "delta"):
                    delta = step.delta
                else:
                    delta = getattr(event, "delta", None)

                if delta:
                    chunk_b64 = getattr(delta, "data", None)
                    if isinstance(chunk_b64, (str, bytes)):
                        chunk_bytes = base64.b64decode(chunk_b64)
                        if chunk_bytes:
                            if not first_chunk_received:
                                first_chunk_received = True
                                logger.debug("Gemini TTS first chunk arrived in %.2fs", elapsed)
                            yield chunk_bytes

        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str or "quota" in exc_str.lower():
                self.quota.record_error_429(exc_str)
                raise QuotaExceededError(f"Gemini TTS 429 quota exhausted: {exc}") from exc
            logger.warning("Gemini TTS streaming failed: %s", exc)
            raise

    def _synthesize_streaming_full(
        self,
        text: str,
        voice: str,
        style: str,
        model: str,
    ) -> bytes:
        """Collect all streaming PCM chunks within timeout and assemble into a WAV file."""
        client = self._get_client()
        t0 = time.perf_counter()
        first_chunk_received = False
        pcm_chunks: list[bytes] = []

        try:
            future = _EXECUTOR.submit(
                client.interactions.create,
                model=model,
                input=[{
                    "type": "user_input",
                    "content": [{
                        "type": "text",
                        "text": text,
                        "annotations": [{"type": "speech_metadata", "style": style}],
                    }],
                }],
                stream=True,
                response_format={
                    "type": "audio",
                    "mime_type": "audio/l16",
                    "sample_rate": SAMPLE_RATE,
                },
                generation_config={"speech_config": [{"voice": voice}]},
            )
            try:
                stream = future.result(timeout=TIMEOUT_FIRST_AUDIO_S)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise TimeoutError(f"Gemini TTS streaming first audio timed out after {TIMEOUT_FIRST_AUDIO_S}s") from exc

            for event in stream:
                elapsed = time.perf_counter() - t0
                if not first_chunk_received and elapsed > TIMEOUT_FIRST_AUDIO_S:
                    raise TimeoutError(
                        f"Gemini TTS streaming first audio timed out after {elapsed:.2f}s (> {TIMEOUT_FIRST_AUDIO_S}s)"
                    )

                step = getattr(event, "step", None)
                if step and hasattr(step, "delta"):
                    delta = step.delta
                else:
                    delta = getattr(event, "delta", None)

                if delta:
                    chunk_b64 = getattr(delta, "data", None)
                    if isinstance(chunk_b64, (str, bytes)):
                        chunk_bytes = base64.b64decode(chunk_b64)
                        if chunk_bytes:
                            if not first_chunk_received:
                                first_chunk_received = True
                                logger.debug("Gemini TTS streaming audio arrived in %.2fs", elapsed)
                            pcm_chunks.append(chunk_bytes)

            full_pcm = b"".join(pcm_chunks)
            if not full_pcm:
                return b""
            return pcm_to_wav(full_pcm, sample_rate=SAMPLE_RATE)

        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str or "quota" in exc_str.lower():
                self.quota.record_error_429(exc_str)
                raise QuotaExceededError(f"Gemini TTS 429 quota exhausted: {exc}") from exc
            logger.warning("Gemini TTS streaming synthesis error: %s", exc)
            raise

    def _synthesize_unary(
        self,
        text: str,
        voice: str,
        style: str,
        model: str,
    ) -> bytes:
        """Call unary interactions.create and return decoded WAV bytes."""
        client = self._get_client()
        t0 = time.perf_counter()

        try:
            future = _EXECUTOR.submit(
                client.interactions.create,
                model=model,
                input=[{
                    "type": "user_input",
                    "content": [{
                        "type": "text",
                        "text": text,
                        "annotations": [{"type": "speech_metadata", "style": style}],
                    }],
                }],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": voice}]},
            )
            try:
                interaction = future.result(timeout=TIMEOUT_FIRST_AUDIO_S)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise TimeoutError(f"Gemini TTS unary timed out after {TIMEOUT_FIRST_AUDIO_S}s") from exc

            elapsed = time.perf_counter() - t0
            if elapsed > TIMEOUT_FIRST_AUDIO_S:
                logger.warning("Gemini TTS unary took %.2fs (> 4s limit)", elapsed)

            out_audio = getattr(interaction, "output_audio", None)
            if out_audio and hasattr(out_audio, "data") and isinstance(out_audio.data, (str, bytes)):
                return base64.b64decode(out_audio.data)

            outputs = getattr(interaction, "output", []) or []
            if isinstance(outputs, list):
                for item in outputs:
                    item_type = getattr(item, "type", "") if not isinstance(item, dict) else item.get("type")
                    if item_type == "audio":
                        b64_val = getattr(item, "data", "") if not isinstance(item, dict) else item.get("data")
                        if isinstance(b64_val, (str, bytes)):
                            return base64.b64decode(b64_val)

            return b""

        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str or "quota" in exc_str.lower():
                self.quota.record_error_429(exc_str)
                raise QuotaExceededError(f"Gemini TTS 429 quota exhausted: {exc}") from exc
            logger.warning("Gemini TTS unary synthesis error: %s", exc)
            raise

    def list_extended_voices(
        self,
        language_code: list[str] | None = None,
        gender: list[str] | None = None,
        pitch: list[str] | None = None,
        contexts: list[str] | None = None,
        search: str | None = None,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """Browse the Google Extended Voice Library using client.voices.list with catalog fallback."""
        client = self._get_client()
        kwargs: dict[str, Any] = {"page_size": page_size}
        if language_code:
            kwargs["language_code"] = language_code
        if gender:
            kwargs["gender"] = gender
        if pitch:
            kwargs["pitch"] = pitch
        if contexts:
            kwargs["contexts"] = contexts
        if search:
            kwargs["search"] = search

        results: list[dict[str, Any]] = []
        if client and hasattr(client, "voices") and hasattr(client.voices, "list"):
            try:
                resp = client.voices.list(**kwargs)
                voices = getattr(resp, "voices", []) or []
                for v in voices:
                    results.append({
                        "id": getattr(v, "id", ""),
                        "display_name": getattr(v, "display_name", ""),
                        "name": getattr(v, "display_name", "") or getattr(v, "id", ""),
                        "language_code": getattr(v, "language_code", ""),
                        "accent": getattr(v, "accent", ""),
                        "gender": getattr(v, "gender", ""),
                        "pitch": getattr(v, "pitch", ""),
                        "context": getattr(v, "context", "") or getattr(v, "persona", ""),
                        "description": getattr(v, "description", ""),
                        "persona": getattr(v, "persona", ""),
                        "type": getattr(v, "type_", "prebuilt"),
                    })
            except Exception as exc:
                logger.debug("Live client.voices.list query not available, using catalog fallback: %s", exc)

        if results:
            return results

        # Catalog fallback filtering
        filtered = []
        target_langs = set(l.lower() for l in language_code) if language_code else None
        target_genders = set(g.lower() for g in gender) if gender else None
        target_pitches = set(p.lower() for p in pitch) if pitch else None
        target_contexts = set(c.lower() for c in contexts) if contexts else None
        s_term = search.lower() if search else None

        for item in EXTENDED_CATALOG:
            if target_langs and item.get("language_code", "").lower() not in target_langs:
                continue
            if target_genders and item.get("gender", "").lower() not in target_genders:
                continue
            if target_pitches and item.get("pitch", "").lower() not in target_pitches:
                continue
            if target_contexts and item.get("context", "").lower() not in target_contexts:
                continue
            if s_term:
                name_match = s_term in item.get("name", "").lower()
                desc_match = s_term in item.get("description", "").lower()
                id_match = s_term in item.get("id", "").lower()
                if not (name_match or desc_match or id_match):
                    continue
            filtered.append(dict(item))

        return filtered[:page_size]


# Module-level default Gemini TTS instance
gemini_tts = GeminiTTS()
