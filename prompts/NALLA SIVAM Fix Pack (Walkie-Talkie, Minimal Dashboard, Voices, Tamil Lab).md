# NALLA SIVAM: Fix Pack (Walkie-Talkie, Minimal Dashboard, Voices, Tamil Lab)

Four Antigravity tasks. Do them in this order: **A** (walkie-talkie, the biggest daily-feel win and a speed gain), **B** (minimal dashboard), **C** (simple voice setup), then **D + E** (the Tamil Voice Lab and Tamil text pipeline, which need your ears). Read each plan before approving.

## Why the Tamil sounds wrong (likely causes)

I can't hear audio, so you are the judge. These are the usual culprits, and the prompts below attack each one:

1. **Tanglish in English letters** ("sollunga", "paathukalam") is read with English phonetics by most voices. Tamil should be spoken from Tamil script.
2. **One voice for mixed sentences.** An English voice mangles Tamil words, and a Tamil voice mangles English words. Each language needs its own voice, switched inside the sentence.
3. **Formal vs spoken Tamil.** Engines are usually trained on bookish Tamil. Colloquial forms (பண்ணுங்க, சாப்டீங்களா) and regional flavour like Kongu are the hard part, so you need to test them, not assume.
4. **Dirty text going into TTS:** emoji, markdown, symbols, numbers and times that the engine reads oddly.
5. **Speech-to-text:** Whisper `base` mishears Tamil, so Nalla may answer the wrong question. Test `small`, `medium` and Gemini audio transcription.

**What exists for Tamil voices:** Microsoft's edge-tts has Tamil voices for India (ta-IN-PallaviNeural, female; ta-IN-ValluvarNeural, male) and also Sri Lanka, Malaysia and Singapore variants. Gemini TTS supports Tamil with automatic language detection. I could not confirm a Tamil voice for Piper, so check its voice list; if none exists, offline Tamil speech is not available and the app should say so instead of reading Tamil with an English voice.

## Prompt A: Walkie-talkie mode (hold Space to talk, release to send)

```
Read CONTEXT.md (Identity Guard) first. Plan, then build walkie-talkie mode.

GOAL: press and HOLD Space to speak, RELEASE to send. No wake word, no silence detection needed in this mode. It must feel instant.

1. Setting talk_mode: walkie_talkie (default) | tap_toggle | wake_word (later). Keep existing modes working.

2. Browser keys: on the dashboard, Space keydown (ignore event.repeat) sends {type:"ptt_down"} over the WebSocket and keyup sends {type:"ptt_up"}, but ONLY when focus is not in an input, textarea, select or contenteditable. Call preventDefault so the page does not scroll. Treat window blur, tab visibility change and WebSocket disconnect as a release/cancel so the mic can never get stuck on.

3. On-screen button: a large circular HOLD TO TALK button using pointer events with pointer capture, so it works with a mouse and with touch on my phone. Same ptt_down / ptt_up messages.

4. Optional global hotkey (works when the browser is not focused) using pynput. Default OFF. Default key F9, configurable in settings. Do NOT offer plain Space as a global key because it would hijack typing everywhere. Add notes for OS permissions (macOS Accessibility, Linux Wayland limits).

5. Server-side capture: on ptt_down open the mic stream (sounddevice) and buffer audio. Add an optional keep_mic_warm setting (default OFF) that keeps a 250 ms in-memory ring buffer so the first word is never clipped; when ON, show a clear mic-active indicator and never store or send that buffer unless a press happens.

6. On ptt_down: if Nalla is speaking, stop playback instantly and cancel any pending TTS and LLM tasks (barge-in), then start listening. Show state LISTENING, a live level meter and a timer. Optional soft click sound (setting).

7. On ptt_up: stop capture, keep a 200 ms tail, and send the audio straight to STT with NO VAD wait. Ignore holds shorter than 0.35 s as accidental taps and show a small "hold to talk" hint. Auto-release at 60 s with a warning tone. Esc while holding cancels and discards the audio.

8. If I press again while Nalla is THINKING or SPEAKING, cancel the current turn cleanly and start the new one.

9. Optional streaming_stt flag (default off): while I hold, transcribe rolling 2-second chunks with faster-whisper so only the last chunk is left to process at release. Measure whether it helps.

10. Timing: add a span release_to_first_audio (release > STT > LLM first token > TTS first audio) as THE main latency metric, shown in the debug drawer. Target: under 2 seconds for short questions.

11. Tests with simulated events: down/up sequence, repeat keydown ignored, short tap ignored, blur releases, max-hold auto-release, Esc cancels, barge-in stops playback, press during THINKING cancels the old turn. No audio hardware needed.

ACCEPTANCE: hold Space, say "what time is it", release, hear the answer quickly; interrupting Nalla by pressing Space works instantly; unplugging the network or closing the tab never leaves the mic on.
```

