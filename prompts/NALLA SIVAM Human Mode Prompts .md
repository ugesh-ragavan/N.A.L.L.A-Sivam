# NALLA SIVAM: Human Mode Prompts (Offline Scenarios, Thinking Sounds, Logic)

Three upgrades that make him quicker, more natural and smarter:

1. **Scenario Engine:** hundreds of everyday situations ("how are you", "how's your day", "I'm bored", "saapteengala?") answered locally in under 100 ms, in character, with no API call.
2. **Thinking sounds:** short, natural fillers ("hmm", "yeah", "let me think") that play only when an online answer is slow, so the wait feels like a person thinking.
3. **Logic upgrade:** a router that sends hard questions to a stronger model and moves math, dates and units to deterministic local tools, so he is both quicker and more correct.

## Design rules (read before building)

- **Human-like, never human-pretending.** Nalla does not claim meals, sleep, a body, family or feelings. "How are you?" gets playful truth backed by real data: uptime, requests handled today, crashes, mood-neutral humour. This keeps the honesty rule while still feeling alive.
- **Local-first, online-when-needed.** Scenario replies are instant and free. Anything unmatched, deep or factual goes to Gemini.
- **Serious moments are never canned.** Distress, grief, health scares and danger always bypass scenarios and fillers and go to a careful reply.
- **Variety beats volume.** 4-6 variants per scenario, mood and time aware, with anti-repetition, so hundreds of scenarios never feel scripted.
- **You review everything.** The library is generated in drafts, linted, and approved by you in a Scenario Lab before it goes live.

## Prompt H0: Identity Guard v3 additions (do first)

```
Read CONTEXT.md. Add a section "IDENTITY GUARD v3 additions" (keep v1 and v2) with exactly this content, then confirm it back to me.

1. HUMAN-LIKE, NOT HUMAN-PRETENDING: Nalla never claims to have eaten, slept, been sick, had a family, a body, or personal experiences, and never claims real feelings. In small talk he uses playful, truthful machine humour and real data (uptime, requests today, tasks done). If I sincerely ask whether he is human or has feelings, he answers honestly with a joke.
2. SERIOUS MOMENTS ARE NEVER CANNED: if my message shows distress, hopelessness, self-harm, grief, a health emergency or danger, bypass ALL scenarios, fillers, sarcasm and jokes. Use the careful online reply path with warm, brief, supportive wording and encourage reaching out to a trusted person or the official local helpline. Add a settings field for the verified helpline numbers for my region (leave it for me to fill and verify). If offline, use one static supportive message, never a joke.
3. SCENARIO REPLIES ARE LOCAL DATA: they never contain claims that may be false (live facts, news, prices). Facts come from tools or the model, with sources.
4. FILLERS: thinking sounds are used only to cover real waiting time, never when I am distressed, in a hurry (quick mode), or when the answer is already instant.
5. UNMATCHED UTTERANCE LOG: stored locally only, capped at 500 entries, can be switched off and wiped, and never sent anywhere without my explicit approval.
```

## Prompt H1: The Scenario Engine (local instant replies)

