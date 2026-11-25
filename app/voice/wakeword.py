"""app/voice/wakeword.py

Wake-word detection interfaces and implementations.

Supported modes:
  1. PushToTalk: Triggered manually via keyboard hotkey or dashboard button.
  2. OpenWakeWordDetector: Lightweight offline keyword spotting using openWakeWord
     (built-in model: 'hey jarvis' or 'alexa').

TODO: Custom "Nalla" Wake Word Model
-------------------------------------
To train a custom "Nalla" (நல்ல) wake word model for production:
  1. Follow the openWakeWord Google Colab training notebook:
     https://github.com/dscripka/openWakeWord
  2. Generate synthetic audio clips for "Nalla" (and common variations).
  3. Export the trained model as `nalla.tflite` or `nalla.onnx`.
  4. Place it in `data/models/wakeword/nalla.tflite` and set:
     `WAKE_WORD_KEYWORD=data/models/wakeword/nalla.tflite` in your `.env`.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

import numpy as np

from app.config import (
    WAKE_WORD_ENGINE,
    WAKE_WORD_KEYWORD,
    WAKE_WORD_SENSITIVITY,
)
from app.voice.audio_io import AudioRecorder, DEFAULT_SAMPLE_RATE

logger = logging.getLogger("nalla.voice.wakeword")


class WakeWordDetector(ABC):
    """Abstract interface for wake-word engines."""

    @abstractmethod
    def get_name(self) -> str:
        """Return the human-readable engine name."""
        ...

    @abstractmethod
    def start(self, on_detected: Callable[[], None]) -> None:
        """Start listening asynchronously for the wake word."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop listening for the wake word."""
        ...

    @abstractmethod
    def wait_for_detection(self, timeout: float | None = None) -> bool:
        """Block until the wake word is detected or timeout expires."""
        ...


class PushToTalk(WakeWordDetector):
    """Manual software trigger for keyboard and web dashboard."""
    engine_name: str = "pushtotalk"

    def __init__(self) -> None:
        self._triggered = threading.Event()
        self._callback: Callable[[], None] | None = None

    def get_name(self) -> str:
        return "Push-to-Talk"

    def trigger(self) -> None:
        """Signal that push-to-talk was pressed."""
        logger.info("PushToTalk triggered.")
        self._triggered.set()
        if self._callback:
            self._callback()

    def start(self, on_detected: Callable[[], None]) -> None:
        self._callback = on_detected

    def stop(self) -> None:
        self._callback = None
        self._triggered.clear()

    def wait_for_detection(self, timeout: float | None = None) -> bool:
        detected = self._triggered.wait(timeout=timeout)
        if detected:
            self._triggered.clear()
        return detected