## Prompt B: Minimal, useful dashboard

```
Read CONTEXT.md (Identity Guard) first. The current dashboard is too busy. Simplify it WITHOUT deleting work: move the pages being removed into web/_parked/ with a README on how to restore them. Plan first and show me the new screen list before building.

PRINCIPLE: every control must earn its place. If I would not use it weekly, it goes under Advanced or into the Debug drawer.

SCREENS (only these three):
1. TALK (default): a small calm orb (subtle states: idle, listening, thinking, speaking), a status chip row (Gemini, mic, voice engine), the conversation transcript (last 20 messages, scrollable), the big HOLD TO TALK button with a Space hint, a text input box, and a Stop button.
2. MEMORY: two tabs, Memories and Reminders. Search, inline edit, delete with confirmation, add, export.
3. SETTINGS (grouped, with search): Voice (engine, English voice, Tamil voice, speed, preview, link to the Voice Lab), Personality (three sliders: Sarcasm, Warmth, Tamil mix; a mode dropdown; persona text editor under Advanced; locked Identity Guard cards), Talk mode (walkie-talkie, tap, global hotkey), Models (chat model, STT engine), API key (masked), Data (export/import).

DEBUG DRAWER (toggle with the D key or a small icon): last 10 turns with the latency breakdown, errors and logs, quota counters for LLM and TTS, fallback events. This replaces the separate Performance and Activity pages.

REMOVE FROM VIEW (park, do not delete): Devices page (hardware is on hold), command palette, multiple accent themes (keep one dark and one light theme), heavy backgrounds and particle effects, the six-page sidebar.

DESIGN: calm and readable, dark by default. One accent colour, large type, generous spacing, system font stack plus one clean display font bundled locally. The orb animation is subtle and respects prefers-reduced-motion. Responsive for laptop and phone. Keyboard: Space (hold) = talk, Esc = cancel/stop, / = focus the text box, D = debug drawer.

RULES: plain HTML, CSS and vanilla JS, no framework or build step; bind to 127.0.0.1; escape all rendered text; never expose the API key; keep every existing API working.

ACCEPTANCE: the first screen shows only the orb, transcript, hold-to-talk button and text box; any setting is at most 3 clicks away; no console errors; it loads fast and looks clean on my phone.
```

## Prompt C: Simple voice setup (3 engines, change voices easily)

```
Read CONTEXT.md (Identity Guard) first. Plan, then simplify the voice settings.

ENGINES: Gemini TTS, Edge (Microsoft, online), Piper (offline). Keep all three behind our TTS interface.

1. Two voice SLOTS instead of one global voice: English voice and Tamil voice. Each slot has its own engine, voice, speed (rate) and volume. This lets Tamil use the best Tamil engine while English uses a different one.
2. Voice lists load dynamically: edge-tts list_voices (show ta-IN first, then ta-LK, ta-SG, ta-MY, and English India); Gemini voices (prebuilt plus the voice library filtered by language); installed Piper voices. Do not hardcode names.
3. A Preview button on every voice that speaks a sample line in that slot's language ("Yes boss, sollunga!" for English; the Tamil version for Tamil).
4. Auto-switch by language (default on): speak each sentence, or each run of words, in its own language's voice.
5. HARD RULE: never read Tamil text with an English-only voice, and never read English text with a Tamil-only voice if an English voice is available. Fallback chains are per language: Tamil: preferred > other Tamil-capable engine > text only with a small notice. English: preferred > any other engine.
6. Keep the Gemini TTS quota guard, cache for fixed phrases, and fallback timings from the earlier prompts. Show which engine actually spoke each sentence in the debug drawer.
7. Voice profiles: save up to 5 named profiles (both slots, speed, style) and switch between them in one click.
8. All changes apply live without a restart and are saved in settings.

ACCEPTANCE: I can pick an English voice and a Tamil voice from different engines, preview both, speak a mixed Tamil-English sentence with each part in the right voice, and see an honest notice (not a wrong-language voice) if a Tamil engine is down.
```

