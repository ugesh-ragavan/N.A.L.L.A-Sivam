# Third-Party Notices & Attribution

This document records all third-party open-source code, algorithms, and design patterns ported or adapted into the **NALLA SIVAM** project in accordance with `CONTEXT.md` (Identity Guard Rule 8).

---

## 1. OpenJarvis

- **Originating Repository:** `openjarvis/OpenJarvis` (Stanford IPW research)
- **License:** Apache License 2.0 (Copyright OpenJarvis Authors)
- **Files Referenced:**
  - `src/openjarvis/speech/faster_whisper.py`
  - `src/openjarvis/speech/voice_io.py`
- **Ported / Adapted Components:**
  1. **Dynamic CTranslate2 Compute Resolution:** `_resolve_compute_type(device, requested)` in `app/voice/stt.py` inspects `ctranslate2.get_supported_compute_types()` to safely select supported quantization (`int8` or `float32`) without crashing on CPU architectures.
  2. **Pure Python RMS Silence Detection:** `_compute_pcm_rms(data)` in `app/voice/vad.py` calculating Root Mean Square amplitude directly via `struct.unpack` on 16-bit PCM frames as a lightweight fallback when WebRTC VAD is bypassed or uninstalled.
- **License Notice:**
  ```text
  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
  ```

---

## 2. Javis OS

- **Originating Repository:** `blogminhquy/javis-os`
- **License:** MIT License (Copyright 2026 Nguyễn Minh Quý / blogminhquy)
- **Files Referenced:**
  - `dashboard/voice-chunker.js`
  - `dashboard/voice-live-worklet.js`
- **Ported / Adapted Components:**
  1. **Early Clause Stream Chunking:** Adapted in `app/voice/pipeline.py` and `app/web/static/js/app.js` to emit the initial speech chunk after 6–10 words upon encountering a punctuation mark or conjunction (`and`, `but`, `then`, `because`), reducing initial Time-to-First-Audio latency.
  2. **Dedicated AudioWorklet Processor:** Adapted in `app/web/static/js/pages/voice.js` for thread-isolated 16 kHz PCM microphone capture.
- **License Notice:**
  ```text
  MIT License

  Copyright (c) 2026 Nguyễn Minh Quý (blogminhquy)

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.
  ```

---

## 3. LiveKit Agents / Jarvis Voice Butler

- **Originating Repository:** `livekit/agents` / `jarvis-voice-butler`
- **License:** MIT License (Copyright 2025 LiveKit, Inc.)
- **Files Referenced:** `src/tools.py`
- **Ported / Adapted Components:**
  - **Two-Phase Action Confirmation Pattern:** Conceptual inspiration for two-stage approval gate before executing state-changing device actions.
