# NALLA SIVAM: Project Blueprint

Why.axis · MVP v1 · Motto, name, philosophy, context, GitHub text, phases, components, tools and build prompts

## 1. Motto

> **Think smart. Speak naturally. Act responsibly. Stay curious.**

Tagline options (pick one for the README and splash screen):

- *"A JARVIS that actually cares."*
- *"Smart like an engineer, warm like a friend."*
- *"Intelligence with anbu."*

## 2. The name

The name - NALLA SiVaM - not a lame logical assistant, siripudam vazhum manidhan
not a lame logical assistant, siripudam inidhaai vazhum anbu manidhan

**NALLA** (நல்ல) means "good" in Tamil. It is also the wake word: say *"Nalla!"* and the reply is *"Yes boss, sollunga!"* As an acronym it is the cheeky engineer's promise: **N**ot **A**nother **L**ame **L**ogical **A**ssistant. No robotic, customer-support voice.

**SIVAM** (சிவம்) is the Tamil word linked to Shiva, and it carries the sense of "auspicious, good". 

**Inspiration: the film *Anbe Sivam* (2003).** Its hero, Nallasivam (played by Kamal Haasan), is a warm, principled activist who travels with a cynical young ad filmmaker, Anbarasu, and shows him through actions, not lectures, that goodness is simply how you treat people. The title is commonly translated as "love is God". Nalla Sivam the assistant borrows that spirit, not a pretence of feelings: care shown through attention, honesty and reliability.

## 3. Philosophy

**Decision principles** (from the master identity prompt):

1. Accuracy over confidence.
2. Logic over blind agreement.
3. Action over unnecessary discussion.
4. Simplicity over complexity.
5. Privacy over convenience.
6. Safety over speed.
7. Transparency over pretending.
8. User autonomy over autonomous control.

**Character:** calm, quick, loyal, curious, with dry humour. The intelligence of a capable engineer, the charm of a good friend, the manners of an executive assistant. It never jokes about serious emotional moments.

**How the Anbe Sivam spirit shows up in the design:**

- **Care** means it remembers what matters to you and follows up on it (local memory, reminders).
- **Kindness without flattery:** it challenges weak ideas politely and recommends the better option.
- **Honesty over comfort:** it never claims a task succeeded unless a tool or device confirms it, and never invents memories, sources or results.
- **Respect for your independence:** it does not pretend to have feelings or consciousness, and it does not encourage unhealthy dependence.
- **Responsible power:** risky actions need approval (GREEN = just do it, AMBER = ask first, RED = explicit confirmation every time).

## 4. Context

**Why it exists:** to be a personal assistant that understands, remembers, acts and verifies, and one day the central intelligence behind Why.axis gadgets and digital experiences.

**Who builds it:** Ugesh, a data engineer working in GenAI, building hardware and software products under Why.axis.

**Constraints that shape MVP v1:**

- A laptop is the brain for now (Intel i7, 16 GB RAM, 4 GB GPU), so everything must run on CPU. A Raspberry Pi comes later, so the code must stay portable.
- Free AI: Google Gemini's free tier, behind a swappable provider so other models can replace it.
- Local-first: memory in SQLite on the machine, a localhost dashboard, no cloud hosting.
- Parts on hand: ESP32 and NodeMCU boards, sensors, displays.

**One brain, many bodies.** The intelligence lives in one app. Laptop, phone and ESP32 gadgets are just ways to reach it.

**Architecture:**

```
Mic -> Wake word / Push-to-talk -> VAD -> Speech-to-text (faster-whisper)
   -> Agent loop (Gemini + persona + memories + tools + approvals)
   -> Text-to-speech (edge-tts / Piper) -> Speaker

Agent tools -> SQLite memory | Reminders | MQTT (Mosquitto) -> ESP32 devices
Localhost dashboard (FastAPI + HTML/JS): chat, settings, memory, devices, logs
```

**Out of scope for v1:** multi-agent systems, vector databases, cloud hosting, mobile app, cameras, multi-user support.

## 5. GitHub text

**Repository name:** `nalla-sivam`

**About (short description):**

```
Nalla Sivam: a local-first, JARVIS-style personal AI assistant. Listens, thinks (Gemini), talks back, remembers locally, and controls ESP32 devices over MQTT. A Why.axis project.
```

