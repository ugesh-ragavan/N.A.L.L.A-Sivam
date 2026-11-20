"""Application configuration loader."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Attempt to load dotenv if available, otherwise read environment variables
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    # Minimal fallback parser if python-dotenv is not yet installed
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

