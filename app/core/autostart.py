"""app/core/autostart.py

Auto-Start and Single-Instance Lock Manager (Prompt 5C).

Features:
- Single-instance process lock to prevent concurrent running instances.
- Auto-start on user login (Windows Registry HKCU Run key, Linux systemd/desktop, macOS launchd).
- System tray icon integration with pystray (with graceful fallback if not installed).
- Clean resource acquisition and release.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable

from app.config import BASE_DIR

logger = logging.getLogger("nalla.autostart")

_DEFAULT_LOCK_FILE = BASE_DIR / "data" / "nalla.lock"


class SingleInstanceLock:
    """Manages an exclusive lock file to guarantee single-instance execution."""

    def __init__(self, lock_file: Path | str | None = None) -> None:
        self.lock_file = Path(lock_file or _DEFAULT_LOCK_FILE)
        self._fd: int | None = None
        self._is_locked = False

    def acquire(self) -> bool:
        """Acquire single-instance lock.

        Raises RuntimeError if another instance holds the lock.
        """
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            if sys.platform == "win32":
                import msvcrt

                # Open file with read/write access
                self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)
                try:
                    # Non-blocking lock 1 byte from start
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                except (OSError, IOError) as exc:
                    os.close(self._fd)
                    self._fd = None
                    raise RuntimeError(
                        f"Another instance of NALLA SIVAM is already running (locked {self.lock_file})."
                    ) from exc
            else:
                import fcntl

                self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, IOError) as exc:
                    os.close(self._fd)
                    self._fd = None
                    raise RuntimeError(
                        f"Another instance of NALLA SIVAM is already running (locked {self.lock_file})."
                    ) from exc

            # Write current PID into lock file
            os.ftruncate(self._fd, 0)
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, f"{os.getpid()}\n".encode("utf-8"))
            self._is_locked = True
            logger.info("Acquired single-instance lock for PID %d (%s)", os.getpid(), self.lock_file)
            return True

        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            logger.error("Failed to acquire single instance lock: %s", exc)
            raise RuntimeError(f"Could not acquire instance lock: {exc}") from exc

    def release(self) -> None:
        """Release the instance lock and remove the lockfile."""
        if not self._is_locked or self._fd is None:
            return

        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except Exception:
                    pass

            os.close(self._fd)
            self._fd = None
            self._is_locked = False

            if self.lock_file.exists():
                try:
                    self.lock_file.unlink()
                except Exception:
                    pass
            logger.info("Released single-instance lock.")
        except Exception as exc:
            logger.warning("Error releasing instance lock: %s", exc)

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Auto-Start Manager (Windows Run Key & Cross-platform Stubs)
# ---------------------------------------------------------------------------

_REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_REG_NAME = "NALLASivam"


def get_autostart_status() -> dict[str, Any]:
    """Check if NALLA SIVAM is configured to auto-start on user login."""
    if sys.platform != "win32":
        return {
            "platform": sys.platform,
            "enabled": False,
            "supported": False,
            "details": "Auto-start registry is Windows specific. Use systemd or cron on Linux/macOS.",
        }

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_READ) as key:
            try:
                val, _ = winreg.QueryValueEx(key, _APP_REG_NAME)
                return {
                    "platform": "win32",
                    "enabled": True,
                    "supported": True,
                    "command": val,
                }
            except FileNotFoundError:
                return {
                    "platform": "win32",
                    "enabled": False,
                    "supported": True,
                    "command": None,
                }
    except Exception as exc:
        logger.warning("Error querying auto-start registry: %s", exc)
        return {
            "platform": "win32",
            "enabled": False,
            "supported": True,
            "error": str(exc),
        }


def set_autostart(enable: bool, custom_cmd: str | None = None) -> bool:
    """Enable or disable auto-start on user login."""
    if sys.platform != "win32":
        logger.info("Auto-start toggle only supported natively on Windows.")
        return False

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                cmd = custom_cmd or f'"{sys.executable}" -m app'
                winreg.SetValueEx(key, _APP_REG_NAME, 0, winreg.REG_SZ, cmd)
                logger.info("Enabled auto-start with command: %s", cmd)
            else:
                try:
                    winreg.DeleteValue(key, _APP_REG_NAME)
                    logger.info("Disabled auto-start.")
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        logger.error("Failed to update auto-start setting: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Tray Icon Helper (pystray)
# ---------------------------------------------------------------------------

class TrayIconController:
    """Manages system tray icon with status color and quick actions."""

    def __init__(
        self,
        on_open_dashboard: Callable[[], None] | None = None,
        on_toggle_mute: Callable[[], bool] | None = None,
        on_toggle_dnd: Callable[[], bool] | None = None,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        self.on_open = on_open_dashboard
        self.on_mute = on_toggle_mute
        self.on_dnd = on_toggle_dnd
        self.on_quit = on_quit
        self.icon = None
        self._status = "healthy"  # healthy (green), degraded (yellow), alert (red)

    @property
    def is_available(self) -> bool:
        """Check if pystray and PIL are available in current environment."""
        try:
            import PIL.Image  # noqa: F401
            import pystray  # noqa: F401
            return True
        except ImportError:
            return False

    def update_status(self, status: str) -> None:
        """Update status color ('healthy' | 'degraded' | 'alert')."""
        self._status = status
        if self.icon and self.is_available:
            try:
                self.icon.icon = self._create_colored_icon(status)
            except Exception as exc:
                logger.debug("Could not update tray icon image: %s", exc)

    def _create_colored_icon(self, status: str):
        """Create a 64x64 colored circle icon."""
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Color mapping
        colors = {
            "healthy": (74, 222, 128, 255),   # Emerald green
            "degraded": (250, 204, 21, 255),  # Amber yellow
            "alert": (248, 113, 113, 255),    # Coral red
        }
        color = colors.get(status, (96, 165, 250, 255))

        # Concentric glowing orb
        draw.ellipse((4, 4, 60, 60), fill=(15, 23, 42, 220), outline=color, width=3)
        draw.ellipse((18, 18, 46, 46), fill=color)
        return img

    def start_in_background(self) -> bool:
        """Launch tray icon in a dedicated daemon thread."""
        if not self.is_available:
            logger.info("pystray or Pillow not installed; system tray icon will not run.")
            return False

        import threading
        try:
            import pystray

            menu_items = [
                pystray.MenuItem("Open Dashboard", lambda icon, item: self.on_open and self.on_open()),
                pystray.MenuItem("Toggle Mute", lambda icon, item: self.on_mute and self.on_mute()),
                pystray.MenuItem("Toggle DND", lambda icon, item: self.on_dnd and self.on_dnd()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit NALLA SIVAM", lambda icon, item: self._handle_quit(icon)),
            ]

            self.icon = pystray.Icon(
                "nalla_sivam",
                self._create_colored_icon(self._status),
                "NALLA SIVAM - Assistant",
                pystray.Menu(*menu_items),
            )

            threading.Thread(target=self.icon.run, daemon=True, name="NallaTrayThread").start()
            logger.info("System tray icon started.")
            return True
        except Exception as exc:
            logger.warning("Could not start tray icon: %s", exc)
            return False

    def _handle_quit(self, icon) -> None:
        try:
            icon.stop()
        except Exception:
            pass
        if self.on_quit:
            self.on_quit()
        else:
            sys.exit(0)


single_instance_lock = SingleInstanceLock()