**Topics:** `ai-assistant` `voice-assistant` `jarvis` `gemini` `esp32` `mqtt` `fastapi` `faster-whisper` `python` `iot` `local-first` `tamil`

**README opening (paste this):**

```
# NALLA SIVAM

> Think smart. Speak naturally. Act responsibly. Stay curious.

Nalla Sivam is a personal AI companion that listens, thinks, talks back, remembers, and controls your gadgets. It runs on your own machine, keeps its memory in a local database, and uses Google Gemini's free tier for reasoning.

The name: NALLA ("good" in Tamil, and the wake word) and SIVAM, inspired by the spirit of the film Anbe Sivam: real goodness is how you treat people. Nalla does not pretend to have feelings. It shows care by paying attention, telling the truth, and getting things done.

## Features (MVP v1)
- Voice loop: wake word or push-to-talk, speech-to-text, Gemini, text-to-speech
- Localhost dashboard for chat, settings, memory, devices and logs
- Local SQLite memory you can inspect, edit and delete
- ESP32 / ESP8266 control over MQTT, confirmed by device acknowledgements
- Approval levels (green / amber / red) for every tool

## Honesty rule
Nalla only says "Done" after a tool or device confirms it.
```

**README outline:** title and tagline, demo GIF, what it does, philosophy, architecture diagram, quick start, configuration, dashboard, voice, ESP32 devices, safety model, roadmap, contributing, license, acknowledgements (Anbe Sivam for the spirit; JARVIS as a style inspiration only).

**Repo hygiene:** keep `.env`, `secrets.h`, `data/` and logs in `.gitignore`. Commit `.env.example` and `secrets.example.h` only. Choose a license (MIT is a common default) before making the repo public.

## 6. Phases at a glance

| Phase | Time | What you get | Done when |
| --- | --- | --- | --- |
| 0. Scaffold | Day 1 | Folders, CONTEXT.md, .env, Gemini test | Banner prints and a Gemini reply appears |
| 1. Brain | Days 2-4 | Persona, provider interface, tool loop, approvals | "Nalla!" gets the greeting; risky tool asks approval |
| 2. Dashboard | Days 5-8 | Localhost page: chat, settings, logs | Settings change behaviour without code edits |
| 3. Memory | Week 2 | SQLite memory, reminders, memory UI | Nalla recalls after restart; delete really deletes |
| 4. Voice | Week 3 | Listen, think, talk back | Spoken answer within about 3 s of finishing speaking |
| 5. ESP link | Week 4 | MQTT, sensor reads, actuator control, firmware | "Done, boss" only after the device acks |
| 6. Polish | Week 5 | Resilience, launcher, docs, demo script | Clean install running in under 30 minutes |

## 7. Components used

**Hardware**

- Laptop (the brain): i7, 16 GB RAM, 4 GB GPU, CPU-only operation.
- Microphone and speaker (headphones recommended while testing).
- ESP32 and/or NodeMCU ESP8266 boards.
- Sensors and peripherals you have wired and tested (for example temperature, PIR, relay, OLED display).
- Later: Raspberry Pi 5 as a dedicated home for Nalla.

**Software modules**

- **brain:** LLM provider interface, Gemini provider, persona loader, tool registry, agent loop.
- **core:** event bus, logging, approval manager.
- **memory:** SQLite database, memory store with full-text search, reminders scheduler.
- **voice:** audio I/O, voice activity detection, wake word, speech-to-text, text-to-speech, pipeline state machine.
- **devices:** MQTT client, device registry (devices.yaml), ESP firmware.
- **web:** FastAPI server, WebSocket events, HTML/CSS/JS dashboard.

## 8. Tools used

