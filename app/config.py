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
# Two model slots: chat (conversation) and command (tool calls).
# Default both to gemini-3.5-flash-lite; set GEMINI_CHAT_MODEL / GEMINI_COMMAND_MODEL in .env to override.
GEMINI_MODEL: str = _require("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_CHAT_MODEL: str = _require("GEMINI_CHAT_MODEL", "") or GEMINI_MODEL
GEMINI_COMMAND_MODEL: str = _require("GEMINI_COMMAND_MODEL", "") or GEMINI_MODEL
GEMINI_TEMPERATURE: float = float(os.getenv("GEMINI_TEMPERATURE", "0.95"))
EXPRESSIVE_TAGS: bool = os.getenv("EXPRESSIVE_TAGS", "false").lower() in ("true", "1", "yes")
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
# Comma-separated list of ISO language codes Whisper may detect.
# "auto" = unrestricted. Otherwise only results whose language is in the list are accepted.
_listen_langs_raw = os.getenv("LISTEN_LANGUAGES", "en,ta").strip()
LISTEN_LANGUAGES: list[str] = [l.strip() for l in _listen_langs_raw.split(",") if l.strip()] or ["en", "ta"]
WHISPER_BEAM_SIZE: int = _int_env("WHISPER_BEAM_SIZE", 1)
TTS_ENGINE: str = _require("TTS_ENGINE", "edge-tts")
TTS_VOICE: str = _require("TTS_VOICE", "en-IN-NeerjaNeural")
TTS_VOICE_TA: str = _require("TTS_VOICE_TA", "ta-IN-PallaviNeural")
FORCE_TTS_ENGINE: str = _require("FORCE_TTS_ENGINE", "auto")  # auto | gemini | edge-tts | piper
GEMINI_TTS_MODEL: str = _require("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts")  # gemini-3.8-flash-lite-tts | gemini-3.8-flash-tts
GEMINI_TTS_STYLE: str = _require("GEMINI_TTS_STYLE", "calm, confident, dry wit, conversational")
VOICE_FX: str = _require("VOICE_FX", "off")  # off | clean | jarvis
WAKE_WORD_ENGINE: str = _require("WAKE_WORD_ENGINE", "openwakeword")
WAKE_WORD_KEYWORD: str = _require("WAKE_WORD_KEYWORD", "hey jarvis")
WAKE_WORD_SENSITIVITY: float = float(os.getenv("WAKE_WORD_SENSITIVITY", "0.5"))
FOLLOW_UP_TIMEOUT: float = float(os.getenv("FOLLOW_UP_TIMEOUT", "30.0"))
FOLLOW_UP_ENABLED: bool = os.getenv("FOLLOW_UP_ENABLED", "true").lower() in ("true", "1", "yes")
VAD_AGGRESSIVENESS: int = _int_env("VAD_AGGRESSIVENESS", 2)
VAD_ENERGY_THRESHOLD: float = float(os.getenv("VAD_ENERGY_THRESHOLD", "400.0"))

# Walkie-Talkie & Interaction Controls (Prompt A)
TALK_MODE: str = _require("TALK_MODE", "walkie_talkie")  # walkie_talkie | tap_toggle | wake_word
KEEP_MIC_WARM: bool = os.getenv("KEEP_MIC_WARM", "false").lower() in ("true", "1", "yes")
GLOBAL_HOTKEY_ENABLED: bool = os.getenv("GLOBAL_HOTKEY_ENABLED", "false").lower() in ("true", "1", "yes")
GLOBAL_HOTKEY_KEY: str = _require("GLOBAL_HOTKEY_KEY", "F9")
STREAMING_STT: bool = os.getenv("STREAMING_STT", "false").lower() in ("true", "1", "yes")
SOFT_CLICK_SOUND: bool = os.getenv("SOFT_CLICK_SOUND", "true").lower() in ("true", "1", "yes")

# Tamil Text Mode & Speech-to-Text (Prompt E)
TAMIL_TEXT_MODE: str = _require("TAMIL_TEXT_MODE", "tanglish_display")  # tanglish_display | tamil_script | english_only
KONGU_TOUCH: int = _int_env("KONGU_TOUCH", 2)  # 0 to 10
STT_ENGINE: str = _require("STT_ENGINE", "faster-whisper")  # faster-whisper | gemini
TALK_LOOK: str = _require("TALK_LOOK", "hud")  # hud | minimal

# Short-term memory & Context awareness settings
SHORT_TERM_EXCHANGES: int = int(_int_env("SHORT_TERM_EXCHANGES", 5))  # Range: 1 - 10
SHORT_TERM_ENABLED: bool = _require("SHORT_TERM_ENABLED", "true").lower() in ("true", "1", "yes")
USER_TIMEZONE: str = _require("USER_TIMEZONE", "system")  # "system" or e.g. "Asia/Kolkata", "UTC"
LATE_NIGHT_START: str = _require("LATE_NIGHT_START", "00:30")
LATE_NIGHT_END: str = _require("LATE_NIGHT_END", "05:00")
CARE_NUDGES: bool = _require("CARE_NUDGES", "true").lower() in ("true", "1", "yes")
NUDGE_COOLDOWN_MINUTES: int = int(_int_env("NUDGE_COOLDOWN_MINUTES", 60))
COACH_MODE: bool = _require("COACH_MODE", "false").lower() in ("true", "1", "yes")
GROUNDING_ENABLED: bool = _require("GROUNDING_ENABLED", "false").lower() in ("true", "1", "yes")

# ------------------------------------------------------------------
# Human Mode Settings (Identity Guard v3, Scenarios, Fillers, Logic)
# ------------------------------------------------------------------
VERIFIED_HELPLINE_NUMBERS: str = _require("VERIFIED_HELPLINE_NUMBERS", "")
SCENARIO_ENGINE_ENABLED: bool = _require("SCENARIO_ENGINE_ENABLED", "true").lower() in ("true", "1", "yes")
SCENARIO_STRICTNESS: str = _require("SCENARIO_STRICTNESS", "balanced")  # strict | balanced | loose
SCENARIO_CHATTINESS: str = _require("SCENARIO_CHATTINESS", "sometimes")  # rarely | sometimes | often
MISS_LOG_ENABLED: bool = _require("MISS_LOG_ENABLED", "true").lower() in ("true", "1", "yes")
THINKING_STYLE: str = _require("THINKING_STYLE", "natural")  # off | subtle | natural | chatty
HUMAN_SPEECH_POLISH: bool = _require("HUMAN_SPEECH_POLISH", "true").lower() in ("true", "1", "yes")
QUICK_MODE: bool = False

# ------------------------------------------------------------------
# Presence & Proactive Policy Settings (Prompt 1A & 1B)
# ------------------------------------------------------------------
PROACTIVE_ENABLED: bool = _require("PROACTIVE_ENABLED", "true").lower() in ("true", "1", "yes")
PROACTIVE_QUIET_HOURS_START: str = _require("PROACTIVE_QUIET_HOURS_START", "23:30")
PROACTIVE_QUIET_HOURS_END: str = _require("PROACTIVE_QUIET_HOURS_END", "06:30")
PROACTIVE_DND: bool = _require("PROACTIVE_DND", "false").lower() in ("true", "1", "yes")
PROACTIVE_MAX_PER_HOUR: int = _int_env("PROACTIVE_MAX_PER_HOUR", 2)
PROACTIVE_MAX_PER_DAY: int = _int_env("PROACTIVE_MAX_PER_DAY", 8)
PROACTIVE_SOUND_CUE: bool = _require("PROACTIVE_SOUND_CUE", "true").lower() in ("true", "1", "yes")

PROACTIVE_BRIEFING_ENABLED: bool = _require("PROACTIVE_BRIEFING_ENABLED", "true").lower() in ("true", "1", "yes")
PROACTIVE_CHECK_INS_ENABLED: bool = _require("PROACTIVE_CHECK_INS_ENABLED", "true").lower() in ("true", "1", "yes")
PROACTIVE_WELCOME_BACK_ENABLED: bool = _require("PROACTIVE_WELCOME_BACK_ENABLED", "true").lower() in ("true", "1", "yes")
PROACTIVE_WRAP_UP_ENABLED: bool = _require("PROACTIVE_WRAP_UP_ENABLED", "true").lower() in ("true", "1", "yes")
PROACTIVE_REMINDERS_ENABLED: bool = _require("PROACTIVE_REMINDERS_ENABLED", "true").lower() in ("true", "1", "yes")

AWAY_THRESHOLD_MINUTES: int = _int_env("AWAY_THRESHOLD_MINUTES", 90)
CHECK_IN_INTERVAL_MINUTES: int = _int_env("CHECK_IN_INTERVAL_MINUTES", 90)
STUCK_TASK_HOURS: int = _int_env("STUCK_TASK_HOURS", 2)
MORNING_BRIEFING_TIME: str = _require("MORNING_BRIEFING_TIME", "08:30")
EVENING_WRAP_UP_TIME: str = _require("EVENING_WRAP_UP_TIME", "21:30")

WEATHER_LATITUDE: float = float(os.getenv("WEATHER_LATITUDE", "11.0168"))
WEATHER_LONGITUDE: float = float(os.getenv("WEATHER_LONGITUDE", "76.9558"))

# ------------------------------------------------------------------
# Multi-Step Planner Settings (Prompt 3C)
# ------------------------------------------------------------------
PLANNER_DRY_RUN: bool = _require("PLANNER_DRY_RUN", "false").lower() in ("true", "1", "yes")
PLANNER_MAX_STEPS: int = _int_env("PLANNER_MAX_STEPS", 8)
PLANNER_TIMEOUT_SECONDS: int = _int_env("PLANNER_TIMEOUT_SECONDS", 180)



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

# ------------------------------------------------------------------
# Voice Studio Profiles & Two Voice Slots (Prompt C)
# ------------------------------------------------------------------
DEFAULT_VOICE_PROFILES: dict[str, Any] = {
    "active": "Nalla Default",
    "profiles": {
        "Nalla Default": {
            "voice_slot_en": {
                "engine": "gemini",
                "voice": "achird",
                "rate": 1.0,
                "volume": 100,
            },
            "voice_slot_ta": {
                "engine": "edge-tts",
                "voice": "ta-IN-PallaviNeural",
                "rate": 1.0,
                "volume": 100,
            },
            "auto_switch_language": True,
            "style": "casual, friendly, dry wit",
        },
        "Calm Night": {
            "voice_slot_en": {
                "engine": "gemini",
                "voice": "sulafat",
                "rate": 0.9,
                "volume": 85,
            },
            "voice_slot_ta": {
                "engine": "edge-tts",
                "voice": "ta-IN-PallaviNeural",
                "rate": 0.9,
                "volume": 85,
            },
            "auto_switch_language": True,
            "style": "soft, calm, gentle",
        },
        "Hype": {
            "voice_slot_en": {
                "engine": "gemini",
                "voice": "puck",
                "rate": 1.15,
                "volume": 100,
            },
            "voice_slot_ta": {
                "engine": "edge-tts",
                "voice": "ta-IN-ValluvarNeural",
                "rate": 1.15,
                "volume": 100,
            },
            "auto_switch_language": True,
            "style": "upbeat, energetic, enthusiastic",
        },
        "Nalla Edge": {
            "voice_slot_en": {
                "engine": "edge-tts",
                "voice": "en-IN-NeerjaNeural",
                "rate": 1.0,
                "volume": 100,
            },
            "voice_slot_ta": {
                "engine": "edge-tts",
                "voice": "ta-IN-PallaviNeural",
                "rate": 1.0,
                "volume": 100,
            },
            "auto_switch_language": True,
            "style": "casual, friendly",
        },
        "Offline Piper": {
            "voice_slot_en": {
                "engine": "piper",
                "voice": "en_US-lessac-medium",
                "rate": 1.0,
                "volume": 100,
            },
            "voice_slot_ta": {
                "engine": "edge-tts",
                "voice": "ta-IN-PallaviNeural",
                "rate": 1.0,
                "volume": 100,
            },
            "auto_switch_language": True,
            "style": "neutral",
        },
    },
}

DEFAULT_AUDIO_FEEL: dict[str, Any] = {
    "speed_preset": "Normal",
    "listening_chime": True,
    "done_chime": True,
    "chime_volume": 80,
}

VOICE_PROFILES: dict[str, Any] = dict(DEFAULT_VOICE_PROFILES)
AUDIO_FEEL: dict[str, Any] = dict(DEFAULT_AUDIO_FEEL)


def normalize_voice_profile(prof: dict[str, Any]) -> dict[str, Any]:
    """Ensure voice profile has both voice_slot_en and voice_slot_ta defined cleanly."""
    norm = dict(prof)
    if "voice_slot_en" not in norm or not isinstance(norm["voice_slot_en"], dict):
        norm["voice_slot_en"] = {
            "engine": norm.get("engine", "gemini"),
            "voice": norm.get("voice", "achird"),
            "rate": norm.get("rate", 1.0),
            "volume": norm.get("volume", 100),
        }
    if "voice_slot_ta" not in norm or not isinstance(norm["voice_slot_ta"], dict):
        norm["voice_slot_ta"] = {
            "engine": norm.get("engine_ta", "edge-tts"),
            "voice": norm.get("voice_ta", "ta-IN-PallaviNeural"),
            "rate": norm.get("rate_ta", norm.get("rate", 1.0)),
            "volume": norm.get("volume_ta", norm.get("volume", 100)),
        }
    if "auto_switch_language" not in norm:
        norm["auto_switch_language"] = True

    # Maintain top-level backward compatibility attributes
    norm["engine"] = norm["voice_slot_en"].get("engine", "gemini")
    norm["voice"] = norm["voice_slot_en"].get("voice", "achird")
    norm["voice_ta"] = norm["voice_slot_ta"].get("voice", "ta-IN-PallaviNeural")
    norm["rate"] = norm["voice_slot_en"].get("rate", 1.0)
    norm["volume"] = norm["voice_slot_en"].get("volume", 100)
    return norm


def get_voice_profiles() -> dict[str, Any]:
    """Return current voice profiles configuration with normalized slot structures."""
    active = VOICE_PROFILES.get("active", "Nalla Default")
    profiles = VOICE_PROFILES.get("profiles", {})
    normalized_profiles = {name: normalize_voice_profile(p) for name, p in profiles.items()}
    return {
        "active": active,
        "profiles": normalized_profiles,
    }


def get_active_voice_profile() -> dict[str, Any]:
    """Return the currently active voice profile dict with both voice slots."""
    active_name = VOICE_PROFILES.get("active", "Nalla Default")
    profiles = VOICE_PROFILES.get("profiles", {})
    if active_name in profiles:
        return normalize_voice_profile(profiles[active_name])
    if profiles:
        return normalize_voice_profile(next(iter(profiles.values())))
    return normalize_voice_profile(DEFAULT_VOICE_PROFILES["profiles"]["Nalla Default"])


def save_voice_profiles(data: dict[str, Any]) -> None:
    """Save updated voice profiles (limit to 5) and persist."""
    global VOICE_PROFILES, TTS_VOICE, TTS_VOICE_TA, TTS_ENGINE
    if "profiles" in data:
        # Enforce max 5 profiles and normalize
        items = list(data["profiles"].items())[:5]
        data["profiles"] = {name: normalize_voice_profile(p) for name, p in items}
    VOICE_PROFILES = data
    active_prof = get_active_voice_profile()
    TTS_ENGINE = active_prof["voice_slot_en"].get("engine", TTS_ENGINE)
    TTS_VOICE = active_prof["voice_slot_en"].get("voice", TTS_VOICE)
    TTS_VOICE_TA = active_prof["voice_slot_ta"].get("voice", TTS_VOICE_TA)
    save_persisted_settings()


def get_audio_feel() -> dict[str, Any]:
    """Return audio feel settings (speed presets and chimes)."""
    return AUDIO_FEEL


def save_audio_feel(data: dict[str, Any]) -> None:
    """Save audio feel settings and persist."""
    global AUDIO_FEEL
    AUDIO_FEEL.update(data)
    save_persisted_settings()


def get_stt_settings() -> dict[str, Any]:
    """Return STT and microphone settings."""
    return {
        "whisper_model": WHISPER_MODEL,
        "language": WHISPER_LANGUAGE,
        "listen_languages": LISTEN_LANGUAGES,
        "input_device": AUDIO_INPUT_DEVICE,
        "sensitivity": VAD_ENERGY_THRESHOLD,
    }


def save_stt_settings(data: dict[str, Any]) -> None:
    """Save STT settings and persist."""
    global WHISPER_MODEL, WHISPER_LANGUAGE, LISTEN_LANGUAGES, AUDIO_INPUT_DEVICE, VAD_ENERGY_THRESHOLD
    if "whisper_model" in data and data["whisper_model"] is not None:
        WHISPER_MODEL = str(data["whisper_model"])
    if "listen_languages" in data and data["listen_languages"] is not None and isinstance(data["listen_languages"], list):
        LISTEN_LANGUAGES = [str(x) for x in data["listen_languages"] if x]
        if len(LISTEN_LANGUAGES) == 1:
            WHISPER_LANGUAGE = LISTEN_LANGUAGES[0]
        else:
            WHISPER_LANGUAGE = "auto"
    elif "language" in data and data["language"] is not None:
        WHISPER_LANGUAGE = str(data["language"])
        if WHISPER_LANGUAGE != "auto":
            LISTEN_LANGUAGES = [WHISPER_LANGUAGE]
        else:
            LISTEN_LANGUAGES = ["en", "ta"]
    if "input_device" in data:
        val = data["input_device"]
        AUDIO_INPUT_DEVICE = int(val) if val is not None and str(val).isdigit() else None
    if "sensitivity" in data and data["sensitivity"] is not None:
        VAD_ENERGY_THRESHOLD = float(data["sensitivity"])
    save_persisted_settings()


def get_wakeword_settings() -> dict[str, Any]:
    """Return wake word and follow-up configuration."""
    return {
        "engine": WAKE_WORD_ENGINE,
        "keyword": WAKE_WORD_KEYWORD,
        "sensitivity": WAKE_WORD_SENSITIVITY,
        "follow_up_enabled": FOLLOW_UP_ENABLED,
        "follow_up_timeout": FOLLOW_UP_TIMEOUT,
    }


def save_wakeword_settings(data: dict[str, Any]) -> None:
    """Save wake word and follow-up configuration and persist."""
    global WAKE_WORD_ENGINE, WAKE_WORD_KEYWORD, WAKE_WORD_SENSITIVITY, FOLLOW_UP_ENABLED, FOLLOW_UP_TIMEOUT
    global TALK_MODE, KEEP_MIC_WARM, GLOBAL_HOTKEY_ENABLED, GLOBAL_HOTKEY_KEY, STREAMING_STT, SOFT_CLICK_SOUND
    if "engine" in data and data["engine"] is not None:
        WAKE_WORD_ENGINE = str(data["engine"])
    if "keyword" in data and data["keyword"] is not None:
        WAKE_WORD_KEYWORD = str(data["keyword"])
    if "sensitivity" in data and data["sensitivity"] is not None:
        WAKE_WORD_SENSITIVITY = float(data["sensitivity"])
    if "follow_up_enabled" in data and data["follow_up_enabled"] is not None:
        FOLLOW_UP_ENABLED = bool(data["follow_up_enabled"])
    if "follow_up_timeout" in data and data["follow_up_timeout"] is not None:
        FOLLOW_UP_TIMEOUT = float(data["follow_up_timeout"])
    save_persisted_settings()


def get_talk_mode_settings() -> dict[str, Any]:
    """Return talk mode and walkie-talkie configuration."""
    return {
        "talk_mode": TALK_MODE,
        "keep_mic_warm": KEEP_MIC_WARM,
        "global_hotkey_enabled": GLOBAL_HOTKEY_ENABLED,
        "global_hotkey_key": GLOBAL_HOTKEY_KEY,
        "streaming_stt": STREAMING_STT,
        "soft_click_sound": SOFT_CLICK_SOUND,
    }


def save_talk_mode_settings(data: dict[str, Any]) -> None:
    """Save talk mode configuration and persist."""
    global TALK_MODE, KEEP_MIC_WARM, GLOBAL_HOTKEY_ENABLED, GLOBAL_HOTKEY_KEY, STREAMING_STT, SOFT_CLICK_SOUND
    if "talk_mode" in data and data["talk_mode"] is not None:
        TALK_MODE = str(data["talk_mode"])
    if "keep_mic_warm" in data and data["keep_mic_warm"] is not None:
        KEEP_MIC_WARM = bool(data["keep_mic_warm"])
    if "global_hotkey_enabled" in data and data["global_hotkey_enabled"] is not None:
        GLOBAL_HOTKEY_ENABLED = bool(data["global_hotkey_enabled"])
    if "global_hotkey_key" in data and data["global_hotkey_key"] is not None:
        GLOBAL_HOTKEY_KEY = str(data["global_hotkey_key"])
    if "streaming_stt" in data and data["streaming_stt"] is not None:
        STREAMING_STT = bool(data["streaming_stt"])
    if "soft_click_sound" in data and data["soft_click_sound"] is not None:
        SOFT_CLICK_SOUND = bool(data["soft_click_sound"])
    save_persisted_settings()


def get_presence_settings() -> dict[str, Any]:
    """Return presence and proactive policy configuration."""
    return {
        "proactive_enabled": PROACTIVE_ENABLED,
        "quiet_hours_start": PROACTIVE_QUIET_HOURS_START,
        "quiet_hours_end": PROACTIVE_QUIET_HOURS_END,
        "dnd": PROACTIVE_DND,
        "max_per_hour": PROACTIVE_MAX_PER_HOUR,
        "max_per_day": PROACTIVE_MAX_PER_DAY,
        "sound_cue": PROACTIVE_SOUND_CUE,
        "briefing_enabled": PROACTIVE_BRIEFING_ENABLED,
        "check_ins_enabled": PROACTIVE_CHECK_INS_ENABLED,
        "welcome_back_enabled": PROACTIVE_WELCOME_BACK_ENABLED,
        "wrap_up_enabled": PROACTIVE_WRAP_UP_ENABLED,
        "reminders_enabled": PROACTIVE_REMINDERS_ENABLED,
        "away_threshold_minutes": AWAY_THRESHOLD_MINUTES,
        "check_in_interval_minutes": CHECK_IN_INTERVAL_MINUTES,
        "stuck_task_hours": STUCK_TASK_HOURS,
        "morning_briefing_time": MORNING_BRIEFING_TIME,
        "evening_wrap_up_time": EVENING_WRAP_UP_TIME,
        "weather_latitude": WEATHER_LATITUDE,
        "weather_longitude": WEATHER_LONGITUDE,
    }


def save_presence_settings(data: dict[str, Any]) -> None:
    """Save presence configuration and persist."""
    global PROACTIVE_ENABLED, PROACTIVE_QUIET_HOURS_START, PROACTIVE_QUIET_HOURS_END, PROACTIVE_DND
    global PROACTIVE_MAX_PER_HOUR, PROACTIVE_MAX_PER_DAY, PROACTIVE_SOUND_CUE
    global PROACTIVE_BRIEFING_ENABLED, PROACTIVE_CHECK_INS_ENABLED, PROACTIVE_WELCOME_BACK_ENABLED
    global PROACTIVE_WRAP_UP_ENABLED, PROACTIVE_REMINDERS_ENABLED
    global AWAY_THRESHOLD_MINUTES, CHECK_IN_INTERVAL_MINUTES, STUCK_TASK_HOURS
    global MORNING_BRIEFING_TIME, EVENING_WRAP_UP_TIME, WEATHER_LATITUDE, WEATHER_LONGITUDE

    if "proactive_enabled" in data and data["proactive_enabled"] is not None:
        PROACTIVE_ENABLED = bool(data["proactive_enabled"])
    if "quiet_hours_start" in data and data["quiet_hours_start"] is not None:
        PROACTIVE_QUIET_HOURS_START = str(data["quiet_hours_start"])
    if "quiet_hours_end" in data and data["quiet_hours_end"] is not None:
        PROACTIVE_QUIET_HOURS_END = str(data["quiet_hours_end"])
    if "dnd" in data and data["dnd"] is not None:
        PROACTIVE_DND = bool(data["dnd"])
    if "max_per_hour" in data and data["max_per_hour"] is not None:
        PROACTIVE_MAX_PER_HOUR = max(1, min(10, int(data["max_per_hour"])))
    if "max_per_day" in data and data["max_per_day"] is not None:
        PROACTIVE_MAX_PER_DAY = max(1, min(30, int(data["max_per_day"])))
    if "sound_cue" in data and data["sound_cue"] is not None:
        PROACTIVE_SOUND_CUE = bool(data["sound_cue"])

    if "briefing_enabled" in data and data["briefing_enabled"] is not None:
        PROACTIVE_BRIEFING_ENABLED = bool(data["briefing_enabled"])
    if "check_ins_enabled" in data and data["check_ins_enabled"] is not None:
        PROACTIVE_CHECK_INS_ENABLED = bool(data["check_ins_enabled"])
    if "welcome_back_enabled" in data and data["welcome_back_enabled"] is not None:
        PROACTIVE_WELCOME_BACK_ENABLED = bool(data["welcome_back_enabled"])
    if "wrap_up_enabled" in data and data["wrap_up_enabled"] is not None:
        PROACTIVE_WRAP_UP_ENABLED = bool(data["wrap_up_enabled"])
    if "reminders_enabled" in data and data["reminders_enabled"] is not None:
        PROACTIVE_REMINDERS_ENABLED = bool(data["reminders_enabled"])

    if "away_threshold_minutes" in data and data["away_threshold_minutes"] is not None:
        AWAY_THRESHOLD_MINUTES = max(15, int(data["away_threshold_minutes"]))
    if "check_in_interval_minutes" in data and data["check_in_interval_minutes"] is not None:
        CHECK_IN_INTERVAL_MINUTES = max(15, int(data["check_in_interval_minutes"]))
    if "stuck_task_hours" in data and data["stuck_task_hours"] is not None:
        STUCK_TASK_HOURS = max(1, int(data["stuck_task_hours"]))
    if "morning_briefing_time" in data and data["morning_briefing_time"] is not None:
        MORNING_BRIEFING_TIME = str(data["morning_briefing_time"])
    if "evening_wrap_up_time" in data and data["evening_wrap_up_time"] is not None:
        EVENING_WRAP_UP_TIME = str(data["evening_wrap_up_time"])
    if "weather_latitude" in data and data["weather_latitude"] is not None:
        WEATHER_LATITUDE = float(data["weather_latitude"])
    if "weather_longitude" in data and data["weather_longitude"] is not None:
        WEATHER_LONGITUDE = float(data["weather_longitude"])

    save_persisted_settings()



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
    global PERSONALITY_MODE, PERSONALITY_SARCASM, PERSONALITY_WARMTH, PERSONALITY_TAMIL_MIX
    global PERSONALITY_VERBOSITY, MEMORY_ENABLED, VOICE_PROFILES, AUDIO_FEEL
    global WHISPER_MODEL, WHISPER_LANGUAGE, LISTEN_LANGUAGES, AUDIO_INPUT_DEVICE, VAD_ENERGY_THRESHOLD
    global TTS_ENGINE, TTS_VOICE, TTS_VOICE_TA, EXPRESSIVE_TAGS, GEMINI_TEMPERATURE
    global FORCE_TTS_ENGINE, GEMINI_TTS_MODEL, GEMINI_TTS_STYLE, VOICE_FX, TALK_LOOK
    global SHORT_TERM_EXCHANGES, SHORT_TERM_ENABLED, USER_TIMEZONE
    global LATE_NIGHT_START, LATE_NIGHT_END, CARE_NUDGES, NUDGE_COOLDOWN_MINUTES
    global COACH_MODE, GROUNDING_ENABLED
    global VERIFIED_HELPLINE_NUMBERS, SCENARIO_ENGINE_ENABLED, SCENARIO_STRICTNESS
    global SCENARIO_CHATTINESS, MISS_LOG_ENABLED, THINKING_STYLE, HUMAN_SPEECH_POLISH
    global WAKE_WORD_ENGINE, WAKE_WORD_KEYWORD, WAKE_WORD_SENSITIVITY, FOLLOW_UP_ENABLED, FOLLOW_UP_TIMEOUT
    global TALK_MODE, KEEP_MIC_WARM, GLOBAL_HOTKEY_ENABLED, GLOBAL_HOTKEY_KEY, STREAMING_STT, SOFT_CLICK_SOUND

    if not SETTINGS_FILE.exists():
        return
    try:
        import json
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if "expressive_tags" in data:
            EXPRESSIVE_TAGS = bool(data["expressive_tags"])
        if "temperature" in data:
            GEMINI_TEMPERATURE = float(data["temperature"])
        if "personality" in data:
            p = data["personality"]
            PERSONALITY_MODE = p.get("mode", PERSONALITY_MODE)
            PERSONALITY_SARCASM = int(p.get("sarcasm", PERSONALITY_SARCASM))
            PERSONALITY_WARMTH = int(p.get("warmth", PERSONALITY_WARMTH))
            PERSONALITY_TAMIL_MIX = int(p.get("tamil_mix", PERSONALITY_TAMIL_MIX))
            PERSONALITY_VERBOSITY = str(p.get("verbosity", PERSONALITY_VERBOSITY))
        if "memory_enabled" in data:
            MEMORY_ENABLED = bool(data["memory_enabled"])
        if "voice_profiles" in data:
            VOICE_PROFILES = data["voice_profiles"]
            active_prof = get_active_voice_profile()
            TTS_ENGINE = active_prof.get("engine", TTS_ENGINE)
            TTS_VOICE = active_prof.get("voice", TTS_VOICE)
            TTS_VOICE_TA = active_prof.get("voice_ta", TTS_VOICE_TA)
        if "audio_feel" in data:
            AUDIO_FEEL.update(data["audio_feel"])
        if "stt" in data:
            stt_data = data["stt"]
            WHISPER_MODEL = stt_data.get("whisper_model", WHISPER_MODEL)
            WHISPER_LANGUAGE = stt_data.get("language", WHISPER_LANGUAGE)
            if "listen_languages" in stt_data and isinstance(stt_data["listen_languages"], list):
                LISTEN_LANGUAGES = [str(x) for x in stt_data["listen_languages"] if x]
            AUDIO_INPUT_DEVICE = stt_data.get("input_device", AUDIO_INPUT_DEVICE)
            VAD_ENERGY_THRESHOLD = float(stt_data.get("sensitivity", VAD_ENERGY_THRESHOLD))
        if "wakeword" in data:
            ww_data = data["wakeword"]
            WAKE_WORD_ENGINE = ww_data.get("engine", WAKE_WORD_ENGINE)
            WAKE_WORD_KEYWORD = ww_data.get("keyword", WAKE_WORD_KEYWORD)
            WAKE_WORD_SENSITIVITY = float(ww_data.get("sensitivity", WAKE_WORD_SENSITIVITY))
            FOLLOW_UP_ENABLED = bool(ww_data.get("follow_up_enabled", FOLLOW_UP_ENABLED))
            FOLLOW_UP_TIMEOUT = float(ww_data.get("follow_up_timeout", FOLLOW_UP_TIMEOUT))
        if "talk_mode" in data:
            tm_data = data["talk_mode"]
            TALK_MODE = tm_data.get("talk_mode", TALK_MODE)
            KEEP_MIC_WARM = bool(tm_data.get("keep_mic_warm", KEEP_MIC_WARM))
            GLOBAL_HOTKEY_ENABLED = bool(tm_data.get("global_hotkey_enabled", GLOBAL_HOTKEY_ENABLED))
            GLOBAL_HOTKEY_KEY = tm_data.get("global_hotkey_key", GLOBAL_HOTKEY_KEY)
            STREAMING_STT = bool(tm_data.get("streaming_stt", STREAMING_STT))
            SOFT_CLICK_SOUND = bool(tm_data.get("soft_click_sound", SOFT_CLICK_SOUND))
        if "tamil_text_mode" in data:
            TAMIL_TEXT_MODE = str(data["tamil_text_mode"])
        if "kongu_touch" in data:
            KONGU_TOUCH = int(data["kongu_touch"])
        if "stt_engine" in data:
            STT_ENGINE = str(data["stt_engine"])
        if "force_tts_engine" in data:
            FORCE_TTS_ENGINE = str(data["force_tts_engine"])
        if "gemini_tts_model" in data:
            GEMINI_TTS_MODEL = str(data["gemini_tts_model"])
        if "gemini_tts_style" in data:
            GEMINI_TTS_STYLE = str(data["gemini_tts_style"])
        if "voice_fx" in data:
            VOICE_FX = str(data["voice_fx"])
        if "talk_look" in data:
            TALK_LOOK = str(data["talk_look"])
        if "short_term_exchanges" in data:
            SHORT_TERM_EXCHANGES = max(1, min(10, int(data["short_term_exchanges"])))
        if "short_term_enabled" in data:
            SHORT_TERM_ENABLED = bool(data["short_term_enabled"])
        if "user_timezone" in data:
            USER_TIMEZONE = str(data["user_timezone"])
        if "late_night_start" in data:
            LATE_NIGHT_START = str(data["late_night_start"])
        if "late_night_end" in data:
            LATE_NIGHT_END = str(data["late_night_end"])
        if "care_nudges" in data:
            CARE_NUDGES = bool(data["care_nudges"])
        if "nudge_cooldown_minutes" in data:
            NUDGE_COOLDOWN_MINUTES = int(data["nudge_cooldown_minutes"])
        if "coach_mode" in data:
            COACH_MODE = bool(data["coach_mode"])
        if "planner_dry_run" in data:
            PLANNER_DRY_RUN = bool(data["planner_dry_run"])
        if "grounding_enabled" in data:
            GROUNDING_ENABLED = bool(data["grounding_enabled"])
        if "verified_helpline_numbers" in data:
            VERIFIED_HELPLINE_NUMBERS = str(data["verified_helpline_numbers"])
        if "scenario_engine_enabled" in data:
            SCENARIO_ENGINE_ENABLED = bool(data["scenario_engine_enabled"])
        if "scenario_strictness" in data:
            SCENARIO_STRICTNESS = str(data["scenario_strictness"])
        if "scenario_chattiness" in data:
            SCENARIO_CHATTINESS = str(data["scenario_chattiness"])
        if "miss_log_enabled" in data:
            MISS_LOG_ENABLED = bool(data["miss_log_enabled"])
        if "thinking_style" in data:
            THINKING_STYLE = str(data["thinking_style"])
        if "human_speech_polish" in data:
            HUMAN_SPEECH_POLISH = bool(data["human_speech_polish"])
        if "presence" in data:
            pr = data["presence"]
            PROACTIVE_ENABLED = bool(pr.get("proactive_enabled", PROACTIVE_ENABLED))
            PROACTIVE_QUIET_HOURS_START = str(pr.get("quiet_hours_start", PROACTIVE_QUIET_HOURS_START))
            PROACTIVE_QUIET_HOURS_END = str(pr.get("quiet_hours_end", PROACTIVE_QUIET_HOURS_END))
            PROACTIVE_DND = bool(pr.get("dnd", PROACTIVE_DND))
            PROACTIVE_MAX_PER_HOUR = int(pr.get("max_per_hour", PROACTIVE_MAX_PER_HOUR))
            PROACTIVE_MAX_PER_DAY = int(pr.get("max_per_day", PROACTIVE_MAX_PER_DAY))
            PROACTIVE_SOUND_CUE = bool(pr.get("sound_cue", PROACTIVE_SOUND_CUE))
            PROACTIVE_BRIEFING_ENABLED = bool(pr.get("briefing_enabled", PROACTIVE_BRIEFING_ENABLED))
            PROACTIVE_CHECK_INS_ENABLED = bool(pr.get("check_ins_enabled", PROACTIVE_CHECK_INS_ENABLED))
            PROACTIVE_WELCOME_BACK_ENABLED = bool(pr.get("welcome_back_enabled", PROACTIVE_WELCOME_BACK_ENABLED))
            PROACTIVE_WRAP_UP_ENABLED = bool(pr.get("wrap_up_enabled", PROACTIVE_WRAP_UP_ENABLED))
            PROACTIVE_REMINDERS_ENABLED = bool(pr.get("reminders_enabled", PROACTIVE_REMINDERS_ENABLED))
            AWAY_THRESHOLD_MINUTES = int(pr.get("away_threshold_minutes", AWAY_THRESHOLD_MINUTES))
            CHECK_IN_INTERVAL_MINUTES = int(pr.get("check_in_interval_minutes", CHECK_IN_INTERVAL_MINUTES))
            STUCK_TASK_HOURS = int(pr.get("stuck_task_hours", STUCK_TASK_HOURS))
            MORNING_BRIEFING_TIME = str(pr.get("morning_briefing_time", MORNING_BRIEFING_TIME))
            EVENING_WRAP_UP_TIME = str(pr.get("evening_wrap_up_time", EVENING_WRAP_UP_TIME))
            WEATHER_LATITUDE = float(pr.get("weather_latitude", WEATHER_LATITUDE))
            WEATHER_LONGITUDE = float(pr.get("weather_longitude", WEATHER_LONGITUDE))
    except Exception:
        pass


def save_persisted_settings() -> None:
    """Save current settings to data/settings.json."""
    import json
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "memory_enabled": MEMORY_ENABLED,
        "expressive_tags": EXPRESSIVE_TAGS,
        "temperature": GEMINI_TEMPERATURE,
        "tamil_text_mode": TAMIL_TEXT_MODE,
        "kongu_touch": KONGU_TOUCH,
        "stt_engine": STT_ENGINE,
        "force_tts_engine": FORCE_TTS_ENGINE,
        "gemini_tts_model": GEMINI_TTS_MODEL,
        "gemini_tts_style": GEMINI_TTS_STYLE,
        "voice_fx": VOICE_FX,
        "talk_look": TALK_LOOK,
        "short_term_exchanges": SHORT_TERM_EXCHANGES,
        "short_term_enabled": SHORT_TERM_ENABLED,
        "user_timezone": USER_TIMEZONE,
        "late_night_start": LATE_NIGHT_START,
        "late_night_end": LATE_NIGHT_END,
        "care_nudges": CARE_NUDGES,
        "nudge_cooldown_minutes": NUDGE_COOLDOWN_MINUTES,
        "coach_mode": COACH_MODE,
        "grounding_enabled": GROUNDING_ENABLED,
        "planner_dry_run": PLANNER_DRY_RUN,
        "verified_helpline_numbers": VERIFIED_HELPLINE_NUMBERS,
        "scenario_engine_enabled": SCENARIO_ENGINE_ENABLED,
        "scenario_strictness": SCENARIO_STRICTNESS,
        "scenario_chattiness": SCENARIO_CHATTINESS,
        "miss_log_enabled": MISS_LOG_ENABLED,
        "thinking_style": THINKING_STYLE,
        "human_speech_polish": HUMAN_SPEECH_POLISH,
        "personality": {
            "mode": PERSONALITY_MODE,
            "sarcasm": PERSONALITY_SARCASM,
            "warmth": PERSONALITY_WARMTH,
            "tamil_mix": PERSONALITY_TAMIL_MIX,
            "verbosity": PERSONALITY_VERBOSITY,
        },
        "voice_profiles": VOICE_PROFILES,
        "audio_feel": AUDIO_FEEL,
        "stt": {
            "whisper_model": WHISPER_MODEL,
            "language": WHISPER_LANGUAGE,
            "listen_languages": LISTEN_LANGUAGES,
            "input_device": AUDIO_INPUT_DEVICE,
            "sensitivity": VAD_ENERGY_THRESHOLD,
        },
        "wakeword": {
            "engine": WAKE_WORD_ENGINE,
            "keyword": WAKE_WORD_KEYWORD,
            "sensitivity": WAKE_WORD_SENSITIVITY,
            "follow_up_enabled": FOLLOW_UP_ENABLED,
            "follow_up_timeout": FOLLOW_UP_TIMEOUT,
        },
        "talk_mode": {
            "talk_mode": TALK_MODE,
            "keep_mic_warm": KEEP_MIC_WARM,
            "global_hotkey_enabled": GLOBAL_HOTKEY_ENABLED,
            "global_hotkey_key": GLOBAL_HOTKEY_KEY,
            "streaming_stt": STREAMING_STT,
            "soft_click_sound": SOFT_CLICK_SOUND,
        },
        "presence": get_presence_settings(),
    }
    SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# Attempt load at import time
load_persisted_settings()

