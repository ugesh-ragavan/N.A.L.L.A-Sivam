"""app/voice/audio_dsp.py

Digital Signal Processing (DSP) utilities for natural voice audio:
• apply_edge_fade: 15-20ms cosine fade-in/out at chunk edges to eliminate pops and clicks.
• normalize_loudness_rms: consistent volume across engines and sentences without clipping.
• resample_to_target_rate: ensures a constant 24,000 Hz sample rate end to end.
• apply_voice_fx: Off | Clean | JARVIS audio polish (presence high-shelf + subtle lab intercom impulse).
"""

from __future__ import annotations

import logging
import numpy as np

logger = logging.getLogger("nalla.voice.audio_dsp")

TARGET_SAMPLE_RATE = 24000


def apply_edge_fade(audio_arr: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE, fade_ms: float = 15.0) -> np.ndarray:
    """Apply a smooth raised-cosine fade-in and fade-out at chunk edges to eliminate clicks and pops."""
    if len(audio_arr) == 0:
        return audio_arr
    fade_len = int(sample_rate * (fade_ms / 1000.0))
    if len(audio_arr) < fade_len * 2:
        fade_len = max(0, len(audio_arr) // 2)
    if fade_len <= 0:
        return audio_arr

    # Raised-cosine (Hanning) curve: 0.0 -> 1.0
    fade_in = 0.5 * (1.0 - np.cos(np.pi * np.arange(fade_len) / fade_len))
    # Raised-cosine curve: 1.0 -> 0.0
    fade_out = 0.5 * (1.0 + np.cos(np.pi * np.arange(fade_len) / fade_len))

    out = np.array(audio_arr, copy=True, dtype=np.float32)
    if out.ndim == 1:
        out[:fade_len] *= fade_in
        out[-fade_len:] *= fade_out
    else:
        out[:fade_len] *= fade_in[:, np.newaxis]
        out[-fade_len:] *= fade_out[:, np.newaxis]
    return out


def normalize_loudness_rms(audio_arr: np.ndarray, target_rms: float = 0.15, max_gain: float = 5.0) -> np.ndarray:
    """Normalize audio array to consistent target RMS volume with soft saturation limiting."""
    if len(audio_arr) == 0:
        return audio_arr
    rms = float(np.sqrt(np.mean(np.square(audio_arr))))
    if rms < 1e-4:
        return audio_arr  # Silent frame

    gain = min(target_rms / rms, max_gain)
    scaled = audio_arr * gain

    # Soft tanh saturation if peaks exceed 0.95 to avoid harsh digital clipping
    max_val = float(np.max(np.abs(scaled)))
    if max_val > 0.95:
        scaled = np.tanh(scaled / 0.95) * 0.95

    return scaled.astype(np.float32)


def resample_to_target_rate(arr: np.ndarray, orig_rate: int, target_rate: int = TARGET_SAMPLE_RATE) -> np.ndarray:
    """Resample audio array to target_rate (default 24 kHz) float32."""
    if orig_rate == target_rate or len(arr) == 0:
        return arr.astype(np.float32)

    try:
        from scipy import signal
        num_samples = int(round(len(arr) * float(target_rate) / orig_rate))
        return signal.resample(arr, num_samples).astype(np.float32)
    except Exception:
        # Fast linear interpolation fallback (pure NumPy, zero scipy required on Pi)
        num_samples = int(round(len(arr) * float(target_rate) / orig_rate))
        orig_indices = np.linspace(0, len(arr) - 1, len(arr))
        target_indices = np.linspace(0, len(arr) - 1, num_samples)
        if arr.ndim == 1:
            return np.interp(target_indices, orig_indices, arr).astype(np.float32)
        cols = [np.interp(target_indices, orig_indices, arr[:, ch]) for ch in range(arr.shape[1])]
        return np.column_stack(cols).astype(np.float32)


def apply_voice_fx(audio_arr: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE, fx_mode: str = "off") -> np.ndarray:
    """Apply voice FX preset: 'off' (raw), 'clean' (sub-bass rumble cut), or 'jarvis' (presence + holographic polish)."""
    mode = (fx_mode or "off").lower().strip()
    if mode in ("off", "none", "disabled") or len(audio_arr) == 0:
        return audio_arr

    mono = audio_arr if audio_arr.ndim == 1 else audio_arr.mean(axis=1)
    processed = np.array(mono, copy=True, dtype=np.float32)

    if mode in ("clean", "jarvis"):
        # Gentle DC / sub-bass highpass (~80 Hz) using a 1-pole recursive filter: y[n] = x[n] - x[n-1] + R*y[n-1]
        rc = 1.0 / (2.0 * np.pi * 80.0)
        dt = 1.0 / sample_rate
        alpha = rc / (rc + dt)
        filtered = np.empty_like(processed)
        filtered[0] = processed[0]
        for i in range(1, len(processed)):
            filtered[i] = alpha * (filtered[i - 1] + processed[i] - processed[i - 1])
        processed = filtered

    if mode == "jarvis":
        # 1. Subtle high-shelf presence polish (~4.5 kHz sparkle)
        # Difference emphasis filter: y[n] = x[n] + 0.35 * (x[n] - x[n-1])
        diff = np.empty_like(processed)
        diff[0] = processed[0]
        diff[1:] = processed[1:] - processed[:-1]
        presence = processed + 0.35 * diff

        # 2. Subtle holographic intercom room impulse (18ms and 28ms early reflections)
        delay_1 = int(sample_rate * 0.018)  # 18 ms
        delay_2 = int(sample_rate * 0.028)  # 28 ms
        reverb = np.zeros_like(presence)

        if len(presence) > delay_1:
            reverb[delay_1:] += 0.16 * presence[:-delay_1]
        if len(presence) > delay_2:
            reverb[delay_2:] += 0.09 * presence[:-delay_2]

        # 85% dry + 15% wet polish
        mixed = 0.85 * presence + 0.15 * reverb
        processed = np.tanh(mixed / 0.95) * 0.95

    return processed.astype(np.float32)