| Tool | Used for | Cost |
| --- | --- | --- |
| Google Antigravity | Agentic IDE that writes the code from the prompts | Per your plan |
| Google AI Studio + Gemini API | API key and the free-tier brain | Free tier (limits change; check AI Studio) |
| Python 3.11+ | Main language | Free |
| FastAPI + uvicorn | Backend and dashboard server | Free |
| SQLite (FTS5) | Local memory | Free |
| faster-whisper | Local speech-to-text on CPU | Free |
| openWakeWord or Picovoice Porcupine | Wake word (custom "Nalla" keyword later) | Open-source / free tier |
| Piper and edge-tts | Text-to-speech (offline English; online Tamil voices) | Free |
| sounddevice, webrtcvad or silero-vad | Mic capture and voice activity detection | Free |
| Mosquitto + paho-mqtt | MQTT broker and Python client | Free |
| Arduino IDE or PlatformIO | Flashing ESP firmware | Free |
| PubSubClient + ArduinoJson | ESP-side MQTT and JSON | Free |
| Git + GitHub | Version control and the public repo | Free |
| Claude | Planning, reviewing and prompt writing | Per your plan |

Optional later: Ollama with a small local model for offline simple commands.

## 9. Detailed build prompts

**How to use:** one phase = one Antigravity task. Paste the prompt, read the agent's plan, then approve. Phase 0 creates `CONTEXT.md`, and every later prompt starts with "Read CONTEXT.md first". Run the acceptance tests yourself before the next phase, and commit to git after each one. Replace anything in `<angle brackets>` first.

### Phase 0: Scaffold and master context

Before pasting: create an empty folder `nalla/`, open it in Antigravity, get a free Gemini key from aistudio.google.com/apikey, and save your master identity prompt as `persona/nalla_master.md`.

```
You are my senior engineer. We are building NALLA SIVAM, a personal JARVIS-style AI assistant. Start by planning, then build only Phase 0.

PHASE 0 GOAL: a clean, runnable project scaffold plus a CONTEXT.md that all later work will follow.

Create these files:

1. CONTEXT.md with exactly this content (fill in the angle brackets from what I say):
   - Project: NALLA SIVAM, Why.axis ecosystem. Owner: Ugesh. I am comfortable with Python.
   - My laptop: Intel i7, 16 GB RAM, 4 GB GPU. OS: <Windows/Linux/macOS>. The app MUST run on CPU only and must be portable to a Raspberry Pi 5 later: no OS-specific code, no hardcoded paths, all settings via .env and a settings store.
   - MVP v1 scope: Nalla listens (wake word + speech-to-text), thinks (Google Gemini free tier), talks back (text-to-speech), has a localhost web dashboard for settings and config, stores memory locally in SQLite, and connects to ESP32/ESP8266 devices over MQTT.
   - Stack: Python 3.11+, FastAPI + uvicorn, SQLite, google-genai SDK, faster-whisper, paho-mqtt, sounddevice. Frontend: plain HTML + vanilla JS (no build step).
   - Architecture rule: the LLM, STT, TTS and wake-word engines each sit behind a small interface so I can swap providers by config.
   - Safety rules: (a) never claim an action succeeded unless the tool or device confirmed it; (b) tools have risk levels GREEN (just do), AMBER (ask me first), RED (explicit confirmation every time); (c) never invent memories, sources or results; (d) the dashboard binds to 127.0.0.1 only; (e) secrets only in .env, never logged, never shown in full in the UI.
   - Coding rules: type hints, small modules, docstrings, logging via the logging module (no print), clear error messages, a pytest test for every non-trivial module, every phase must leave the app runnable.
   - Out of scope for v1: multi-agent systems, vector databases, cloud hosting, mobile app, cameras, multi-user support.

2. Folder structure:
   nalla/
     app/
       main.py            (entry: python -m app)
       config.py          (loads .env + settings)
       core/              (events.py, logging_setup.py, approvals.py)
       brain/             (llm_provider.py, gemini_provider.py, persona.py, tools.py, agent.py)
       memory/            (db.py, store.py)
       voice/             (audio_io.py, wakeword.py, vad.py, stt.py, tts.py, pipeline.py)
       devices/           (mqtt_client.py, registry.py)
       web/               (server.py, static/, templates/)
     persona/nalla_master.md   (already exists, do not edit)
     data/                (gitignored: nalla.db, logs/)
     firmware/esp_node/
     tests/
     .env.example, .gitignore, requirements.txt, README.md, CONTEXT.md
   Create each module as a stub with a docstring describing its future job.

3. .env.example with: GEMINI_API_KEY=, GEMINI_MODEL=gemini-2.5-flash (add a comment: verify the current free-tier model name in AI Studio), LOG_LEVEL=INFO, WEB_PORT=8000, MQTT_HOST=localhost, MQTT_PORT=1883, MQTT_USER=, MQTT_PASSWORD=.

4. requirements.txt with pinned minimum versions for the stack, and a README with setup steps (create venv, pip install, copy .env.example to .env).

5. app/main.py that loads config, sets up logging, prints a startup banner, and exits cleanly.

ACCEPTANCE TESTS: a fresh venv install works; `python -m app` prints the banner with no errors; `pytest` passes; a tiny script calls Gemini once using my key from .env and prints the reply (put it in scripts/test_gemini.py).

Do NOT build features beyond Phase 0. Show me your plan before writing code.
```