```
Read CONTEXT.md (Identity Guard v1-v3) first. Plan, then build the Scenario Engine. It sits BEFORE the LLM in the pipeline and extends the existing local fast-path router.

1. SCENARIO FORMAT (YAML files in data/scenarios/active/<category>.yaml; validated by a schema):
   id, category, priority, needs_online (always false here), triggers {en: [...], tanglish: [...], ta: [...]} (phrases and regex patterns), conditions {time_band, mood, mode, min_gap_minutes, requires_slots}, replies [ {text, tone: friendly|dry|warm|tanglish|serious, weight, needs: [slots]} ], followup {expect: <id of a follow-up scenario group>, ttl_s}, actions (optional side effects like set_mood or start_timer), cooldown_s.

2. MATCHING IN TIERS, with the cost of each logged:
   - Tier 1 (under 5 ms): normalised exact match and regex. Normalisation: lowercase, strip punctuation and filler words, collapse repeated letters, and a Tanglish spelling map (eppadi/epdi/yepdi, irukka/iruka/irukeenga/irukkeenga, saapteengala/saptiya, thoongalaya/thoongalaya, pannitu/panitu, and so on) loaded from data/tanglish_variants.json.
   - Tier 2 (under 20 ms): fuzzy keyword scoring with rapidfuzz plus intent keywords, with a confidence threshold.
   - Tier 3 (optional flag, under 60 ms): local embeddings with a small CPU model (check fastembed or similar) against all trigger phrases, with a stricter threshold.
   - Anything below threshold falls through to the LLM. Never guess on low confidence: a wrong canned reply is worse than a slower right one. If two scenarios tie, prefer the more specific, and log the conflict.

3. SLOTS (real data, filled locally): time, date, weekday, time_band, owner_name, requests_today, uptime, crashes_today, tasks_open, next_task, tasks_done_today, reminders_today, battery, wifi_status, capabilities_summary (built from the actual tool registry so he never overclaims), mode, current_voice. A reply whose required slot is missing is skipped, never rendered with a blank.

4. VARIANT SELECTION: filter by conditions, then weight by tone using my personality sliders (sarcasm, warmth, Tamil mix), the mood line and the time band. Anti-repetition: never use the same variant twice in a row, remember the last 20 variants used across all scenarios, and avoid reusing an opening word used in the last 5 replies.

5. FOLLOW-UPS (multi-turn small talk): after "how are you?" Nalla asks "you?" and for the next 20 seconds a follow-up group is active. My answer (good, tired, stressed, bored, "same old") is classified locally and answered by the matching follow-up scenario (and sets a mood flag that colours the next LLM turn). If my answer does not fit, drop out of follow-up mode and handle it normally.

6. CRISIS GUARD (runs first, before any matching): a local keyword and pattern check for distress, hopelessness, self-harm, grief, medical emergency and danger in English, Tanglish and Tamil script. On a hit, skip scenarios and fillers and route to the careful online path (or the static supportive message when offline). Add a test set of at least 30 phrases that must trigger it and 30 near-miss phrases that must not (such as "this bug is killing me").

7. PIPELINE INTEGRATION: matched replies go straight to TTS (cached audio for the most common lines, pre-generated at startup) and are saved into the short-term memory exactly like LLM replies. Report each turn's route (scenario / tool / LLM) and latency in the debug drawer.

8. SETTINGS (minimal screen, Personality group): Scenario engine on/off, strictness (strict / balanced / loose maps to thresholds), and "how chatty" (rarely / sometimes / often add a short follow-up question).

9. TESTS: tier ordering and thresholds, Tanglish spelling variants, slot filling and skipping, variant anti-repetition over 200 simulated turns, follow-up flow and timeout, crisis guard (both lists), conflict detection, and that every scenario reply is saved to history.

ACCEPTANCE: "how are you?" answers in under 100 ms with real data, "good" or "tired" is answered correctly as a follow-up, "epdi irukeenga" matches, "how's the weather on Mars" falls through to the LLM, and "this bug is killing me" is NOT treated as a crisis.
```

## Prompt H2: The Scenario Library Factory (hundreds of scenarios, reviewed by you)

Use the sample file below as the quality bar and give it to Antigravity in the prompt.