## Prompt D: Tamil Voice Lab (check pronunciation and slang properly)

First save the test sentences below as `data/tamil_tests.json` (or let the agent create it from this table). **You are the judge**: you speak Tamil, I do not hear audio. Please correct any spelling you notice in the test sentences before running.

**Test sentences** (id, category, Tanglish, Tamil):

| id | category | Tanglish | Tamil |
| --- | --- | --- | --- |
| f1 | formal | Vanakkam, naan Nalla Sivam. Ungalukku eppadi udhava vendum? | வணக்கம், நான் நல்ல சிவம். உங்களுக்கு எப்படி உதவ வேண்டும்? |
| f2 | formal | Indru vaanilai migavum inimaiyaaga irukkiradhu. | இன்று வானிலை மிகவும் இனிமையாக இருக்கிறது. |
| c1 | colloquial | Yes boss, sollunga! | எஸ் பாஸ், சொல்லுங்க! |
| c2 | colloquial | Seri boss, paathukalam. | சரி பாஸ், பார்த்துக்கலாம். |
| c3 | colloquial | Aiyo boss, device confirm pannala. | ஐயோ பாஸ், டிவைஸ் கன்ஃபார்ம் பண்ணல. |
| c4 | colloquial | Sema idea boss, aana mudhalla irukkira velaiya mudinga. | செம ஐடியா பாஸ், ஆனா முதல்ல இருக்கிற வேலையே முடிங்க. |
| c5 | colloquial | Innaiku romba neramaa velai paakkareenga, konjam break edunga. | இன்னைக்கு ரொம்ப நேரமா வேலை பாக்கறீங்க, கொஞ்சம் பிரேக் எடுங்க. |
| c6 | colloquial | Naalaikku kaalaila aaru manikku reminder vechirukken. | நாளைக்கு காலைல ஆறு மணிக்கு ரிமைண்டர் வெச்சிருக்கேன். |
| c7 | colloquial | Memory-la save pannitten boss. Marakkamaatten. | மெமரில சேவ் பண்ணிட்டேன் பாஸ். மறக்கமாட்டேன். |
| c8 | colloquial | Enakku theriyala boss, theriyaadhatha theriyumnu solla maatten. | எனக்கு தெரியல பாஸ், தெரியாததை தெரியும்னு சொல்ல மாட்டேன். |
| k1 | Kongu flavour | Enunga, saapteengala? Innaiku enna pudhu velai? | ஏனுங்க, சாப்டீங்களா? இன்னைக்கு என்ன புது வேலை? |
| k2 | Kongu flavour | Enna kannu, innum thoongalaya? | என்ன கண்ணு, இன்னும் தூங்கலயா? |
| m1 | mixed English | Gemini API konjam slow-a irukku, retry panren. | ஜெமினி API கொஞ்சம் ஸ்லோவா இருக்கு, ரீட்ரை பண்றேன். |
| m2 | mixed English | Unga Wi-Fi connect aagala boss, router-a oru thadava restart pannunga. | உங்க Wi-Fi கனெக்ட் ஆகல பாஸ், ரௌட்டரை ஒரு தடவை ரீஸ்டார்ட் பண்ணுங்க. |
| n1 | time | Ippo mani maalai aaru muppadhu. | இப்போ மணி மாலை ஆறு முப்பது. |
| n2 | date | Innaiku thedhi November irubathi moonru, irandaayirathu irubathaaru. | இன்னைக்கு தேதி நவம்பர் இருபத்தி மூன்று, இரண்டாயிரத்து இருபத்தாறு. |
| n3 | number | Veppanilai muppathi rendu degree. | வெப்பநிலை முப்பத்திரண்டு டிக்ரி. |
| p1 | names | En peru Nalla Sivam. Ugesh ennoda nanban. | என் பேரு நல்ல சிவம். உகேஷ் என்னோட நண்பன். |
| e1 | caring | Paravaala boss, moochu vidunga. Mudhalla oru vishayatha mattum sari pannalaam. | பரவால்ல பாஸ், மூச்சு விடுங்க. முதல்ல ஒரு விஷயத்த மட்டும் சரி பண்ணலாம். |

