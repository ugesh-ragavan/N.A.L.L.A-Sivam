"""app/voice/vad.py

Voice Activity Detection (VAD) using hybrid WebRTC VAD + RMS energy gating.

Monitors incoming 16 kHz audio frames, detects when speech begins,
and terminates recording when silence exceeds a threshold (~0.8s),
capping at a maximum duration of 20 seconds.
"""

from __future__ import annotations

import collections
import logging
import time
from typing import Any, Callable

import numpy as np
import webrtcvad

from app.config import VAD_AGGRESSIVENESS, VAD_ENERGY_THRESHOLD
from app.voice.audio_io import AudioRecorder, DEFAULT_SAMPLE_RATE

logger = logging.getLogger("nalla.voice.vad")

# Frame size: 20 ms @ 16 kHz mono int16 = 320 samples = 640 bytes
FRAME_DURATION_MS = 20
SAMPLES_PER_FRAME = int(DEFAULT_SAMPLE_RATE * (FRAME_DURATION_MS / 1000.0))
BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2  # 16-bit = 2 bytes per sample


def _compute_pcm_rms(data: bytes) -> float:
    """Compute RMS amplitude of 16-bit PCM bytes (adapted from OpenJarvis voice_io.py)."""
    import struct
    n = len(data) // 2
    if n == 0:
        return 0.0
    shorts = struct.unpack(f"{n}h", data[: n * 2])
    return float((sum(s * s for s in shorts) / n) ** 0.5)


class VoiceActivityDetector:
    """Detects speech and silence intervals in a stream of PCM16 frames using hybrid VAD."""

    def __init__(
        self,
        aggressiveness: int | None = None,
        energy_threshold: float | None = None,
    ) -> None:
        agg = aggressiveness if aggressiveness is not None else VAD_AGGRESSIVENESS
        self.aggressiveness = max(0, min(3, agg))
        self._vad = webrtcvad.Vad(self.aggressiveness)
        self.energy_threshold = (
            energy_threshold if energy_threshold is not None else VAD_ENERGY_THRESHOLD
        )

    def is_speech(
        self,
        pcm_frame: bytes,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        energy_threshold: float | None = None,
    ) -> bool:
        """Evaluate whether a single frame contains active speech using hybrid WebRTC + energy check."""
        if len(pcm_frame) != BYTES_PER_FRAME:
            # Pad or truncate if needed for webrtcvad strict sizing
            if len(pcm_frame) < BYTES_PER_FRAME:
                pcm_frame = pcm_frame.ljust(BYTES_PER_FRAME, b"\x00")
            else:
                pcm_frame = pcm_frame[:BYTES_PER_FRAME]

        thresh = self.energy_threshold if energy_threshold is None else energy_threshold
        # Energy gate: low RMS is ambient noise / silence, regardless of WebRTC classification
        if thresh > 0:
            rms = _compute_pcm_rms(pcm_frame)
            if rms < thresh:
                return False

        try:
            return self._vad.is_speech(pcm_frame, sample_rate)
        except Exception as exc:
            logger.debug("VAD frame evaluation error: %s", exc)
            # Fallback to pure energy thresholding if WebRTC raises error
            return _compute_pcm_rms(pcm_frame) >= (thresh or 200.0)

    def record_speech_segment(
        self,
        recorder: AudioRecorder | None = None,
        silence_duration: float = 0.8,
        max_duration: float = 20.0,
        initial_timeout: float = 8.0,
        state_callback: Callable[[str], None] | None = None,
    ) -> bytes:
        """Capture microphone audio until the user stops speaking.

        Parameters
        ----------
        recorder: AudioRecorder instance (uses default if None).
        silence_duration: Seconds of consecutive silence to conclude speech (default 0.8s).
        max_duration: Maximum allowed recording duration in seconds (default 20s).
        initial_timeout: Max wait for speech to commence before aborting (default 8s).
        state_callback: Optional callable for status updates ("waiting", "speaking").

        Returns
        -------
        Recorded PCM16 bytes of the speech segment (including pre-speech buffer).
        """
        rec = recorder or AudioRecorder()
        silence_frames_threshold = int(silence_duration / (FRAME_DURATION_MS / 1000.0))
        max_total_frames = int(max_duration / (FRAME_DURATION_MS / 1000.0))
        initial_timeout_frames = int(initial_timeout / (FRAME_DURATION_MS / 1000.0))

        # Ring buffer for ~300 ms pre-speech audio
        pre_speech_buffer_len = int(0.3 / (FRAME_DURATION_MS / 1000.0))
        ring_buffer: collections.deque[bytes] = collections.deque(maxlen=pre_speech_buffer_len)

        audio_frames: list[bytes] = []
        speech_started = False
        consecutive_silence = 0
        total_frames = 0
        speech_streak = 0
        initial_rms_samples: list[float] = []

        frame_queue: collections.deque[bytes] = collections.deque()

        def _on_chunk(chunk_bytes: bytes) -> None:
            # Slice chunk into exact 20 ms frames
            idx = 0
            while idx + BYTES_PER_FRAME <= len(chunk_bytes):
                frame_queue.append(chunk_bytes[idx : idx + BYTES_PER_FRAME])
                idx += BYTES_PER_FRAME

        stream = rec.open_stream(callback=_on_chunk, blocksize=SAMPLES_PER_FRAME)

        effective_thresh = self.energy_threshold

        try:
            if state_callback:
                state_callback("waiting")

            while total_frames < max_total_frames:
                if not frame_queue:
                    time.sleep(0.01)
                    continue

                frame = frame_queue.popleft()
                total_frames += 1

                # Calculate frame RMS
                samples = np.frombuffer(frame, dtype=np.int16)
                frame_rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if len(samples) > 0 else 0.0

                if not speech_started:
                    ring_buffer.append(frame)
                    if len(initial_rms_samples) < 15:
                        initial_rms_samples.append(frame_rms)
                        if len(initial_rms_samples) == 15:
                            ambient_avg = float(np.mean(initial_rms_samples))
                            effective_thresh = max(self.energy_threshold, ambient_avg * 1.5)
                            logger.debug("VAD calibrated: ambient=%.1f, thresh=%.1f", ambient_avg, effective_thresh)

                    has_speech = self.is_speech(frame, energy_threshold=effective_thresh)
                    if has_speech:
                        speech_streak += 1
                    else:
                        speech_streak = 0

                    # Require at least 2 consecutive speech frames for onset
                    if speech_streak >= 2:
                        speech_started = True
                        if state_callback:
                            state_callback("speaking")
                        logger.info("Speech onset detected by VAD (rms=%.1f).", frame_rms)
                        audio_frames.extend(ring_buffer)
                    elif total_frames > initial_timeout_frames:
                        logger.debug("Initial silence timeout reached without speech.")
                        break
                else:
                    audio_frames.append(frame)
                    has_speech = self.is_speech(frame, energy_threshold=effective_thresh)
                    if has_speech:
                        consecutive_silence = 0
                    else:
                        consecutive_silence += 1
                        if consecutive_silence >= silence_frames_threshold:
                            dur = len(audio_frames) * (FRAME_DURATION_MS / 1000.0)
                            logger.info("Speech end detected after %.2fs of silence (segment: %.2fs).", silence_duration, dur)
                            break

        finally:
            rec.close_stream()

        if not speech_started or not audio_frames:
            return b""

        return b"".join(audio_frames)


default_vad = VoiceActivityDetector()