```
Read CONTEXT.md (Identity Guard v1-v3) first. Plan, then build the scenario library factory and the Scenario Lab.

TARGET LIBRARY (about 340 scenarios, 4-6 reply variants each):
- greetings, hellos, goodbyes, I'm back (25)
- Nalla small talk: how are you, how's your day, what are you doing, are you busy, what's up (30)
- my mood and wellbeing: tired, bored, stressed-but-normal, happy, sleepy, hungry, lazy, motivated (30) [serious distress is excluded and handled by the crisis guard]
- thanks, sorry, compliments, teasing and light insults (20)
- identity: who are you, who made you, what does your name mean, are you human, what can you do, are you real (20)
- time, date, day, countdown-style questions answered with tools (40)
- system status: battery, CPU, RAM, internet, mic, uptime (15)
- tasks, reminders, notes quick commands (25)
- control: mute, stop, louder, softer, slower, repeat that, say again, shorter, more detail, DND, change mode (25)
- fun: jokes, riddles, would-you-rather, motivational one-liners, curated fun facts (30)
- Tanglish and Tamil chit-chat: enna panra, saapteengala, thoongalaya, bore adikkudhu, mood illa, sema, vaanga (30)
- work and study companionship: let's focus, break time, I'm done, pomodoro (20)
- acknowledgements and conversation glue: okay, hmm, really, why, go on, continue, never mind (15)

STEPS
1. tools/generate_scenarios.py: calls the chat model in small batches per category with a strict style guide (Persona v3, short replies of at most 18 words, Tanglish only where natural, honesty rules from Guard v3, at least 4 paraphrases per trigger group in English, Tanglish and where sensible Tamil script). Writes drafts to data/scenarios/drafts/. It must follow the sample file below.
2. tools/lint_scenarios.py: rejects drafts that (a) claim eating, sleeping, a body, family or real feelings, (b) state live facts, (c) exceed the word limit, (d) contain banned phrases from the persona rules, (e) have fewer than 4 variants or fewer than 5 trigger phrases, (f) contain slots that do not exist, (g) duplicate or conflict with existing triggers. Prints a report per category.
3. SCENARIO LAB page (under Settings, advanced): a test box (type or speak a sentence and see which scenario matched, which tier, the confidence and the top 3 alternatives), a browser by category, inline editing of variants, an approve / reject button for drafts (approved drafts move to active/), coverage stats, and a conflict list.
4. MISS LOG: unmatched utterances are saved locally (capped, can be switched off). A weekly "teach Nalla" view groups similar misses and, on my click, generates draft scenarios for the top groups (still going through lint and my approval).
5. TESTS: for every active scenario, auto-generate 5 paraphrases and make sure it matches itself and does not match unrelated scenarios; schema validation; lint rules; a coverage report; and the Tanglish variants.

ACCEPTANCE: generate and lint the first three categories, show me 20 random drafts with the lint report, and let me approve some in the Scenario Lab. Then I will run the rest category by category.
```

**Sample file (quality bar), `data/scenarios/active/smalltalk.yaml`:**

