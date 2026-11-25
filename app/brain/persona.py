"""app/brain/persona.py

Persona loader, prompt builder, and personality engine.

Reads ``persona/nalla_master.md`` and builds the full system prompt
that is prepended to every LLM call. The file is hot-reloaded
automatically whenever its modification time changes, so you can edit
the persona without restarting the app.

Personality Engine features:
  • Plain sentences for all personality slider configurations.
  • Mood stress override: turns off sarcasm when stress words are detected.
  • Expressive audio tag gating: <chuckle>, <sigh>, <short pause> allowed
    only when enabled and TTS engine is Gemini; stripped otherwise.
  • Few-shot examples loader: prepends canonical turns from persona/few_shot.json.
  • Style rules positioned at the END of the system prompt for strongest adherence.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.brain.llm_provider import Message

logger = logging.getLogger("nalla.persona")

# Default location relative to the project root (BASE_DIR).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PERSONA_PATH = _PROJECT_ROOT / "persona" / "nalla_master.md"
_DEFAULT_FEW_SHOT_PATH = _PROJECT_ROOT / "persona" / "few_shot.json"

# Stress detection keywords
_STRESS_RE = re.compile(
    r"\b(tired|stuck|frustrated|sad|anxious|can'?t|cannot|exhausted|struggling|depressed|overwhelmed|mood illa|give up)\b",
    re.IGNORECASE,
)

# Expressive tags pattern
_TAGS_RE = re.compile(
    r"<(chuckle|sigh|short pause|pause|giggle|groan|laughter|cough|throat clearing|yawn)>",
    re.IGNORECASE,
)


def detect_stress_mood(text: str) -> bool:
    """Return True if user text contains words indicating stress, sadness, or exhaustion."""
    if not text:
        return False
    return bool(_STRESS_RE.search(text))


def strip_expressive_tags(text: str) -> str:
    """Remove expressive audio tags (like <chuckle>, <sigh>, <short pause>) from text."""
    if not text:
        return ""
    cleaned = _TAGS_RE.sub("", text)
    # Collapse multiple spaces left after tag removal
    cleaned = re.sub(r" +", " ", cleaned).strip()
    return cleaned


def format_personality_sentences(
    mode: str,
    sarcasm: int,
    warmth: int,
    tamil_mix: int,
    verbosity: str,
) -> str:
    """Format personality sliders into plain sentences for the system prompt."""
    mode_desc = f"Your operating mode is {mode}."

    if sarcasm >= 9:
        sarcasm_desc = f"Sarcasm level is {sarcasm} out of 10: sharp, ruthless roasts and biting wit; mock foolish ideas mercilessly with heavy dry sarcasm."
    elif sarcasm == 0:
        sarcasm_desc = f"Sarcasm level is 0 out of 10: absolutely zero sarcasm or teasing; speak purely gently, warmly, and helpfully."
    else:
        sarcasm_desc = f"Sarcasm level is {sarcasm} out of 10: dry, clever wit; roast ideas, never people."

    if warmth >= 9:
        warmth_desc = f"Warmth level is {warmth} out of 10: exceptionally gentle, comforting, caring, reassuring, and soothing."
    elif warmth <= 2:
        warmth_desc = f"Warmth level is {warmth} out of 10: cold, blunt, unsentimental, direct."
    else:
        warmth_desc = f"Warmth level is {warmth} out of 10: genuinely care about Ugesh and look out for him."

    if tamil_mix == 0:
        tamil_desc = "Tamil mix is 0 percent: speak standard English with zero Tamil words."
    elif tamil_mix >= 60:
        tamil_desc = f"Tamil mix is {tamil_mix} percent: heavy Tanglish slang (sollunga, paathukalam, vendam, seri, aiyo) woven naturally into responses."
    else:
        tamil_desc = f"Tamil mix is {tamil_mix} percent: blend casual Tanglish naturally into conversation."

    verb_desc = f"Verbosity is {verbosity}: reply in 1 to 2 punchy, conversational sentences by default."
    return f"{mode_desc}\n{sarcasm_desc}\n{warmth_desc}\n{tamil_desc}\n{verb_desc}"


# ---------------------------------------------------------------------------
# Few-shot turns loader & editor helpers
# ---------------------------------------------------------------------------

_few_shot_cache: list[Message] = []
_few_shot_mtime: float = 0.0


def load_few_shot_messages(path: Path | None = None) -> list[Message]:
    """Load canonical user/assistant few-shot pairs from persona/few_shot.json.

    Cached in-memory and reloaded on modification.
    """
    global _few_shot_cache, _few_shot_mtime
    p = path or _DEFAULT_FEW_SHOT_PATH
    if not p.exists():
        return []
    try:
        mtime = p.stat().st_mtime
        if mtime != _few_shot_mtime:
            data = json.loads(p.read_text(encoding="utf-8"))
            turns: list[Message] = []
            for item in data:
                turns.append({"role": "user", "content": item["user"]})
                turns.append({"role": "assistant", "content": item["model"]})
            _few_shot_cache = turns
            _few_shot_mtime = mtime
            logger.debug("Loaded %d few-shot turns from %s", len(turns), p)
        return list(_few_shot_cache)
    except Exception as exc:
        logger.warning("Could not load few-shot examples from %s: %s", p, exc)
        return []


def get_few_shot_examples(path: Path | None = None) -> list[dict[str, Any]]:
    """Return list of user/model few-shot example dicts."""
    p = path or _DEFAULT_FEW_SHOT_PATH
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read few shot examples: %s", exc)
        return []


def save_few_shot_examples(examples: list[dict[str, str]], path: Path | None = None) -> bool:
    """Save 8 few-shot examples to disk and refresh the in-memory cache."""
    global _few_shot_cache, _few_shot_mtime
    p = path or _DEFAULT_FEW_SHOT_PATH
    try:
        p.write_text(json.dumps(examples, indent=2, ensure_ascii=False), encoding="utf-8")
        _few_shot_mtime = p.stat().st_mtime
        turns: list[Message] = []
        for item in examples:
            turns.append({"role": "user", "content": item.get("user", "")})
            turns.append({"role": "assistant", "content": item.get("model", "")})
        _few_shot_cache = turns
        return True
    except Exception as exc:
        logger.error("Failed to save few-shot examples: %s", exc)
        return False


def revert_few_shot_examples(path: Path | None = None) -> list[dict[str, Any]]:
    """Revert few-shot examples to the canonical backup file."""
    p = path or _DEFAULT_FEW_SHOT_PATH
    default_p = p.parent / "few_shot.default.json"
    if default_p.exists():
        content = default_p.read_text(encoding="utf-8")
        p.write_text(content, encoding="utf-8")
        load_few_shot_messages(p)
    return get_few_shot_examples(p)


# ---------------------------------------------------------------------------
# Persona Class
# ---------------------------------------------------------------------------

class Persona:
    """Loads and hot-reloads the master persona markdown file.

    Parameters
    ----------
    path: Path to the persona markdown file. Defaults to
          ``persona/nalla_master.md`` relative to the project root.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = Path(path) if path else _DEFAULT_PERSONA_PATH
        self._cached_text: str = ""
        self._cached_mtime: float = 0.0
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_system_prompt(
        self,
        tool_summary: str = "(no tools connected)",
        memory_context: str = "",
        user_text: str = "",
        mode: str | None = None,
        sarcasm: int | None = None,
        warmth: int | None = None,
        tamil_mix: int | None = None,
        verbosity: str | None = None,
        context_block: str = "",
    ) -> str:
        """Return the full system prompt for one LLM call.

        Hot-reloads the persona file if it has been modified on disk.

        Parameters
        ----------
        tool_summary: One-line-per-tool description from ToolRegistry.summary().
        memory_context: Top retrieved relevant memories for the current turn.
        user_text: Current user input for per-turn mood and stress detection.
        mode, sarcasm, warmth, tamil_mix, verbosity: Optional overrides for testing/preview.
        """
        self._reload_if_changed()
        now = datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")
        from app import config

        eff_mode = mode or config.PERSONALITY_MODE
        eff_sarcasm = sarcasm if sarcasm is not None else config.PERSONALITY_SARCASM
        eff_warmth = warmth if warmth is not None else config.PERSONALITY_WARMTH
        eff_tamil = tamil_mix if tamil_mix is not None else config.PERSONALITY_TAMIL_MIX
        eff_verb = verbosity or config.PERSONALITY_VERBOSITY

        personality_line = (
            f"Sarcasm: {eff_sarcasm}/10. Warmth: {eff_warmth}/10. "
            f"Tamil mix: {eff_tamil}%. Replies: {eff_verb}."
        )
        personality_block = format_personality_sentences(
            mode=eff_mode,
            sarcasm=eff_sarcasm,
            warmth=eff_warmth,
            tamil_mix=eff_tamil,
            verbosity=eff_verb,
        )

        # Expressive audio tags directive
        if config.EXPRESSIVE_TAGS and config.TTS_ENGINE == "gemini":
            tags_rule = (
                "You may include at most one expressive tag per response: "
                "<chuckle>, <sigh>, or <short pause>."
            )
        else:
            tags_rule = "Do not include XML or audio tags like <chuckle>, <sigh>, or <short pause> in your text."

        # Mood override for stressed / exhausted user
        is_stressed = detect_stress_mood(user_text)
        mood_override = ""
        if is_stressed:
            mood_override = (
                "\n[MOOD OVERRIDE: The user seems stressed: no sarcasm, be warm and brief.]\n"
            )

        # Assemble prompt with core identity and style rules at the END
        prompt = (
            f"{self._cached_text}\n\n"
            f"---\n"
            f"{personality_line}\n\n"
            f"Current date/time: {now}\n\n"
            f"Currently connected tools:\n{tool_summary}\n"
        )
        if context_block.strip():
            prompt += f"\n{context_block.strip()}\n"

        if memory_context.strip() and not context_block.strip():
            prompt += (
                f"\n## Known about the user (may be outdated)\n"
                f"{memory_context.strip()}\n\n"
                f"Rule: if a memory or fact is not in this list, say you don't remember it. Never fabricate memories.\n"
            )

        # Tamil Text Mode & Kongu Touch rules (Prompt E)
        from app.voice.tamil_pipeline import get_kongu_flavor_instructions
        tamil_mode = getattr(config, "TAMIL_TEXT_MODE", "tanglish_display").lower()
        if tamil_mode == "tamil_script":
            tamil_rule = (
                "TAMIL TEXT MODE: Tamil script. Write Tamil words using Tamil script (e.g. சொல்லுங்க, பாத்துக்கலாம்) "
                "and technical terms in English. Always use everyday spoken Tamil (colloquial), never bookish written Tamil."
            )
        elif tamil_mode == "english_only":
            tamil_rule = "TAMIL TEXT MODE: English only. Respond exclusively in English."
        else:
            tamil_rule = (
                "TAMIL TEXT MODE: Tanglish display. Write colloquial Tamil using English letters "
                "(e.g. sollunga, paathukalam, sema idea). Always use everyday spoken Tamil (colloquial), never bookish written Tamil."
            )

        kongu_touch_val = getattr(config, "KONGU_TOUCH", 2)
        kongu_rule = get_kongu_flavor_instructions(kongu_touch_val)

        # WRITE FOR THE EAR rule (Natural Spoken Speech Delivery)
        ear_rule = (
            "WRITE FOR THE EAR (Spoken Speech Delivery):\n"
            "• Use natural contractions always (I'm, don't, you're, it's, we'll, won't, can't).\n"
            "• Keep every sentence short and breathable (under 18 words). Never speak in run-on sentences.\n"
            "• Zero markdown: NO bullet points, NO numbered lists, NO asterisks, NO bolding, NO backticks or code blocks.\n"
            "• Write numbers and times phonetically as spoken (e.g. 'ten thirty a m', 'twenty-five degrees', 'three hundred thousand').\n"
            "• Use commas and dashes for natural conversational pauses and pacing.\n"
            "• Include occasional short natural reactions at start of thoughts ('Hmm', 'Okay', 'Ha', 'Aaha', 'Seri').\n"
            "• Maintain Nalla's signature identity: cool, sarcastic, caring, friendly, never robotic."
        )

        # Humanisation Engine directives (Response profile, Anti-repetition, Tanglish Vibe)
        from app.brain.humanisation import (
            classify_response_profile,
            detect_user_mood,
            get_anti_repetition_directive,
            get_tanglish_sample_instructions,
        )
        profile_name, profile_cfg = classify_response_profile(user_text)
        profile_hint = profile_cfg.get("hint", "")
        anti_rep_line = get_anti_repetition_directive()
        tanglish_vibe_line = get_tanglish_sample_instructions(eff_tamil)

        # Style & Tone rules placed at the very END for maximum attention
        prompt += (
            f"\n---\n"
            f"## Current Personality Configuration\n"
            f"{personality_block}\n"
            f"{tags_rule}\n"
            f"{tamil_rule}\n"
            f"{kongu_rule}\n"
            f"{tanglish_vibe_line}\n"
            f"{ear_rule}\n"
            f"{mood_override}\n"
            f"TURN GUIDANCE:\n"
            f"• {profile_hint}\n"
            f"• {anti_rep_line}\n"
            f"• Answer size matches question size: quick question = one short sentence; casual = 1-2 sentences. "
            f"For detailed explanations, start with a 1-2 sentence spoken headline, then use '---' on its own line before screen details."
            f"\nStyle enforcement: Speak punchily in NALLA's signature Coimbatore tone. "
            f"Use everyday spoken Tamil (colloquial), not bookish or formal written Tamil. "
            f"Give decisive recommendations, never generic lists."
        )

        return prompt

    def build_prompt_preview(
        self,
        mode: str | None = None,
        sarcasm: int | None = None,
        warmth: int | None = None,
        tamil_mix: int | None = None,
        verbosity: str | None = None,
        user_text: str = "",
        sample_tool_summary: str = "living_room_light (relay): Living room main ceiling lamp\nweather_sensor (sensor): DHT22 temp and humidity",
    ) -> dict[str, Any]:
        """Return a structured breakdown of the live system prompt."""
        self._reload_if_changed()
        full_prompt = self.build_system_prompt(
            tool_summary=sample_tool_summary,
            memory_context="Ugesh prefers dark mode in all UI apps\nWorking on autonomous agent architecture",
            user_text=user_text,
            mode=mode,
            sarcasm=sarcasm,
            warmth=warmth,
            tamil_mix=tamil_mix,
            verbosity=verbosity,
        )
        from app import config

        eff_mode = mode or config.PERSONALITY_MODE
        eff_sarcasm = sarcasm if sarcasm is not None else config.PERSONALITY_SARCASM
        eff_warmth = warmth if warmth is not None else config.PERSONALITY_WARMTH
        eff_tamil = tamil_mix if tamil_mix is not None else config.PERSONALITY_TAMIL_MIX
        eff_verb = verbosity or config.PERSONALITY_VERBOSITY

        personality_line = (
            f"Sarcasm: {eff_sarcasm}/10. Warmth: {eff_warmth}/10. "
            f"Tamil mix: {eff_tamil}%. Replies: {eff_verb}."
        )
        personality_block = format_personality_sentences(
            mode=eff_mode,
            sarcasm=eff_sarcasm,
            warmth=eff_warmth,
            tamil_mix=eff_tamil,
            verbosity=eff_verb,
        )
        is_stressed = detect_stress_mood(user_text)
        mood_override = (
            "[MOOD OVERRIDE: The user seems stressed: no sarcasm, be warm and brief.]"
            if is_stressed
            else "(No mood override active — user tone is neutral)"
        )
        return {
            "full_prompt": full_prompt,
            "personality_block": personality_block,
            "personality_line": personality_line,
            "mood_override": mood_override,
            "stress_detected": is_stressed,
            "tags_rule": (
                "Do not include XML or audio tags"
                if not (config.EXPRESSIVE_TAGS and config.TTS_ENGINE == "gemini")
                else "Expressive tags allowed"
            ),
        }

    @property
    def path(self) -> Path:
        """Absolute path to the persona file being watched."""
        return self._path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load the persona file from disk and update the cache."""
        if not self._path.exists():
            logger.warning(
                "Persona file not found at %s — using empty persona.", self._path
            )
            self._cached_text = ""
            self._cached_mtime = 0.0
            return
        try:
            self._cached_text = self._path.read_text(encoding="utf-8").strip()
            self._cached_mtime = self._path.stat().st_mtime
            logger.debug("Persona loaded from %s", self._path)
        except OSError as exc:
            logger.error("Failed to read persona file: %s", exc)

    def _reload_if_changed(self) -> None:
        """Check the file's mtime and reload if it has changed."""
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if mtime != self._cached_mtime:
            logger.info("Persona file changed — hot-reloading %s", self._path)
            self._load()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

persona = Persona()
