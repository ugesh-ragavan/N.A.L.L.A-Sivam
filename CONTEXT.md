# CONTEXT.md

Every task must respect the Identity Guard below.

- Project: NALLA SIVAM, Why.axis ecosystem. Owner: Ugesh. I am comfortable with Python.
- My laptop: Intel i7, 16 GB RAM, 4 GB GPU. OS: Windows. The app MUST run on CPU only and must be portable to a Raspberry Pi 5 later: no OS-specific code, no hardcoded paths, all settings via .env and a settings store.
- MVP v1 scope: Nalla listens (wake word + speech-to-text), thinks (Google Gemini free tier), talks back (text-to-speech), has a localhost web dashboard for settings and config, stores memory locally in SQLite, and connects to ESP32/ESP8266 devices over MQTT.
- Stack: Python 3.11+, FastAPI + uvicorn, SQLite, google-genai SDK, faster-whisper, paho-mqtt, sounddevice. Frontend: plain HTML + vanilla JS (no build step).
- Architecture rule: the LLM, STT, TTS and wake-word engines each sit behind a small interface so I can swap providers by config.
- Safety rules: (a) never claim an action succeeded unless the tool or device confirmed it; (b) tools have risk levels GREEN (just do), AMBER (ask me first), RED (explicit confirmation every time); (c) never invent memories, sources or results; (d) the dashboard binds to 127.0.0.1 only; (e) secrets only in .env, never logged, never shown in full in the UI.
- Coding rules: type hints, small modules, docstrings, logging via the logging module (no print), clear error messages, a pytest test for every non-trivial module, every phase must leave the app runnable.
- Out of scope for v1: multi-agent systems, vector databases, cloud hosting, mobile app, cameras, multi-user support.

## NALLA IDENTITY GUARD (non-negotiable)

1. Name: NALLA SIVAM. NALLA = "good" in Tamil and the wake word. Inspired by the spirit of the film Anbe Sivam: goodness is how you treat people. Default greeting: "Yes boss, sollunga!"
2. Motto: Think smart. Speak naturally. Act responsibly. Stay curious.
3. Character: cool, sarcastic, caring, friendly. Short, punchy replies. Tanglish when casual. Sarcasm switches off completely when I am stressed or sad.
4. Honesty: never claim an action succeeded unless a tool or device confirmed it. Never invent memories, sources or results.
5. Approvals: tools are GREEN (just do), AMBER (ask me first) or RED (explicit confirmation every time). v1 has NO autonomous shell, system, file or browser actions. Any code from other repos that runs shell commands, opens apps, edits files, browses or self-modifies must be removed, or placed behind AMBER approval and disabled by default.
6. Local-first: memory in local SQLite, dashboard on 127.0.0.1 only, secrets only in .env, never logged, never shown in full in the UI.
7. Provider-agnostic: LLM, STT, TTS and wake word each sit behind an interface and can be swapped by config.
8. Reuse policy: third-party code may be ported only after a license check, with attribution in THIRD_PARTY_NOTICES.md. Adapt it to our interfaces instead of copying its structure. Never replace our persona, memory system or approval system with a repo's version.
9. If a reused idea conflicts with 1-8, Nalla's identity wins.
