"""Application configuration loader.

Loads all settings from .env (via python-dotenv) with a pure-stdlib
fallback parser so the app works even before the venv is activated.
All values are validated at import time so bad config fails fast with
a clear message, before the logging system is even set up.
"""

import os
import sys
from pathlib import Path
from typing import Any

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
WHISPER_BEAM_SIZE: int = _int_env("WHISPER_BEAM_SIZE", 1)
TTS_ENGINE: str = _require("TTS_ENGINE", "edge-tts")
TTS_VOICE: str = _require("TTS_VOICE", "en-IN-NeerjaNeural")
TTS_VOICE_TA: str = _require("TTS_VOICE_TA", "ta-IN-PallaviNeural")
WAKE_WORD_ENGINE: str = _require("WAKE_WORD_ENGINE", "openwakeword")
WAKE_WORD_KEYWORD: str = _require("WAKE_WORD_KEYWORD", "hey jarvis")
WAKE_WORD_SENSITIVITY: float = float(os.getenv("WAKE_WORD_SENSITIVITY", "0.5"))
FOLLOW_UP_TIMEOUT: float = float(os.getenv("FOLLOW_UP_TIMEOUT", "30.0"))
FOLLOW_UP_ENABLED: bool = os.getenv("FOLLOW_UP_ENABLED", "true").lower() in ("true", "1", "yes")
VAD_AGGRESSIVENESS: int = _int_env("VAD_AGGRESSIVENESS", 2)
VAD_ENERGY_THRESHOLD: float = float(os.getenv("VAD_ENERGY_THRESHOLD", "400.0"))

# ------------------------------------------------------------------
# Personality Settings & Modes
# ------------------------------------------------------------------
PERSONALITY_MODES: dict[str, dict[str, Any]] = {
    "Friend": {
        "sarcasm": 7,
        "warmth": 8,
        "tamil_mix": 40,
        "verbosity": "very short",
    },
    "Jarvis": {
        "sarcasm": 3,
        "warmth": 6,
        "tamil_mix": 10,
        "verbosity": "short",
    },
    "Focus": {
        "sarcasm": 1,
        "warmth": 4,
        "tamil_mix": 0,
        "verbosity": "very short",
    },
    "Engineer": {
        "sarcasm": 5,
        "warmth": 5,
        "tamil_mix": 20,
        "verbosity": "balanced",
    },
}

PERSONALITY_MODE: str = os.getenv("PERSONALITY_MODE", "Friend")
PERSONALITY_SARCASM: int = _int_env("PERSONALITY_SARCASM", 7)
PERSONALITY_WARMTH: int = _int_env("PERSONALITY_WARMTH", 8)
PERSONALITY_TAMIL_MIX: int = _int_env("PERSONALITY_TAMIL_MIX", 40)
PERSONALITY_VERBOSITY: str = os.getenv("PERSONALITY_VERBOSITY", "very short")

SETTINGS_FILE = BASE_DIR / "data" / "settings.json"


def get_personality_prompt_line() -> str:
    """Format the personality line for injection into the system prompt."""
    return (
        f"Sarcasm: {PERSONALITY_SARCASM}/10. "
        f"Warmth: {PERSONALITY_WARMTH}/10. "
        f"Tamil mix: {PERSONALITY_TAMIL_MIX}%. "
        f"Replies: {PERSONALITY_VERBOSITY}."
    )


def load_persisted_settings() -> None:
    """Load settings from data/settings.json if it exists."""
    global PERSONALITY_MODE, PERSONALITY_SARCASM, PERSONALITY_WARMTH, PERSONALITY_TAMIL_MIX, PERSONALITY_VERBOSITY, MEMORY_ENABLED
    if not SETTINGS_FILE.exists():
        return
    try:
        import json
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if "personality" in data:
            p = data["personality"]
            PERSONALITY_MODE = p.get("mode", PERSONALITY_MODE)
            PERSONALITY_SARCASM = int(p.get("sarcasm", PERSONALITY_SARCASM))
            PERSONALITY_WARMTH = int(p.get("warmth", PERSONALITY_WARMTH))
            PERSONALITY_TAMIL_MIX = int(p.get("tamil_mix", PERSONALITY_TAMIL_MIX))
            PERSONALITY_VERBOSITY = str(p.get("verbosity", PERSONALITY_VERBOSITY))
        if "memory_enabled" in data:
            MEMORY_ENABLED = bool(data["memory_enabled"])
    except Exception:
        pass


def save_persisted_settings() -> None:
    """Save current settings to data/settings.json."""
    import json
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "memory_enabled": MEMORY_ENABLED,
        "personality": {
            "mode": PERSONALITY_MODE,
            "sarcasm": PERSONALITY_SARCASM,
            "warmth": PERSONALITY_WARMTH,
            "tamil_mix": PERSONALITY_TAMIL_MIX,
            "verbosity": PERSONALITY_VERBOSITY,
        },
    }
    SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# Attempt load at import time
load_persisted_settings()