```
Read CONTEXT.md (Identity Guard) first. Plan, then build a Tamil Voice Lab (a page under Settings > Voice, also reachable from the debug drawer). Test sentences are in data/tamil_tests.json with fields id, category, tanglish, tamil.

PURPOSE: let ME judge Tamil pronunciation and slang quickly and scientifically, then set the best voices.

1. COMPARISON GRID: I pick up to 6 voices (any engine: Edge ta-IN/ta-LK/ta-SG/ta-MY, Gemini voices with Tamil, Piper if a Tamil voice exists) and a set of sentences (all, or by category). The lab generates audio for each (sentence x voice), caches it in data/voice_lab/, and shows a grid with play buttons side by side plus time-to-first-audio per cell.

2. INPUT VARIANT TEST: for each sentence render THREE text variants and let me compare them per voice: (a) Tamil script, (b) Tanglish in English letters, (c) Tamil script after applying the pronunciation dictionary (Prompt E). This shows me whether script choice fixes the accent.

3. RATING: for every cell, buttons Good / OK / Bad plus optional issue tags: wrong stress, English-style accent, word skipped or garbled, too formal or bookish, robotic, too fast, too slow, wrong word pronunciation (let me type which word), and a free-text note. Save to data/voice_lab_results.json.

4. SUMMARY VIEW: average score per voice per category (formal, colloquial, Kongu flavour, mixed English, time, date, number, names, caring), the most common issue tags, a table of words flagged as mispronounced, and a recommendation: best Tamil voice and best English voice, with a Set as Tamil voice / Set as English voice button. Export CSV.

5. ROUND-TRIP CHECK (a hint, not a verdict): transcribe each generated clip back with faster-whisper (and optionally Gemini audio) and show the character error rate against the source text. High error flags sentences worth listening to first. State clearly in the UI that STT errors can confuse this check.

6. FEEDBACK LOOP: from any flagged word, a one-click "fix pronunciation" opens the dictionary editor (Prompt E) with the word prefilled; after saving, re-render that cell.

7. Tests for the grid generation, caching, rating persistence, summary maths and the round-trip scoring (mock the engines).

ACCEPTANCE: I can compare at least 4 voices on all test sentences in one sitting, rate them quickly, and see which voice and which text variant is best. The lab sets my Tamil and English voices from the results.
```

**How to judge (your checklist per clip):** are the words clear, are the letters and stress right, does it sound like spoken Tamil (not a newsreader), are English words in the sentence handled well, is the voice pleasant for Nalla's character, is the speed natural.

## Prompt E: Tamil text pipeline and speech-to-text

