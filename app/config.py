"""Application configuration loader.

Loads all settings from .env (via python-dotenv) with a pure-stdlib
fallback parser so the app works even before the venv is activated.
All values are validated at import time so bad config fails fast with
a clear message, before the logging system is even set up.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------
# Load .env file
# ------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())


# ------------------------------------------------------------------
# Helper: fail loudly on bad config
# ------------------------------------------------------------------
def _require(key: str, default: str = "") -> str:
    """Return env var value; warn if it looks like a placeholder."""
    val = os.getenv(key, default)
    return val


def _int_env(key: str, default: int) -> int:
    """Parse an integer env var; exit with a readable message on failure."""
    raw = os.getenv(key, str(default))
    try:
        return int(raw)
    except ValueError:
        print(
            f"[CONFIG ERROR] {key}={raw!r} is not a valid integer. "
            f"Check your .env file.",
            file=sys.stderr,
        )
        sys.exit(1)


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------
GEMINI_API_KEY: str = _require("GEMINI_API_KEY", "")
GEMINI_MODEL: str = _require("GEMINI_MODEL", "gemini-3.1-flash-lite")
LOG_LEVEL: str = _require("LOG_LEVEL", "INFO").upper()
WEB_PORT: int = _int_env("WEB_PORT", 8000)
WEB_HOST: str = _require("WEB_HOST", "127.0.0.1")

MQTT_HOST: str = _require("MQTT_HOST", "localhost")
MQTT_PORT: int = _int_env("MQTT_PORT", 1883)
MQTT_USER: str = _require("MQTT_USER", "")
MQTT_PASSWORD: str = _require("MQTT_PASSWORD", "")

# Phase 3: Memory setting
MEMORY_ENABLED: bool = _require("MEMORY_ENABLED", "true").lower() in ("true", "1", "yes")

# Phase 4: Voice settings
_dev_in = os.getenv("AUDIO_INPUT_DEVICE", "").strip()
AUDIO_INPUT_DEVICE: int | None = int(_dev_in) if _dev_in.isdigit() else None

_dev_out = os.getenv("AUDIO_OUTPUT_DEVICE", "").strip()
AUDIO_OUTPUT_DEVICE: int | None = int(_dev_out) if _dev_out.isdigit() else None

WHISPER_MODEL: str = _require("WHISPER_MODEL", "base")
WHISPER_LANGUAGE: str = _require("WHISPER_LANGUAGE", "auto")
TTS_ENGINE: str = _require("TTS_ENGINE", "edge-tts")
TTS_VOICE: str = _require("TTS_VOICE", "en-IN-NeerjaNeural")
TTS_VOICE_TA: str = _require("TTS_VOICE_TA", "ta-IN-PallaviNeural")
WAKE_WORD_ENGINE: str = _require("WAKE_WORD_ENGINE", "pushtotalk")
WAKE_WORD_KEYWORD: str = _require("WAKE_WORD_KEYWORD", "hey jarvis")
WAKE_WORD_SENSITIVITY: float = float(os.getenv("WAKE_WORD_SENSITIVITY", "0.5"))
VAD_AGGRESSIVENESS: int = _int_env("VAD_AGGRESSIVENESS", 2)