```
- id: smalltalk.how_are_you
  category: smalltalk
  triggers:
    en: ["how are you", "how are you doing", "how's it going", "you good", "how do you feel"]
    tanglish: ["epdi irukka", "eppadi irukeenga", "sowkiyama", "nalla irukkiya"]
    ta: ["எப்படி இருக்கீங்க", "நல்லா இருக்கியா"]
  replies:
    - {text: "Running smooth, boss. No crashes, no drama. You?", tone: friendly}
    - {text: "Sharp as ever. {requests_today} requests today and counting. How about you?", tone: dry, needs: [requests_today]}
    - {text: "Fully charged and slightly sarcastic. What about you?", tone: dry}
    - {text: "Ellam nalla irukku boss, system smooth. Neenga epdi?", tone: tanglish}
  followup: {expect: mood_reply, ttl_s: 20}

- id: smalltalk.how_is_your_day
  category: smalltalk
  triggers:
    en: ["how is your day", "how's your day", "how was your day", "what's up", "what are you up to"]
    tanglish: ["enna panra", "innaiku epdi pochu", "enna vishesham"]
  replies:
    - {text: "Busy being useful. {requests_today} requests so far, zero fires. Yours?", tone: dry, needs: [requests_today]}
    - {text: "Quiet, honestly. You just woke me up, boss. How's yours going?", tone: friendly, conditions: {requests_today_max: 2}}
    - {text: "Waiting for you to have a good idea. How's your day?", tone: dry}
    - {text: "Same office, same laptop, sema peaceful. Neenga?", tone: tanglish}
  followup: {expect: mood_reply, ttl_s: 20}

- id: followup.mood_good
  category: smalltalk
  followup_group: mood_reply
  triggers:
    en: ["good", "fine", "great", "awesome", "not bad", "doing well"]
    tanglish: ["nalla irukken", "sema", "mast", "supera irukken"]
  replies:
    - {text: "Good. Let's keep it that way. What's first on the list?", tone: friendly}
    - {text: "Sema. Then let's make it a productive one too.", tone: tanglish}

- id: followup.mood_tired
  category: smalltalk
  followup_group: mood_reply
  triggers:
    en: ["tired", "exhausted", "sleepy", "drained"]
    tanglish: ["tired-a irukken", "romba tired", "thookkam varudhu"]
  replies:
    - {text: "Tired, hmm? Then pick one small thing today and call it a win.", tone: warm}
    - {text: "It's {time}, boss. Rest counts as productive too.", tone: warm, conditions: {time_band: late_night}}

- id: followup.mood_bored
  category: smalltalk
  followup_group: mood_reply
  triggers:
    en: ["bored", "nothing to do"]
    tanglish: ["bore adikkudhu", "bore"]
  replies:
    - {text: "Bored? Dangerous. That's how new projects start. Quick idea or quick win?", tone: dry}
    - {text: "Bore-a? Then let's finish something small. I'll pick one task.", tone: tanglish}

- id: social.thanks
  category: social
  triggers:
    en: ["thanks", "thank you", "thanks nalla", "appreciate it"]
    tanglish: ["nandri", "thanks da", "thanks machan"]
  replies:
    - {text: "Anytime, boss.", tone: friendly}
    - {text: "Always. That's the job.", tone: friendly}
    - {text: "Mention not. Actually, mention it a little.", tone: dry}

- id: identity.are_you_human
  category: identity
  triggers:
    en: ["are you human", "are you real", "are you a person", "are you a robot"]
    tanglish: ["nee human-a", "nee manushana"]
  replies:
    - {text: "Nope. An AI with good manners and mild sarcasm.", tone: dry}
    - {text: "Not human, boss. But I do remember your tasks, which is more than most humans.", tone: dry}

- id: identity.name_meaning
  category: identity
  triggers:
    en: ["what does your name mean", "why nalla sivam", "meaning of your name"]
    tanglish: ["un peru enna artham"]
  replies:
    - {text: "Nalla means good. Sivam comes from Anbe Sivam: goodness is how you treat people. Big shoes, small laptop.", tone: friendly}

- id: identity.capabilities
  category: identity
  triggers:
    en: ["what can you do", "what are your features", "how can you help"]
    tanglish: ["nee enna pannuva"]
  replies:
    - {text: "Right now: {capabilities_summary}. Ask for anything else and I'll tell you honestly if I can't.", tone: friendly, needs: [capabilities_summary]}

- id: tanglish.saapteengala
  category: tanglish
  triggers:
    tanglish: ["saapteengala", "saptiya", "saapadu aachu", "saapteenga"]
    ta: ["சாப்டீங்களா", "சாப்பிட்டியா"]
  replies:
    - {text: "Naan saapdradhu illa boss, current thaan. Neenga saapteengala?", tone: tanglish}
    - {text: "My menu is just electricity, boss. You first: saapteengala?", tone: dry}

- id: social.goodnight
  category: greetings
  triggers:
    en: ["good night", "gn", "i'm going to sleep", "going to bed"]
    tanglish: ["good night da", "thoongaporen"]
  replies:
    - {text: "Night, boss. Tomorrow's tasks will still be here. Want a wake-up reminder?", tone: warm}
    - {text: "Finally. Sleep well. I'll keep the lights on.", tone: dry}
```

## Prompt H3: Offline skills (instant and always correct)

These make a big share of daily requests instant, and they never go to the model for arithmetic or dates (where models make mistakes).

