"""app/voice/audio_io.py

Microphone capture and speaker playback via sounddevice.

Enforces:
  • 16 kHz sample rate, 1 channel (mono), 16-bit PCM for microphone input.
  • Device enumeration and selection via configuration/settings.
  • Immediate playback interruption support (stop() method).
  • PortAudio error resilience (graceful degradation if no hardware attached).
"""

from __future__ import annotations

import io
import logging
import threading
import wave
from typing import Any, Callable

import numpy as np
import sounddevice as sd

from app.config import AUDIO_INPUT_DEVICE, AUDIO_OUTPUT_DEVICE

logger = logging.getLogger("nalla.voice.audio_io")

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_DTYPE = "int16"


def list_input_devices() -> list[dict[str, Any]]:
    """Return all available audio input (microphone) devices."""
    devices = []
    try:
        all_devs = sd.query_devices()
        for idx, dev in enumerate(all_devs):
            if dev.get("max_input_channels", 0) > 0:
                devices.append({
                    "index": idx,
                    "name": dev.get("name", f"Device #{idx}"),
                    "channels": dev.get("max_input_channels", 1),
                    "default_samplerate": dev.get("default_samplerate", 16000),
                    "is_default": idx == sd.default.device[0],
                })
    except Exception as exc:
        logger.warning("Failed to query input devices: %s", exc)
    return devices


def list_output_devices() -> list[dict[str, Any]]:
    """Return all available audio output (speaker) devices."""
    devices = []
    try:
        all_devs = sd.query_devices()
        for idx, dev in enumerate(all_devs):
            if dev.get("max_output_channels", 0) > 0:
                devices.append({
                    "index": idx,
                    "name": dev.get("name", f"Device #{idx}"),
                    "channels": dev.get("max_output_channels", 2),
                    "default_samplerate": dev.get("default_samplerate", 44100),
                    "is_default": idx == sd.default.device[1],
                })
    except Exception as exc:
        logger.warning("Failed to query output devices: %s", exc)
    return devices


def decode_audio_to_array(audio_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode any audio bytes (WAV, MP3, OGG) to a numpy array and sample rate."""
    import av
    container = av.open(io.BytesIO(audio_bytes))
    if not container.streams.audio:
        raise ValueError("No audio streams found in audio bytes.")
    stream = container.streams.audio[0]
    sample_rate = stream.rate
    arrays = []
    for frame in container.decode(audio=0):
        arr = frame.to_ndarray()
        if arr.ndim > 1 and arr.shape[0] < arr.shape[1]:
            arr = arr.T
        arrays.append(arr)
    if not arrays:
        return np.array([], dtype=np.float32), sample_rate
    combined = np.concatenate(arrays, axis=0)
    return combined, sample_rate


class AudioPlayer:
    """Speaker audio playback manager with immediate interrupt capability."""

    def __init__(self, device: int | None = None) -> None:
        self.device = device if device is not None else AUDIO_OUTPUT_DEVICE
        self._is_playing = threading.Event()
        self._stop_requested = threading.Event()

    def play_audio_bytes(self, audio_bytes: bytes, block: bool = True) -> bool:
        """Play any supported audio format (MP3, WAV) through sounddevice."""
        if not audio_bytes:
            return False
        try:
            array, rate = decode_audio_to_array(audio_bytes)
            return self.play_array(array, sample_rate=rate, block=block)
        except Exception as exc:
            logger.error("Failed to decode/play audio bytes: %s", exc)
            return False

    def play_wav_bytes(self, wav_bytes: bytes, block: bool = True) -> bool:
        """Play WAV format audio bytes to the speaker."""
        return self.play_audio_bytes(wav_bytes, block=block)

    def play_pcm_bytes(
        self,
        pcm_bytes: bytes,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        block: bool = True,
    ) -> bool:
        """Play raw PCM16 audio bytes to the speaker."""
        if not pcm_bytes:
            return False
        try:
            audio_array = np.frombuffer(pcm_bytes, dtype=np.int16)
            if channels > 1:
                audio_array = audio_array.reshape(-1, channels)
            return self.play_array(audio_array, sample_rate=sample_rate, block=block)
        except Exception as exc:
            logger.error("Failed to play PCM audio: %s", exc)
            return False

    def play_array(self, audio_data: np.ndarray, sample_rate: int = 16000, block: bool = True) -> bool:
        """Play a numpy audio array through sounddevice."""
        self._stop_requested.clear()
        self._is_playing.set()

        try:
            sd.play(audio_data, samplerate=sample_rate, device=self.device)
            if block:
                while sd.get_stream() and sd.get_stream().active:
                    if self._stop_requested.is_set():
                        sd.stop()
                        logger.info("Playback interrupted by user.")
                        break
                    sd.sleep(50)
                sd.stop()
            return True
        except Exception as exc:
            logger.error("Audio playback error: %s", exc)
            return False
        finally:
            self._is_playing.clear()

    def stop(self) -> None:
        """Immediately interrupt and stop any active audio playback."""
        self._stop_requested.set()
        try:
            sd.stop()
        except Exception as exc:
            logger.debug("Error while stopping playback: %s", exc)
        self._is_playing.clear()

    @property
    def is_playing(self) -> bool:
        """Return True if audio is actively playing."""
        return self._is_playing.is_set()


class AudioRecorder:
    """Microphone audio capture manager (16 kHz, 16-bit mono)."""

    def __init__(
        self,
        device: int | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> None:
        self.device = device if device is not None else AUDIO_INPUT_DEVICE
        self.sample_rate = sample_rate
        self.channels = DEFAULT_CHANNELS
        self._stream: sd.InputStream | None = None
        self._recording = threading.Event()

    def record_fixed(self, duration_seconds: float) -> bytes:
        """Record for a fixed number of seconds. Returns 16kHz mono PCM16 bytes."""
        samples = int(duration_seconds * self.sample_rate)
        try:
            data = sd.rec(
                samples,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=DEFAULT_DTYPE,
                device=self.device,
            )
            sd.wait()
            return data.tobytes()
        except Exception as exc:
            logger.error("Microphone recording failed: %s", exc)
            return b""

    def open_stream(self, callback: Callable[[bytes], None], blocksize: int = 320) -> sd.InputStream:
        """Open an active audio input stream with callback for audio frames."""
        def _sd_callback(indata, frames, time_info, status):
            if status:
                logger.debug("Audio stream status: %s", status)
            callback(indata.tobytes())

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=DEFAULT_DTYPE,
            device=self.device,
            blocksize=blocksize,
            callback=_sd_callback,
        )
        self._stream.start()
        return self._stream

    def close_stream(self) -> None:
        """Close the active audio input stream."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.debug("Error closing audio stream: %s", exc)
            finally:
                self._stream = None


# Module-level default instances
default_player = AudioPlayer()
default_recorder = AudioRecorder()