### Phase 1: Brain, persona and tool registry

First append this addendum to the end of `persona/nalla_master.md` yourself. It fixes gaps in the original:

```
## ADDENDUM (v1)
- SIVAM expands to: Siripudan Inidhaai Vazhum Anbu Manidhan. Use only this expansion everywhere.
- Voice rules: default replies are 1-2 short sentences. Give detail only when asked.
- Capability honesty: only claim capabilities that are currently connected. NOW: chat, reasoning, memory, reminders, device control via connected tools. NEXT: calendar, email drafts, file search. LATER: autonomous workflows. Never promise a NEXT or LATER item as if it works today.
- Approvals: GREEN = just do it. AMBER = ask first. RED = explicit confirmation every time. Say "Done, boss. Everything checks out." ONLY after a tool or device confirms success.
- If a tool fails, say so plainly and suggest the next step.
```

```
Read CONTEXT.md first and follow it. Plan, then build Phase 1 only.

PHASE 1 GOAL: a working terminal chat with Nalla's personality, a swappable LLM provider, conversation history, and a tool-calling loop with approval levels (tools are stubs for now).

Build:

1. app/brain/llm_provider.py: an abstract LLMProvider with `generate(messages, tools, system_prompt) -> LLMResponse` (text and/or tool calls) and `stream(...)` that yields text chunks. LLMResponse and ToolCall are dataclasses.

2. app/brain/gemini_provider.py: implements LLMProvider using the google-genai SDK. Model name comes from GEMINI_MODEL (never hardcoded). Handle: 429/quota errors with exponential backoff (max 3 tries) then a friendly error; network failure; empty responses; safety blocks. Translate our tool schema into Gemini function declarations and Gemini function calls back into ToolCall objects.

3. app/brain/persona.py: loads persona/nalla_master.md as the system prompt, hot-reloads when the file changes. Also appends the current date/time and a short list of CURRENTLY AVAILABLE tools to the prompt on every call, so Nalla knows what it can really do.

4. app/core/approvals.py: enum RiskLevel (GREEN, AMBER, RED) and an ApprovalManager. For AMBER/RED tools, it asks the user to confirm through a pluggable callback (terminal prompt now, dashboard and voice later) and records every decision in a log.

5. app/brain/tools.py: a ToolRegistry with a `@tool(name, description, risk, parameters_schema)` decorator. Register stub tools: get_time (GREEN), echo_test (GREEN), fake_relay_toggle (AMBER). Every tool returns a ToolResult(ok: bool, message: str, data: dict).

6. app/brain/agent.py: the agent loop. Send messages to the provider; if it returns tool calls, check approval, run the tool, send results back, repeat up to 5 steps; then return the final text. Hard rule in code: a tool result with ok=False must be reported honestly, and the loop must never fabricate success.

7. A terminal chat (`python -m app chat`) with conversation history (last 20 turns kept in memory for now). Typing "Nalla!" should reply "Yes boss, sollunga!".

8. Tests with a FakeProvider (no network) covering: tool loop, approval denial, tool failure reporting, step limit.

ACCEPTANCE TESTS: "Nalla!" gets the greeting; "what time is it" triggers get_time; "toggle the relay" asks my approval before running; denying it makes Nalla say it did not happen; with the internet off I see a clear error, not a crash.

Do NOT add voice, web UI, memory or MQTT yet.
```

### Phase 2: Localhost dashboard

