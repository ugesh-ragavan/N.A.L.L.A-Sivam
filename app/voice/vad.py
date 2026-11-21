"""app/voice/vad.py

Voice Activity Detection (VAD) using webrtcvad.

Monitors incoming 16 kHz audio frames, detects when speech begins,
and terminates recording when silence exceeds a threshold (~0.8s),
capping at a maximum duration of 20 seconds.
"""

from __future__ import annotations

import collections
import logging
import time
from typing import Any, Callable

import webrtcvad

from app.config import VAD_AGGRESSIVENESS
from app.voice.audio_io import AudioRecorder, DEFAULT_SAMPLE_RATE

logger = logging.getLogger("nalla.voice.vad")

# Frame size: 20 ms @ 16 kHz mono int16 = 320 samples = 640 bytes
FRAME_DURATION_MS = 20
SAMPLES_PER_FRAME = int(DEFAULT_SAMPLE_RATE * (FRAME_DURATION_MS / 1000.0))
BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2  # 16-bit = 2 bytes per sample


class VoiceActivityDetector:
    """Detects speech and silence intervals in a stream of PCM16 frames."""

    def __init__(self, aggressiveness: int | None = None) -> None:
        agg = aggressiveness if aggressiveness is not None else VAD_AGGRESSIVENESS
        self.aggressiveness = max(0, min(3, agg))
        self._vad = webrtcvad.Vad(self.aggressiveness)

    def is_speech(self, pcm_frame: bytes, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bool:
        """Evaluate whether a single frame contains active speech."""
        if len(pcm_frame) != BYTES_PER_FRAME:
            # Pad or truncate if needed for webrtcvad strict sizing
            if len(pcm_frame) < BYTES_PER_FRAME:
                pcm_frame = pcm_frame.ljust(BYTES_PER_FRAME, b"\x00")
            else:
                pcm_frame = pcm_frame[:BYTES_PER_FRAME]
        try:
            return self._vad.is_speech(pcm_frame, sample_rate)
        except Exception as exc:
            logger.debug("VAD frame evaluation error: %s", exc)
            return False

    def record_speech_segment(
        self,
        recorder: AudioRecorder | None = None,
        silence_duration: float = 0.8,
        max_duration: float = 20.0,
        initial_timeout: float = 5.0,
        state_callback: Callable[[str], None] | None = None,
    ) -> bytes:
        """Capture microphone audio until the user stops speaking.

        Parameters
        ----------
        recorder: AudioRecorder instance (uses default if None).
        silence_duration: Seconds of consecutive silence to conclude speech (default 0.8s).
        max_duration: Maximum allowed recording duration in seconds (default 20s).
        initial_timeout: Max wait for speech to commence before aborting (default 5s).
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

        frame_queue: collections.deque[bytes] = collections.deque()

        def _on_chunk(chunk_bytes: bytes) -> None:
            # Slice chunk into exact 20 ms frames
            idx = 0
            while idx + BYTES_PER_FRAME <= len(chunk_bytes):
                frame_queue.append(chunk_bytes[idx : idx + BYTES_PER_FRAME])
                idx += BYTES_PER_FRAME

        stream = rec.open_stream(callback=_on_chunk, blocksize=SAMPLES_PER_FRAME)

        try:
            if state_callback:
                state_callback("waiting")

            while total_frames < max_total_frames:
                if not frame_queue:
                    time.sleep(0.01)
                    continue

                frame = frame_queue.popleft()
                total_frames += 1
                has_speech = self.is_speech(frame)

                if not speech_started:
                    ring_buffer.append(frame)
                    if has_speech:
                        speech_started = True
                        if state_callback:
                            state_callback("speaking")
                        logger.info("Speech onset detected by VAD.")
                        audio_frames.extend(ring_buffer)
                    elif total_frames > initial_timeout_frames:
                        logger.debug("Initial silence timeout reached without speech.")
                        break
                else:
                    audio_frames.append(frame)
                    if has_speech:
                        consecutive_silence = 0
                    else:
                        consecutive_silence += 1
                        if consecutive_silence >= silence_frames_threshold:
                            logger.info("Speech end detected after %.2fs of silence.", silence_duration)
                            break

        finally:
            rec.close_stream()

        if not speech_started or not audio_frames:
            return b""

        return b"".join(audio_frames)


default_vad = VoiceActivityDetector()
