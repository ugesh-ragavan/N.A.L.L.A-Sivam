"""app/voice/global_hotkey.py

Optional system-wide global hotkey for Walkie-Talkie mode using pynput.

Allows user to hold a global function key (default: F9) from any desktop application
without focusing the web browser.

Security & Identity Guard:
  • Default is OFF.
  • Never maps plain 'Space' globally (which would hijack regular typing everywhere).
  • Configurable key (e.g. F9, F10, F8).
  • Non-blocking background thread.
  • Graceful degradation if pynput is not installed or on platforms without permissions.

OS Notes:
  • Windows: Works out of the box with standard desktop privileges.
  • macOS: Requires granting Accessibility permissions under System Preferences -> Privacy & Security -> Accessibility.
  • Linux: Supported on X11. Under Wayland, global hotkey listening is restricted by compositor security models.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Any

logger = logging.getLogger("nalla.voice.hotkey")


def is_pynput_available() -> bool:
    """Return True if pynput library is installed and available."""
    try:
        import pynput  # noqa: F401
        return True
    except ImportError:
        return False


class GlobalHotkeyManager:
    """Manages background global hotkey listener for walkie-talkie mode."""

    def __init__(
        self,
        on_down: Callable[[], Any] | None = None,
        on_up: Callable[[], Any] | None = None,
        key_name: str = "F9",
        enabled: bool = False,
    ) -> None:
        self.on_down = on_down
        self.on_up = on_up
        self.key_name = key_name.upper().strip()
        self.enabled = enabled
        self._listener = None
        self._is_holding = False
        self._lock = threading.Lock()

    def configure(self, enabled: bool, key_name: str | None = None) -> None:
        """Update hotkey configuration live."""
        with self._lock:
            self.enabled = bool(enabled)
            if key_name:
                self.key_name = key_name.upper().strip()

            if self.enabled:
                self.start()
            else:
                self.stop()

    def start(self) -> bool:
        """Start global hotkey listener if pynput is available."""
        if not is_pynput_available():
            logger.debug("pynput not installed; global hotkey is disabled.")
            return False

        if self._listener is not None:
            return True

        try:
            from pynput import keyboard

            target_key_name = self.key_name

            def _get_key_identifier(k):
                try:
                    if hasattr(k, "name") and k.name:
                        return k.name.upper()
                    if hasattr(k, "char") and k.char:
                        return k.char.upper()
                except Exception:
                    pass
                return str(k).upper().replace("KEY.", "")

            def _on_press(k):
                ident = _get_key_identifier(k)
                if ident == target_key_name:
                    with self._lock:
                        if not self._is_holding:
                            self._is_holding = True
                            logger.debug("Global hotkey %s DOWN", ident)
                            if self.on_down:
                                self.on_down()

            def _on_release(k):
                ident = _get_key_identifier(k)
                if ident == target_key_name:
                    with self._lock:
                        if self._is_holding:
                            self._is_holding = False
                            logger.debug("Global hotkey %s UP", ident)
                            if self.on_up:
                                self.on_up()

            self._listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
            self._listener.daemon = True
            self._listener.start()
            logger.info("Global hotkey listener started for key '%s'", self.key_name)
            return True
        except Exception as exc:
            logger.warning("Could not start global hotkey listener: %s", exc)
            self._listener = None
            return False

    def stop(self) -> None:
        """Stop global hotkey listener."""
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None
                self._is_holding = False
                logger.info("Global hotkey listener stopped.")


# Module singleton attached to default pipeline
from app.voice.pipeline import default_pipeline
from app import config as _cfg

hotkey_manager = GlobalHotkeyManager(
    on_down=default_pipeline.ptt_down,
    on_up=default_pipeline.ptt_up,
    key_name=getattr(_cfg, "GLOBAL_HOTKEY_KEY", "F9"),
    enabled=getattr(_cfg, "GLOBAL_HOTKEY_ENABLED", False),
)

if getattr(_cfg, "GLOBAL_HOTKEY_ENABLED", False):
    hotkey_manager.start()