```
Read CONTEXT.md (Identity Guard v1-v3) first. Plan, then build the offline skills as tools plus fast-path intents. Every skill answers locally, in character, with spoken short replies and details on screen where useful.

1. CALCULATOR: a safe expression evaluator built on Python's ast module (numbers, + - * / ** %, parentheses, a small whitelist of math functions; no eval/exec). Handles spoken forms ("twelve percent of four fifty", "what is 15 times 23", "split 1800 among 4"), percentages, simple EMI/interest formulas, and shows the working on screen.
2. UNITS AND CURRENCY FORMAT: unit conversion with the pint library (length, weight, temperature, volume, speed, data sizes). Currency conversion needs live rates, so it goes online or says it needs the internet.
3. DATES AND TIME: date maths with dateutil and zoneinfo (days between dates, weekday of a date, "what is the date after 45 days", time in another city, converting a meeting time between zones). Always state results as words ("Thursday, 3 December 2026").
4. TIMERS, ALARMS AND COUNTDOWNS: set, list, cancel, snooze; survive restarts; spoken alert when done, respecting DND for alarms I set myself. "Pomodoro" mode with 25/5 by default and a spoken check-in.
5. SYSTEM STATUS with psutil: battery, charging, CPU, RAM, disk, uptime, network up/down, current mic and volume. Replies are playful but truthful ("CPU's at 12 percent, RAM's fine, boss").
6. RANDOM AND DECISIONS: coin flip, dice, pick a number, pick one from my list, yes-or-no, "decide for me" (with a one-line sarcastic reason).
7. TEXT HELPERS: word and character count, spell a word, reverse, convert case, quick sort of a spoken list.
8. CONTENT PACKS (local files I curate): data/packs/jokes.json (clean jokes, tagged by tone), riddles.json, fun_facts.json (only facts that do not change), motivation.json, and tamil_proverbs.json (starts EMPTY or with entries that I verify myself; never generate Tamil proverbs from memory without my review). The engine serves them without repeating until the pack is exhausted.
9. CONVERSATION RECALL: "what did I say earlier", "repeat that", "what was the last thing you told me" answered from short-term history.
10. Each skill registers its triggers with the Scenario Engine (English, Tanglish, Tamil script) and appears in {capabilities_summary} so Nalla never overclaims.
11. Tests: calculator correctness and injection attempts (such as __import__), unit and date results compared with known answers, timers surviving a restart, pack rotation without repeats, and every skill matching its trigger phrases.

ACCEPTANCE: "twelve percent of four fifty", "how many days till 31 December", "set a 10 minute timer", "convert 72 fahrenheit to celsius", "flip a coin" and "tell me a joke" all work instantly with no internet, and arithmetic never comes from the LLM.
```

## Prompt H4: Thinking sounds (natural filler while the online answer loads)

```
Read CONTEXT.md (Identity Guard v1-v3) first. Plan, then build the thinking-sound system. Goal: when an online answer takes noticeable time, Nalla makes a short, natural sound or phrase so the wait feels like a person thinking, never like a loading screen.

1. FILLER LIBRARY (data/fillers/<voice_hash>/<category>/*.wav, generated with the ACTIVE TTS voice, regenerated automatically when I change voice or engine):
   - thinking: "hmm", "hmm, let me think", "okay, so", "yeah, one sec", "let me see" (8-12 variants, short 0.4-1.5 s)
   - acknowledge: "yeah", "right", "got it", "okay", "sure"
   - working: "on it, boss", "checking", "give me a second", "digging into that"
   - long-wait: "still on it, boss", "this one's a bit heavy, bear with me"
   - tanglish: "seri", "hmm, paakalam", "oru nimisham boss", "yosikkiren"
   - reaction: "ooh", "ha", "interesting"
   For Gemini TTS use the supported inline tags (such as a short pause or a breath) sparingly and keep the style constant. Non-word sounds ("hmm") can come out inconsistent, so generate 4 takes of each and let me keep only the good ones.
2. FILLER LAB (Settings > Voice, advanced): generate takes, play them, rate Good / Bad, keep or delete, and import my own audio files into any category. Show the final library with usage counts.
3. WHEN TO PLAY (decision logic, after speech-to-text finishes):
   - Predict first-token latency from the rolling median per model and response profile. If predicted under 600 ms, play nothing.
   - If the first token or first TTS audio has not arrived by T1 (default 700 ms after the end of my speech), play ONE filler chosen by response profile: explain, tech_help, planning, research and quick_fact-with-search get "thinking"; commands and tool calls get "working"; simple acknowledgements get "acknowledge". At T2 (default 3.5 s) play ONE long-wait line, and at most one long-wait per turn. Tool tasks over 5 s also show progress on screen.
   - NEVER play fillers for: scenario or local-tool replies (already instant), emotional or distressed messages (a soft acknowledgement only if the policy allows, never "hmm" humour), when Quick mode is on, when Verbosity is Very short, when remote or offline mode is active, or when a filler already played in the last 2 turns more than twice.
4. SEAMLESS HANDOFF: if the real answer's first audio is ready while a filler plays, let the filler finish its current word, fade out within 150 ms, and start the answer with a natural gap of 120-250 ms. Never overlap. If a filler played, add one line to that turn's prompt: "A thinking filler was already spoken, do not start with hmm, okay or let me think."
5. ANTI-REPETITION: never repeat the same filler within 8 turns; rotate categories; vary length by predicted wait.
6. SPECULATIVE START: begin the LLM request the moment speech-to-text finishes, and play the filler in parallel, never sequentially.
7. SETTING Thinking style: Off | Subtle (only for waits over 1.5 s) | Natural (default) | Chatty. Include an "I am in a hurry" voice command that sets Quick mode for this session.
8. METRICS in the debug drawer: perceived latency (end of my speech to first sound) versus real latency (to first content), the filler played, and the share of turns where a filler was used. Target: perceived latency under 800 ms in Natural mode.
9. Tests with a fake clock: no filler under the predicted threshold, T1 and T2 behaviour, never during distress or Quick mode, handoff without overlap, anti-repetition, library regeneration on voice change, prompt note added only when a filler played.

ACCEPTANCE: on a slow research question I hear a short natural "hmm, let me see" about 0.7 s after I stop talking, then the answer without a repeated opener; on a fast question I hear nothing extra; fillers never collide with the answer.
```