class OpenWakeWordDetector(WakeWordDetector):
    """Continuous microphone stream monitoring using openWakeWord."""
    engine_name: str = "openwakeword"

    def __init__(
        self,
        keyword: str | None = None,
        sensitivity: float | None = None,
        recorder: AudioRecorder | None = None,
    ) -> None:
        self.keyword = keyword or WAKE_WORD_KEYWORD
        self.sensitivity = sensitivity if sensitivity is not None else WAKE_WORD_SENSITIVITY
        self.recorder = recorder or AudioRecorder()
        self._stop_event = threading.Event()
        self._detected_event = threading.Event()
        self._callback: Callable[[], None] | None = None
        self._thread: threading.Thread | None = None
        self._model = None

    def get_name(self) -> str:
        return "openWakeWord"

    def _init_model(self):
        if self._model is None:
            import openwakeword
            import openwakeword.utils
            from openwakeword.model import Model

            # Check if custom model file or built-in model
            kw = (self.keyword or "hey jarvis").strip()
            if kw.endswith(".onnx") or kw.endswith(".tflite") or "/" in kw or "\\" in kw:
                model_path = kw
                framework = "tflite" if kw.endswith(".tflite") else "onnx"
            else:
                model_name = kw.lower().replace(" ", "_")
                if "jarvis" in model_name:
                    model_path = "hey_jarvis_v0.1.onnx"
                elif "alexa" in model_name:
                    model_path = "alexa_v0.1.onnx"
                else:
                    model_path = "hey_jarvis_v0.1.onnx"
                framework = "onnx"

            # Auto-download prebuilt model file if missing
            if framework == "onnx" and not ("/" in model_path or "\\" in model_path):
                try:
                    openwakeword.utils.download_models([model_path])
                except Exception as dl_err:
                    logger.debug("Prebuilt model download check: %s", dl_err)

            logger.info(
                "Initializing openWakeWord model: %s (framework: %s, sensitivity: %.2f)",
                model_path,
                framework,
                self.sensitivity,
            )
            self._model = Model(wakeword_models=[model_path], inference_framework=framework)
        return self._model

    def update_settings(self, keyword: str | None = None, sensitivity: float | None = None) -> None:
        """Update keyword or sensitivity and reload model if changed."""
        changed = False
        if keyword is not None and keyword != self.keyword:
            self.keyword = keyword
            self._model = None
            changed = True
        if sensitivity is not None:
            self.sensitivity = float(sensitivity)
        if changed and self._thread and self._thread.is_alive():
            # Restart listener
            cb = self._callback
            self.stop()
            if cb:
                self.start(cb)

    def _listen_loop(self) -> None:
        model = self._init_model()
        chunk_size = 1280  # 80ms @ 16kHz
        self._stop_event.clear()

        def _on_audio(chunk_bytes: bytes):
            if self._stop_event.is_set():
                return
            audio_data = np.frombuffer(chunk_bytes, dtype=np.int16)
            prediction = model.predict(audio_data)

            for mdl, score in prediction.items():
                if score >= self.sensitivity:
                    logger.info("Wake word '%s' detected! (Score: %.3f)", mdl, score)
                    self._detected_event.set()
                    if self._callback:
                        self._callback()
                    model.reset()

        stream = self.recorder.open_stream(callback=_on_audio, blocksize=chunk_size)
        try:
            while not self._stop_event.is_set():
                time.sleep(0.05)
        finally:
            self.recorder.close_stream()

    def start(self, on_detected: Callable[[], None]) -> None:
        self._callback = on_detected
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="WakeWordListener")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._callback = None

    def wait_for_detection(self, timeout: float | None = None) -> bool:
        detected = self._detected_event.wait(timeout=timeout)
        if detected:
            self._detected_event.clear()
        return detected


def get_wakeword_detector(engine_name: str | None = None) -> WakeWordDetector:
    """Factory to construct the configured wake word detector."""
    engine = (engine_name or WAKE_WORD_ENGINE).lower()
    if engine in ("openwakeword", "wakeword"):
        try:
            return OpenWakeWordDetector()
        except Exception as exc:
            logger.warning("Failed to initialize OpenWakeWord (%s) — falling back to PushToTalk.", exc)
            return PushToTalk()
    return PushToTalk()


default_wakeword: WakeWordDetector = get_wakeword_detector()


def update_wakeword_settings(
    engine: str | None = None,
    keyword: str | None = None,
    sensitivity: float | None = None,
) -> WakeWordDetector:
    """Update wake word settings and return the active detector instance."""
    global default_wakeword
    from app.config import save_wakeword_settings

    save_wakeword_settings({
        "engine": engine,
        "keyword": keyword,
        "sensitivity": sensitivity,
    })

    if engine and engine.lower() != getattr(default_wakeword, "engine_name", "").lower():
        default_wakeword.stop()
        default_wakeword = get_wakeword_detector(engine)
    elif isinstance(default_wakeword, OpenWakeWordDetector):
        default_wakeword.update_settings(keyword=keyword, sensitivity=sensitivity)

    return default_wakeword