```
Read CONTEXT.md (Identity Guard) first. Plan, then build the Tamil text pipeline. Use the Voice Lab results to decide defaults; do not guess.

1. LANGUAGE SEGMENTATION: split every reply into sentences, then into runs by script (Tamil script, Latin script) and merge runs shorter than 3 words into their neighbours so voices do not switch every word. Route Tamil runs to the Tamil voice and English runs to the English voice (Prompt C rules).

2. TANGLISH-TO-TAMIL SPEECH CONVERSION (speech only, display text stays as the model wrote it): data/tanglish_dict.json maps Tanglish words to Tamil script. Apply it with longest-match, case-insensitive, and ONLY to tokens found in the dictionary, so English words stay English. Seed it with the entries below, then let me edit it in the dashboard (add/remove/search) and import/export JSON. I will correct any spelling that looks wrong.

Seed entries: sollunga → சொல்லுங்க; seri → சரி; boss → பாஸ்; sema → செம; aiyo → ஐயோ; paathukalam → பார்த்துக்கலாம்; illa → இல்ல; irukku → இருக்கு; enna → என்ன; eppadi → எப்படி; romba → ரொம்ப; konjam → கொஞ்சம்; naalaikku → நாளைக்கு; innaiku → இன்னைக்கு; vaanga → வாங்க; pannunga → பண்ணுங்க; pannala → பண்ணல; mudinjuchu → முடிஞ்சுச்சு; theriyum → தெரியும்; theriyala → தெரியல; machan → மச்சான்; nalla → நல்ல; sivam → சிவம்; vanakkam → வணக்கம்; nandri → நன்றி; kannu → கண்ணு; aama → ஆமா; venam → வேணாம்; venum → வேணும்; podhum → போதும்; moochu → மூச்சு; paravaala → பரவால்ல.

3. TTS-SAFE CLEANING: strip emoji, markdown, URLs (say "link") and stray symbols. First test what each engine does natively with numbers, times and dates (Voice Lab rows n1, n2, n3); add a number/time/date normaliser per language ONLY where the lab shows it is needed.

4. TAMIL TEXT MODE setting, applied in the prompt builder with matching few-shot examples:
   - tanglish_display (default): the model writes Tanglish in English letters; speech uses the dictionary conversion.
   - tamil_script: the model writes Tamil words in Tamil script and English words in English; best pronunciation, needs me to read Tamil on screen.
   - english_only.
   Add this style rule to the persona prompt: use everyday spoken Tamil (colloquial), not bookish written Tamil.

5. KONGU TOUCH (optional slider, default low): only use phrases from data/kongu_phrases.json, a list I curate myself. Never invent dialect words. Start the file empty with a few examples I can edit (such as the words in test rows k1 and k2).

6. SPEECH-TO-TEXT FOR TAMIL: add an STT engine selector: faster-whisper (base, small, medium) and Gemini audio transcription (uses quota). Add a Listening Lab next to the Voice Lab: I record 10 Tamil and Tanglish phrases with the walkie-talkie button, and the page shows each engine's transcript side by side so I can mark which is correct and pick the default. Report latency for each engine.

7. Tests: segmentation, dictionary matching (longest match, English words untouched), cleaning, mode switching in the prompt builder, and STT engine selection (mock engines).

ACCEPTANCE: after setting the voices chosen in the Voice Lab and adding my fixes to the dictionary, Nalla's Tamil lines like c1 to c8 sound right to me, mixed sentences use the right voice for each part, and the Listening Lab shows which STT engine understands my Tamil best.
```

## Voice debug prompt

```
Read CONTEXT.md. A voice problem: <describe>.
Example text: <the sentence>. Voice and engine: <which>. What I hear: <e.g. English accent, word skipped, wrong stress, silence>.
First list the most likely causes in order (text variant, language routing, dictionary, engine or voice limitation, cleaning, speed, caching of an old clip). Check the cache first (a stale cached clip can hide a fix). Reproduce in the Voice Lab, try the other text variants, and tell me whether it is fixable in our code or a limit of that voice. Fix what is fixable, add the word to the dictionary if needed, add a regression test, and do not weaken the Identity Guard.
```

## Suggested order

1. Prompt A (walkie-talkie), test it for a day.
2. Prompt B (minimal dashboard).
3. Prompt C (two voice slots), then Prompt D (Voice Lab). Spend 30 to 45 minutes rating clips.
4. Prompt E (Tamil pipeline), using what you learned in the lab. Send me the Voice Lab summary and I will help interpret it.