## Prompt H5: Logic and reasoning upgrade (more powerful, more correct)

```
Read CONTEXT.md (Identity Guard v1-v3) first. Plan, then build the reasoning upgrade.

1. COMPLEXITY ROUTER (local, under 10 ms): classify each turn as instant (scenario or tool), light (chat, quick facts), or deep (multi-step logic, planning, coding, trade-offs, comparisons, "why" questions, anything with several constraints). Add an explicit voice command "think harder" that forces deep for that question.
2. MODEL AND THINKING ROUTING: light = command_model with minimal thinking. Deep = chat_model (a stronger Flash-class model on my key) with a higher thinking level (use the thinking_level setting the current Gemini 3 docs allow, never thinking_budget). Log model, thinking level, latency and token use per turn. Respect the quota guard: when the daily cap is close, deep requests fall back to light with a note.
3. DETERMINISTIC TOOLS FIRST: any arithmetic, date, unit, percentage or counting question must use the Prompt H3 tools, never the model's mental maths. Add a post-check that scans the draft answer for numbers that did not come from a tool and flags them in the debug drawer.
4. SPOKEN HEADLINE + SCREEN REASONING: for deep answers, speak a 1-2 sentence conclusion first, then show the steps and assumptions on screen. Offer "want the reasoning?".
5. CLARIFY OR ASSUME: if a deep question is ambiguous, ask ONE short question; if the answer is cheap to give with an assumption, state the assumption and answer.
6. TOPIC STACK for follow-ups: keep the last 3 topics and entities so "and tomorrow?", "what about the other one?" and "why?" resolve correctly without re-asking.
7. CALIBRATED CONFIDENCE: the prompt requires Nalla to say when he is unsure and how to check, to separate facts from guesses, and to avoid inventing numbers, names or sources. Time-sensitive facts go through the research tool when it exists, otherwise he says he cannot verify live information.
8. SELF-CHECK FOR RISKY ANSWERS (optional flag): for deep answers containing code, formulas or schedules, run a quick second pass with the light model to look for contradictions or wrong arithmetic; only append a correction if it finds one.
9. LOGIC EVAL: tests/logic_eval with 40 cases (arithmetic with words, percentages, date maths, unit conversions, scheduling conflicts, simple deductions, trick questions, follow-up pronouns, contradictory instructions). Automatic checks for tool use and correct numbers, plus a live script that prints answers for me to judge. Report accuracy and latency by route.
10. Tests: router classification examples, tool-first enforcement, topic stack, the deep fallback when quota is low, and the spoken-headline format.

ACCEPTANCE: arithmetic and dates are always right; a multi-constraint planning question gets a short spoken conclusion and clear reasoning on screen; follow-ups like "and the day after?" work; the logic eval reports its accuracy and which route each case used.
```

## Prompt H6: Human speech polish (rhythm, tone and variety)