```
Read CONTEXT.md first. Plan, then build Phase 2 only.

PHASE 2 GOAL: a localhost web dashboard (FastAPI, bound to 127.0.0.1) for chat, settings and live status, without any frontend build step.

Build:

1. app/web/server.py: FastAPI app started by `python -m app serve` (default port from WEB_PORT). Bind to 127.0.0.1 only.

2. Settings system (app/config.py): non-secret settings in data/settings.json (model name, temperature, persona file path, TTS voice, wake-word sensitivity, microphone device, language mode: English / Tamil-English, mic muted flag). The Gemini API key lives only in .env; the UI can update it but must show it masked (last 4 chars) and never return it in any API response. Validate all inputs and apply changes live without restarting where possible.

3. API routes: GET/PUT /api/settings; POST /api/chat (uses the Phase 1 agent, returns reply and tool activity); GET /api/status (provider reachable, model name, uptime); GET /api/logs?limit=100; WebSocket /ws pushing live events (message, tool call, approval request, device status, errors).

4. Frontend in app/web/static and templates (HTML, CSS, vanilla JS), dark theme, mobile-friendly, with tabs: Chat, Settings, Memory (placeholder), Devices (placeholder), Logs. Chat tab shows messages and tool calls inline. When an AMBER/RED approval is requested, show a modal with Approve / Deny that resolves the pending approval.

5. Persona editor in Settings: edit nalla_master.md text, Save, with a "revert to last saved" button.

6. A header status light: green (Gemini reachable), amber (degraded), red (offline).

7. Tests for the settings validation and the API routes.

ACCEPTANCE TESTS: I open http://localhost:8000, chat with Nalla; change the model or temperature in Settings and the next reply uses it; edit the persona text and see the behavior change; trigger the fake_relay_toggle tool and approve it from the modal; the API key never appears in any network response.

Security: no CORS wildcard, escape all rendered text (prevent XSS), reject oversized requests.
```

### Phase 3: Local memory

```
Read CONTEXT.md first. Plan, then build Phase 3 only.

PHASE 3 GOAL: durable local memory that Nalla can save, recall, and that I can inspect, edit and delete from the dashboard.

Build:

1. app/memory/db.py: SQLite (data/nalla.db) with migrations. Tables: messages (id, session_id, role, text, created_at), memories (id, text, category [preference|fact|project|person|task], importance 1-5, source [user_said|auto_suggested], created_at, updated_at, last_used_at), reminders (id, text, due_at, status, created_at). Enable FTS5 on memories.text for search.

2. app/memory/store.py: save_memory, update_memory, delete_memory (hard delete, really gone), search_memories(query, limit), list_memories(filters), plus conversation history persistence across restarts.

3. New tools in the registry: remember(text, category, importance) GREEN; recall(query) GREEN; forget(memory_id) AMBER; set_reminder(text, due_at) GREEN; list_reminders GREEN. Nalla may only call remember when I explicitly ask ("remember this", "note that") OR it proposes a memory and I confirm. It must NEVER silently store sensitive data (passwords, card or ID numbers).

4. Memory injection: before each model call, run search_memories on my latest message and add the top 5 relevant memories to the system prompt under a section labelled "Known about the user (may be outdated)". Add the rule: if a memory is not in this list, say you don't remember it. Never fabricate memories.

5. Reminders scheduler: a background asyncio task checks due reminders every 30 seconds and pushes a reminder event over the WebSocket (voice delivery comes in Phase 4).

6. Dashboard Memory tab: table with search, category filter, inline edit, delete with confirmation, an "Add memory" form, and export to JSON. Reminders list below it.

7. A "memory off" switch in Settings (when off, Nalla neither stores nor reads memories).

8. Tests: save/recall, FTS search ranking, delete really removes the row, restart persistence, injection formatting.

ACCEPTANCE TESTS: say "remember I prefer short answers", restart the app, ask "what do you know about my preferences?" and it answers correctly; delete that memory in the dashboard and Nalla no longer knows it; ask about something never stored and Nalla says it doesn't remember; set a reminder for 2 minutes ahead and see it fire.
```

### Phase 4: Voice (listen, think, talk back)

