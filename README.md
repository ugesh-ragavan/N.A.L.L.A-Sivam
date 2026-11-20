# NALLA SIVAM

> **Think smart. Speak naturally. Act responsibly. Stay curious.**

Nalla Sivam is a personal AI companion that listens, thinks, talks back, remembers, and controls your gadgets. It runs on your own machine, keeps its memory in a local database, and uses Google Gemini's free tier for reasoning.

The name: **NALLA** ("good" in Tamil, and the wake word) and **SIVAM**, inspired by the spirit of the film *Anbe Sivam*: real goodness is how you treat people. Nalla does not pretend to have feelings. It shows care by paying attention, telling the truth, and getting things done.

---

## Features (MVP v1)
- **Voice loop:** wake word or push-to-talk, speech-to-text (`faster-whisper`), Gemini, text-to-speech (`edge-tts` / `piper`).
- **Dashboard:** Localhost dashboard (`FastAPI` + HTML/JS) for chat, settings, memory, devices, and logs.
- **Local Memory:** SQLite memory you can inspect, edit, and delete.
- **Hardware Integration:** Control ESP32/NodeMCU devices over MQTT.

---

## Quickstart

### 1. Prerequisites
- Python 3.10+
- Git

### 2. Setup
```bash
# Clone the repository
git clone https://github.com/ugesh-ragavan/N.A.L.L.A-Sivam.git
cd N.A.L.L.A-Sivam

# Set up virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Variables
Copy `.env.example` to `.env` and provide your Gemini API key:
```bash
cp .env.example .env
```

---

## Project Blueprint
For details on architecture, design philosophy, and phase breakdown, check [NALLA SIVAM Project Blueprint.md](file:///o:/Ug.Engineer/N.A.L.L.A-Sivam/NALLA%20SIVAM%20Project%20Blueprint.md).