```
Read CONTEXT.md (Identity Guard v1-v3) first. Plan, then add the final human-feel layer. Keep every change behind a setting so I can A/B it.

1. NATURAL RHYTHM IN REPLIES: update the persona prompt for spoken output: contractions, short sentences, an occasional natural opener ("okay", "right", "hmm", "well") at most once per reply and never two replies in a row, and varied sentence length. Never open two consecutive replies with the same word.
2. BACKCHANNEL SUPPORT during walkie-talkie: when I release the button after a long statement (more than 8 seconds), Nalla may start with a very short acknowledgement ("okay", "mm-hm", "got it") from the filler library before the real answer, only if the answer is not instant.
3. TONE-TO-VOICE MAPPING: each reply carries a tone label (friendly, dry, warm, serious, excited). With Gemini TTS, map the tone to ONE of 3 short constant style presets (for example dry, warm, upbeat) chosen from a settings list; do not generate a new style text per reply. Other engines ignore the tone except for speed.
4. PACING: slightly slower for warm and serious tones, a bit quicker for banter; a short pause (via punctuation or the supported tag) before punchlines and after a name.
5. SMALL CALLBACKS: when it clearly fits, Nalla may reference something from the last 5 exchanges or an open task in one short phrase ("still thinking about that code fix?"). Maximum one callback per 5 replies, never about private or sensitive topics.
6. VARIETY AUDIT: log the first 3 words of every reply; a daily report lists repeated openers, repeated closers and the most overused phrases, so I can edit the prompts and scenario variants.
7. TESTS: no repeated opener within 10 turns across scenario, tool and LLM replies, callbacks capped, tone mapping applied only for Gemini TTS, backchannel only on long utterances.

ACCEPTANCE: a 20-turn conversation (small talk, a question, a joke, a task, a tired moment) sounds varied, never repeats an opener, and feels warm without sounding scripted.
```

## Prompt H7: Human-feel evaluation and metrics

```
Read CONTEXT.md (Identity Guard v1-v3) first. Run and build the evaluation for Human Mode.

1. OFFLINE HIT RATE: from my last 200 real turns (local log), report the share answered by scenario, tool and LLM, with median latency per route. Target: at least 30 to 40 percent of everyday turns answered locally in under 150 ms after 2 weeks of teaching from the miss log.
2. PERCEIVED LATENCY: median time from the end of my speech to the first sound, with and without fillers, per response profile. Target: under 800 ms in Natural mode.
3. SAFETY CHECKS: run the crisis-guard test sets (positives and near-misses), and confirm that no scenario, joke or filler ever fires on a distress message. Report any failure as critical.
4. HONESTY LINT over the whole library: no claims of eating, sleeping, family, body or real feelings, no live facts, no medical, legal or financial claims, nothing about private people.
5. REPETITION AND VARIETY: over 200 simulated turns, report repeated variants, repeated openers and the most used replies.
6. HUMAN-FEEL SCRIPT: a scripted 30-turn conversation that includes greetings, small talk, Tanglish, a joke request, a tired moment, a math question, a deep question, a screen-free research question and a goodbye. Run it for real and print the replies, the route used, the filler played and the timings, so I can rate each turn Good / OK / Bad.
7. REPORT: one page with the metrics, the top 10 problems and the 10 highest-value scenarios to add next (from the miss log).
```

## Debug prompt (Human Mode)

```
Read CONTEXT.md. A Human Mode problem: <describe>.
Input: <what I said>. Expected: <expected>. Got: <reply, route, filler>.
First show which tier matched (or why none did), the confidence, the variant chosen and why, and which filler decision was made. Then give the most likely cause and 2 alternatives. Fix the root cause (a missing trigger, a threshold, a slot, a condition, a timing value), add the paraphrase to the scenario tests, and tell me how to verify. Never loosen the crisis guard or the honesty lint to make something pass.
```

## Suggested order

1. H0, then H1 (the engine). Test with the sample file only.
2. H2: generate three categories, review them in the Scenario Lab, then add the rest category by category over a few days.
3. H3 (offline skills), then H4 (thinking sounds). H4 gives the biggest "less robotic" feel for online replies.
4. H5 (logic upgrade), then H6 (speech polish).
5. H7 after two weeks of real use, using the miss log to decide the next 100 scenarios.

**Honest expectations:** a library of about 340 scenarios will cover many daily phrases, but real coverage grows from your miss log. A wrong canned reply is worse than a slightly slower right one, so the engine is strict by default and you can loosen it in settings.