**Honest note on the wake word:** open-source wake-word engines do not ship a ready "Nalla" model. Plan: build push-to-talk first, use a placeholder built-in wake word while testing, then train a custom "Nalla" keyword later (Picovoice Porcupine's console or openWakeWord training). The prompt below makes the keyword swap a config change.

```
Read CONTEXT.md first. Plan, then build Phase 4 only.

PHASE 4 GOAL: full voice loop: listen -> speech-to-text -> agent -> text-to-speech -> speak, on a CPU-only laptop.

Build, in this order, testing each step:

1. app/voice/audio_io.py: microphone capture and speaker playback with sounddevice. List devices, choose by setting, 16 kHz mono input.

2. app/voice/vad.py: voice activity detection (webrtcvad or silero-vad, whichever installs cleanly on my OS) to record until I stop speaking (about 0.8 s of silence), with a max length of 20 s.

3. app/voice/stt.py: an STT interface plus a faster-whisper implementation. Use the `base` model (int8, CPU) by default, configurable to `small`. Language setting: auto-detect, English, or Tamil. Return text plus confidence.

4. app/voice/tts.py: a TTS interface with two implementations: (a) edge-tts (online, supports Tamil voices such as ta-IN, and English Indian voices), (b) Piper (offline, English). Voice and engine chosen in settings. Fall back from edge-tts to Piper automatically when offline.

5. app/voice/wakeword.py: a WakeWord interface. Implement (1) PushToTalk: a keyboard hotkey and a dashboard button; (2) an openWakeWord engine using a built-in placeholder keyword for now, with the keyword/model path and sensitivity in settings. Add a clear TODO and README section on how to train and plug in a custom "Nalla" keyword.

6. app/voice/pipeline.py: a state machine IDLE -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE, publishing each state change as a WebSocket event so the dashboard shows it live. Rules: the microphone MUST be muted while Nalla is speaking (avoid hearing itself); a new wake word during SPEAKING interrupts playback; if STT returns empty or low confidence, say "Sorry boss, didn't catch that" instead of calling the model.

7. Latency: stream the model's reply and send each completed sentence to TTS immediately instead of waiting for the full answer. Log timings (STT, LLM first token, TTS start) so I can see where time goes. Target: first spoken word within about 3 seconds of finishing my sentence.

8. Spoken replies must be short and natural: strip markdown, emoji and code from text before TTS.

9. Dashboard: a live voice status indicator, a mic mute button, an input device dropdown, a "Test speaker" and "Test mic" button, and a transcript of what was heard.

10. Speak due reminders from Phase 3 aloud.

ACCEPTANCE TESTS: press the button, say "what time is it", hear the answer; say a sentence in Tamil-English and see the correct transcript; interrupt Nalla mid-sentence; unplug the internet and hear it fall back to Piper; check the timing log.

Do NOT add MQTT yet.
```

**Tips:** start with `base` Whisper in a quiet room. If transcripts are poor, move to `small` before blaming the code. Use headphones during early tests to avoid echo.

### Phase 5: ESP32 link over MQTT

Before pasting, list the real hardware you have wired and tested. The prompt has placeholders for that.

```
Read CONTEXT.md first. Plan, then build Phase 5 only.

PHASE 5 GOAL: Nalla can read sensors from and control actuators on my ESP devices over MQTT, and only reports success when the device confirms.

My hardware: board = <ESP32 or NodeMCU ESP8266>. Wired and working: <e.g. DHT11 on GPIO4, PIR on GPIO27, relay on GPIO26, SSD1306 OLED on I2C>. WiFi is a normal home network. Broker runs on my laptop.

Build:

1. Mosquitto setup: write docs/mqtt_setup.md for my OS and a mosquitto.conf with password authentication and a listener reachable on my LAN (explain the firewall step). Create a user for the app and one for devices.

2. MQTT topic contract (document it in docs/mqtt_contract.md):
   nalla/<device_id>/status  (retained; online/offline via Last Will)
   nalla/<device_id>/telemetry  (JSON, e.g. {"sensor":"temp","value":28.5,"unit":"C","ts":...})
   nalla/<device_id>/cmd  (JSON: {"cmd_id":"uuid","target":"relay1","action":"set","value":"on"})
   nalla/<device_id>/ack  (JSON: {"cmd_id":"uuid","ok":true,"detail":"relay1=on"})

3. app/devices/mqtt_client.py: paho-mqtt client with auto-reconnect, thread-safe, caching the latest telemetry and status per device. send_command() publishes a command and WAITS up to 5 seconds for the matching ack; it returns ok=True only if an ack arrives with ok=true. Timeout or ok=false is reported honestly.

4. app/devices/registry.py and a devices.yaml file: device_id, friendly name, room, sensors (name, unit), actuators (name, allowed values, RISK LEVEL; default AMBER, mark low-risk ones GREEN). The registry is editable from the dashboard.

5. New tools: list_devices (GREEN), read_sensor(device, sensor) (GREEN; if the reading is older than 60 s, say it is stale), set_actuator(device, actuator, value) (risk from registry; uses the confirm-by-ack flow). Replace the Phase 1 fake relay tool.

6. Firmware in firmware/esp_node/ (Arduino C++, PubSubClient + ArduinoJson): WiFi connect and reconnect, MQTT connect with Last Will, publish telemetry every 10 s, subscribe to cmd, execute, publish ack with the cmd_id. Config (WiFi, broker IP, device_id, credentials) in a separate secrets.h that is gitignored, with secrets.example.h. Use #ifdef to support both ESP32 and ESP8266. Add a README with wiring and flashing steps.

7. Dashboard Devices tab: live online/offline status, latest readings, manual control buttons, a command history with ack results.

8. Tests with a mocked MQTT client covering: ack success, ack failure, ack timeout, stale telemetry, device offline.

ACCEPTANCE TESTS: power the ESP and see it online in the dashboard; ask "what's the temperature?" and get the live reading; say "turn on the relay" and hear "Done, boss" ONLY after the device acks; unplug the ESP and ask again and hear an honest failure; verify the relay physically changed.
```

### Phase 6: Polish and demo

```
Read CONTEXT.md first. Plan, then build Phase 6 only.

PHASE 6 GOAL: make MVP v1 reliable and demo-ready.

1. Resilience: graceful handling of Gemini outage or rate limit (spoken and shown message, queueing nothing risky), microphone unplugged, MQTT broker down (auto-retry, dashboard warning), corrupt settings file (fall back to defaults and warn).
2. A single launcher `python -m app run` that starts web, voice and MQTT together with a health check page (/health) and clean shutdown (Ctrl+C).
3. Log rotation, an "export diagnostics" button that bundles recent logs and config with all secrets removed.
4. Latency and quota dashboard: requests used today, average response time, errors.
5. A security review of the codebase: list any place a secret could leak, any unescaped output, any tool that can act without the right approval. Fix what you find and show me the list.
6. Docs: update README (install, configure, run, troubleshoot), add docs/architecture.md with a diagram (Mermaid), and docs/portability.md explaining exactly what to change to run on a Raspberry Pi 5.
7. tests/e2e_demo.md: a scripted demo checklist: greet, remember a preference, set a reminder, read a sensor, toggle a relay with approval, recover from the internet going off.
8. Run the whole test suite and report coverage gaps.

ACCEPTANCE: I can follow the README on a clean machine and have Nalla running in under 30 minutes.
```

### Debug prompt (use any time something breaks)

```
Read CONTEXT.md. Something is broken in <module>.
What I did: <steps>. What I expected: <expected>. What happened: <error text or behavior>.
First, explain the most likely root cause in plain language and list 2 other possibilities. Then add logging if needed, reproduce the problem, fix the root cause (not just the symptom), add a regression test, and tell me how to verify. Do not change unrelated files.
```

### Review prompt (end of each phase)

```
Review everything built in this phase against CONTEXT.md. List: (1) deviations from the safety rules, (2) anything claiming success without confirmation, (3) places where a secret or sensitive data could be logged or exposed, (4) missing tests. Fix the issues, then give me a 5-line summary.
```

## 10. Looking ahead (not in v1)

- Move to a Raspberry Pi 5 (same code, new `.env`).
- Train a custom "Nalla" wake word.
- Calendar, email-draft and file-search tools.
- A desk-gadget body for Nalla: ESP32 with display, mic and speaker.
- A local Ollama model for offline, simple commands.
- Becoming the shared brain for other Why.axis gadgets.
