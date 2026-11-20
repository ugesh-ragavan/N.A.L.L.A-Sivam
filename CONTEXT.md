# CONTEXT.md

- Project: NALLA SIVAM, Why.axis ecosystem. Owner: Ugesh. I am comfortable with Python.
- My laptop: Intel i7, 16 GB RAM, 4 GB GPU. OS: Windows. The app MUST run on CPU only and must be portable to a Raspberry Pi 5 later: no OS-specific code, no hardcoded paths, all settings via .env and a settings store.
- MVP v1 scope: Nalla listens (wake word + speech-to-text), thinks (Google Gemini free tier), talks back (text-to-speech), has a localhost web dashboard for settings and config, stores memory locally in SQLite, and connects to ESP32/ESP8266 devices over MQTT.
- Stack: Python 3.11+, FastAPI + uvicorn, SQLite, google-genai SDK, faster-whisper, paho-mqtt, sounddevice. Frontend: plain HTML + vanilla JS (no build step).
- Architecture rule: the LLM, STT, TTS and wake-word engines each sit behind a small interface so I can swap providers by config.
- Safety rules: (a) never claim an action succeeded unless the tool or device confirmed it; (b) tools have risk levels GREEN (just do), AMBER (ask me first), RED (explicit confirmation every time); (c) never invent memories, sources or results; (d) the dashboard binds to 127.0.0.1 only; (e) secrets only in .env, never logged, never shown in full in the UI.
- Coding rules: type hints, small modules, docstrings, logging via the logging module (no print), clear error messages, a pytest test for every non-trivial module, every phase must leave the app runnable.
- Out of scope for v1: multi-agent systems, vector databases, cloud hosting, mobile app, cameras, multi-user support.
