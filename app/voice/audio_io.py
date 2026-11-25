"""app/voice/audio_io.py

Microphone capture and speaker playback via sounddevice.

Enforces:
  • 16 kHz sample rate, 1 channel (mono), 16-bit PCM for microphone input.
  • Device enumeration and selection via configuration/settings.
  • Immediate playback interruption support (stop() method).
  • PortAudio error resilience (graceful degradation if no hardware attached).
"""

from __future__ import annotations

import collections
import io
import logging
import queue
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


def compute_rms(pcm_bytes: bytes) -> float:
    """Compute Root Mean Square (RMS) energy level for 16-bit PCM mono audio."""
    if not pcm_bytes:
        return 0.0
    try:
        arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if len(arr) == 0:
            return 0.0
        return float(np.sqrt(np.mean(arr**2)))
    except Exception:
        return 0.0


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
    """Speaker audio playback manager with immediate interrupt capability and gapless streaming."""

    def __init__(self, device: int | None = None) -> None:
        self.device = device if device is not None else AUDIO_OUTPUT_DEVICE
        self._is_playing = threading.Event()
        self._stop_requested = threading.Event()
        self._gapless_stream: sd.OutputStream | None = None
        self._gapless_queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._gapless_thread: threading.Thread | None = None
        self._gapless_lock = threading.RLock()
        self._active_rate = 24000
        self.played_audio: list[bytes] = []

    def start_gapless_stream(self, sample_rate: int = 24000) -> bool:
        """Open a continuous sounddevice OutputStream for gapless chunk playback."""
        self.stop()
        with self._gapless_lock:
            self._stop_requested.clear()
            self._is_playing.set()
            self._active_rate = sample_rate
            self._gapless_queue = queue.Queue()

            try:
                self._gapless_stream = sd.OutputStream(
                    samplerate=sample_rate,
                    channels=1,
                    dtype="float32",
                    device=self.device,
                )
                self._gapless_stream.start()
            except Exception as exc:
                logger.warning("Could not open continuous output stream (device=%s): %s", self.device, exc)
                self._gapless_stream = None

            def _worker() -> None:
                while not self._stop_requested.is_set():
                    try:
                        chunk = self._gapless_queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    if chunk is None:
                        break
                    if self._stop_requested.is_set():
                        break
                    if self._gapless_stream is not None:
                        try:
                            self._gapless_stream.write(chunk)
                        except Exception as w_exc:
                            logger.debug("Gapless stream write error: %s", w_exc)
                            break
                    self._gapless_queue.task_done()

                with self._gapless_lock:
                    if self._gapless_stream is not None:
                        try:
                            self._gapless_stream.stop()
                            self._gapless_stream.close()
                        except Exception:
                            pass
                        self._gapless_stream = None
                self._is_playing.clear()

            self._gapless_thread = threading.Thread(target=_worker, daemon=True, name="GaplessPlaybackWorker")
            self._gapless_thread.start()
            return True

    def push_gapless_chunk(
        self,
        audio_bytes: bytes,
        fx_mode: str | None = None,
        normalize: bool = True,
        fade: bool = True,
    ) -> bool:
        """Decode audio bytes, apply DSP (resample to 24k, RMS norm, FX, 15ms cosine fade), and enqueue."""
        if not audio_bytes or self._stop_requested.is_set():
            return False
        if hasattr(self, "played_audio") and isinstance(self.played_audio, list):
            self.played_audio.append(audio_bytes)
        try:
            from app.voice.audio_dsp import (
                apply_edge_fade,
                normalize_loudness_rms,
                resample_to_target_rate,
                apply_voice_fx,
            )
            import app.config as _cfg

            arr, rate = decode_audio_to_array(audio_bytes)
            if len(arr) == 0:
                return False

            if arr.ndim > 1:
                arr = arr.mean(axis=1)
            arr = arr.astype(np.float32)

            max_val = float(np.max(np.abs(arr))) if len(arr) > 0 else 0.0
            if max_val > 1.5:
                arr = arr / 32768.0

            if rate != self._active_rate:
                arr = resample_to_target_rate(arr, orig_rate=rate, target_rate=self._active_rate)

            if normalize:
                arr = normalize_loudness_rms(arr, target_rms=0.15)

            active_fx = fx_mode if fx_mode is not None else getattr(_cfg, "VOICE_FX", "off")
            if active_fx and active_fx != "off":
                arr = apply_voice_fx(arr, sample_rate=self._active_rate, fx_mode=active_fx)

            if fade:
                arr = apply_edge_fade(arr, sample_rate=self._active_rate, fade_ms=15.0)

            self._gapless_queue.put(arr.reshape(-1, 1))
            return True
        except Exception as exc:
            logger.error("Failed to process & push gapless chunk: %s", exc)
            return False

    def finish_gapless_stream(self, timeout: float = 30.0) -> None:
        """Signal end of stream and wait for playback to complete."""
        if self._gapless_thread and self._gapless_thread.is_alive():
            self._gapless_queue.put(None)
            self._gapless_thread.join(timeout=timeout)

    def play_audio_bytes(
        self,
        audio_bytes: bytes,
        block: bool = True,
        apply_dsp: bool = True,
        fx_mode: str | None = None,
    ) -> bool:
        """Play any supported audio format (MP3, WAV) through sounddevice with natural DSP."""
        if not audio_bytes:
            return False
        if hasattr(self, "played_audio") and isinstance(self.played_audio, list):
            self.played_audio.append(audio_bytes)
        try:
            from app.voice.audio_dsp import (
                apply_edge_fade,
                normalize_loudness_rms,
                resample_to_target_rate,
                apply_voice_fx,
                TARGET_SAMPLE_RATE,
            )
            import app.config as _cfg

            array, rate = decode_audio_to_array(audio_bytes)
            if len(array) == 0:
                return False

            if apply_dsp:
                if array.ndim > 1:
                    array = array.mean(axis=1)
                array = array.astype(np.float32)
                max_val = float(np.max(np.abs(array))) if len(array) > 0 else 0.0
                if max_val > 1.5:
                    array = array / 32768.0

                if rate != TARGET_SAMPLE_RATE:
                    array = resample_to_target_rate(array, orig_rate=rate, target_rate=TARGET_SAMPLE_RATE)
                    rate = TARGET_SAMPLE_RATE

                array = normalize_loudness_rms(array, target_rms=0.15)
                active_fx = fx_mode if fx_mode is not None else getattr(_cfg, "VOICE_FX", "off")
                if active_fx and active_fx != "off":
                    array = apply_voice_fx(array, sample_rate=rate, fx_mode=active_fx)
                array = apply_edge_fade(array, sample_rate=rate, fade_ms=15.0)

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
            while not self._gapless_queue.empty():
                self._gapless_queue.get_nowait()
        except Exception:
            pass
        try:
            self._gapless_queue.put(None)
        except Exception:
            pass

        with self._gapless_lock:
            if self._gapless_stream is not None:
                try:
                    self._gapless_stream.abort()
                    self._gapless_stream.close()
                except Exception as exc:
                    logger.debug("Error while aborting gapless stream: %s", exc)
                self._gapless_stream = None

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
        self._is_muted = False
        # 250ms in-memory warm ring buffer (13 chunks * 640 bytes = 8320 bytes = 260ms @ 16kHz mono int16)
        self._warm_buffer: collections.deque[bytes] = collections.deque(maxlen=13)
        self._is_warm_active = False
        self._warm_stream: sd.InputStream | None = None
        # PTT active capture buffer
        self._ptt_buffer: list[bytes] = []
        self._ptt_lock = threading.Lock()

    def get_warm_frames(self) -> bytes:
        """Return the current 250ms in-memory warm ring buffer."""
        return b"".join(list(self._warm_buffer))

    def clear_warm_frames(self) -> None:
        """Clear warm buffer in memory."""
        self._warm_buffer.clear()

    def start_warm_stream(self) -> None:
        """Start low-overhead background microphone monitor to keep 250ms warm buffer fresh."""
        if self._warm_stream is not None:
            return

        def _warm_cb(indata, frames, time_info, status):
            if not self._is_muted:
                self._warm_buffer.append(indata.tobytes())

        try:
            self._warm_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=DEFAULT_DTYPE,
                device=self.device,
                blocksize=320,
                callback=_warm_cb,
            )
            self._warm_stream.start()
            self._is_warm_active = True
            logger.info("Keep-mic-warm active with 250ms in-memory ring buffer.")
        except Exception as exc:
            logger.warning("Could not start keep-mic-warm stream: %s", exc)

    def stop_warm_stream(self) -> None:
        """Stop background keep-mic-warm stream."""
        if self._warm_stream is not None:
            try:
                self._warm_stream.stop()
                self._warm_stream.close()
            except Exception as exc:
                logger.debug("Error stopping warm stream: %s", exc)
            finally:
                self._warm_stream = None
                self._is_warm_active = False

    @property
    def is_warm_active(self) -> bool:
        """Return True if keep_mic_warm stream is running."""
        return self._is_warm_active

    def set_muted(self, muted: bool) -> None:
        """Mute or unmute microphone frame streaming (echo control)."""
        self._is_muted = bool(muted)

    @property
    def is_muted(self) -> bool:
        """Return True if the microphone capture stream is muted."""
        return self._is_muted

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
            if self._is_muted:
                return b"\x00" * (samples * 2)
            return data.tobytes()
        except Exception as exc:
            logger.error("Microphone recording failed: %s", exc)
            return b""

    def open_stream(self, callback: Callable[[bytes], None], blocksize: int = 320) -> sd.InputStream:
        """Open an active audio input stream with callback for audio frames."""
        def _sd_callback(indata, frames, time_info, status):
            if status:
                logger.debug("Audio stream status: %s", status)
            if self._is_muted:
                # Echo prevention: emit silent zero frames while speaking
                callback(b"\x00" * (frames * 2))
            else:
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
