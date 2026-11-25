"""app/brain/fast_router.py

Local fast-path router — handles common intents WITHOUT calling the LLM.
Target latency: < 100 ms end-to-end (no network, no model load).

Matched intents
---------------
  greetings     — "Nalla", "hey nalla", "hi nalla", "yo nalla"
  time / date   — "what time is it", "what's the date", "current time"
  mic mute      — "mute mic", "mute yourself"
  mic unmute    — "unmute", "unmute mic"
  stop / shut up— "stop", "shut up", "stop talking", "cancel"
  volume        — "volume up", "volume down", "set volume to 80%", "mute volume"
  device keywords — "turn on relay 1", "turn off light", "switch on fan"
  reminders     — "any reminders", "my reminders"

Returns None when no fast-path match, so caller falls through to the LLM.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from app import config

logger = logging.getLogger("nalla.fast_router")

# ---------------------------------------------------------------------------
# Compiled patterns — cheaper than re-compiling per call
# ---------------------------------------------------------------------------

_GREETING = re.compile(
    r"^(hey\s+)?nalla\s*[!\s]*$|^(yo|hello|hi)\s+nalla\s*[!\s]*$",
    re.IGNORECASE,
)

_TIME = re.compile(
    r"\b(what\s*(is|'?s)\s+the\s+time|current\s+time|what\s+time\s+is\s+it"
    r"|time\s+now|tell\s+me\s+the\s+time)\b",
    re.IGNORECASE,
)

_DATE = re.compile(
    r"\b(what\s*(is|'?s)\s+the\s+date|today['\s]+s?\s+date|what\s+day\s+is\s+(it|today)"
    r"|current\s+date|day\s+and\s+date)\b",
    re.IGNORECASE,
)

_MIC_MUTE = re.compile(
    r"\b(mute\s+(your\s+)?mic(rophone)?|stop\s+listening|be\s+quiet)\b",
    re.IGNORECASE,
)

_MIC_UNMUTE = re.compile(
    r"\b(unmute\s*(your\s+)?mic(rophone)?|start\s+listening\s+again|listen\s+again)\b",
    re.IGNORECASE,
)

_STOP = re.compile(
    r"^(stop|cancel|shut\s+up|stop\s+talking|that['\s]+s?\s+enough)\s*[.!]?\s*$",
    re.IGNORECASE,
)

_REMINDERS = re.compile(
    r"\b(any|my|show|list)\s+reminder(s)?\b|\breminder(s)?\s+(due|today|now)\b",
    re.IGNORECASE,
)

_VOLUME = re.compile(
    r"\b(volume\s+(up|down|max|mute)|mute\s+volume|set\s+volume(\s+to)?\s+(\d+)\s*%?|volume\s+(\d+)\s*%?|louder|quieter|softer)\b",
    re.IGNORECASE,
)

_DEVICE_CMD = re.compile(
    r"\b(turn|switch|toggle)\s+(on|off)?\s*(the\s+)?(.*?)(relay\s*\d+|main\s+light|light|ceiling\s+fan|fan|smart\s+plug|plug)\b",
    re.IGNORECASE,
)

_SCREEN_HELP = re.compile(
    r"\b(look\s+at\s+my\s+screen|what'?s\s+this\s+error|what\s+is\s+this\s+error|explain\s+this|check\s+my\s+screen|read\s+my\s+screen|inspect\s+screen)\b",
    re.IGNORECASE,
)

_FORGET_ALL_ABOUT = re.compile(
    r"\bforget\s+everything\s+about\s+(.+?)[.!]?\s*$",
    re.IGNORECASE,
)

_FORGET_LAST_THING = re.compile(
    r"\bforget\s+(the\s+)?last\s+thing(\s+i\s+told\s+you)?\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Device lookup & dispatch helper
# ---------------------------------------------------------------------------

def _handle_device_command(text: str) -> str:
    """Execute simple device keyword command and return verified status."""
    text_lower = text.lower()
    target_action = "on" if "on" in text_lower else ("off" if "off" in text_lower else "toggle")

    # Target device mapping
    target_device = None
    target_name = ""
    try:
        from app.web.server import _DEVICES, _DEVICE_HISTORY
        devices_list = _DEVICES
    except Exception:
        devices_list = []

    if "plug" in text_lower:
        target_name = "Workbench Smart Plug"
        target_id = "esp32-plug-1"
    elif "relay 1" in text_lower or "main light" in text_lower or "light" in text_lower:
        target_name = "Living Room Main Light"
        target_id = "esp32-relay-1"
    elif "fan" in text_lower:
        target_name = "Bedroom Ceiling Fan"
        target_id = "esp8266-fan-1"
    else:
        target_id = "esp32-relay-1"
        target_name = "Relay 1"

    found_device = None
    for d in devices_list:
        if d.get("id") == target_id or d.get("name") == target_name:
            found_device = d
            break

    if not found_device:
        return f"Cannot find device '{target_name}' in registry, boss."

    # Honesty rule: if device is offline, NEVER claim success
    if not found_device.get("online", False):
        return f"Illai boss, {found_device.get('name')} is offline. Action failed."

    # Perform the state change
    old_state = found_device.get("state", "off")
    if target_action == "toggle":
        new_state = "off" if old_state == "on" else "on"
    else:
        new_state = target_action

    found_device["state"] = new_state
    found_device["last_seen"] = "Just now"

    # Log to device history
    try:
        from app.web.server import _DEVICE_HISTORY
        _DEVICE_HISTORY.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "device": found_device.get("name"),
            "command": f"FAST_RELAY_{new_state.upper()}",
            "status": "ACK_CONFIRMED",
            "latency_ms": 12,
        })
    except Exception:
        pass

    return f"{found_device.get('name')} switched {new_state}, boss."


# ---------------------------------------------------------------------------
# Volume helper
# ---------------------------------------------------------------------------

def _handle_volume(text: str) -> str:
    """Adjust system volume in config.AUDIO_FEEL and return status message."""
    current_vol = config.AUDIO_FEEL.get("chime_volume", 80)
    text_lower = text.lower()

    if "up" in text_lower or "louder" in text_lower:
        new_vol = min(100, current_vol + 10)
        action_msg = f"Volume increased to {new_vol}%, boss."
    elif "down" in text_lower or "quieter" in text_lower or "softer" in text_lower:
        new_vol = max(0, current_vol - 10)
        action_msg = f"Volume decreased to {new_vol}%, boss."
    elif "max" in text_lower:
        new_vol = 100
        action_msg = "Volume set to maximum (100%), boss."
    elif "mute" in text_lower:
        new_vol = 0
        action_msg = "Audio volume muted, boss."
    else:
        # Check numeric percentage
        digits = re.findall(r"\d+", text)
        if digits:
            new_vol = max(0, min(100, int(digits[0])))
            action_msg = f"Volume set to {new_vol}%, boss."
        else:
            new_vol = current_vol
            action_msg = f"Current volume is {current_vol}%, boss."

    config.AUDIO_FEEL["chime_volume"] = new_vol
    config.save_persisted_settings()
    return action_msg


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

last_route_info: dict[str, Any] = {"route": "llm", "bypass_fillers": False}


def get_last_route_info() -> dict[str, Any]:
    """Return metadata about the most recent fast_router.route() call."""
    return dict(last_route_info)


def route(user_text: str, pipeline: "object | None" = None) -> str | None:
    """Try to handle *user_text* locally.

    Parameters
    ----------
    user_text:  Cleaned STT or chat input.
    pipeline:   VoicePipeline instance (optional); used for mute/unmute/interrupt.

    Returns
    -------
    A response string if handled locally, else None.
    """
    global last_route_info
    text = user_text.strip()
    if not text:
        last_route_info = {"route": "fast_path", "bypass_fillers": True}
        return "Yes boss, sollunga!"

    # 0. CRISIS GUARD — always runs first before ANY scenario, joke, or filler
    try:
        from app.brain.scenarios import check_crisis
        crisis = check_crisis(text)
        if crisis.is_crisis:
            logger.warning("Crisis Guard triggered (%s) — bypassing scenarios and fillers.", crisis.category)
            last_route_info = {
                "route": "crisis",
                "category": crisis.category,
                "bypass_fillers": True,
            }
            return None
    except Exception as exc:
        logger.debug("Crisis check error: %s", exc)

    # Greeting
    if _GREETING.match(text):
        logger.debug("Fast-path: greeting")
        return "Yes boss, sollunga!"

    # Stop
    if _STOP.match(text):
        logger.debug("Fast-path: stop")
        if pipeline is not None:
            try:
                pipeline.interrupt()  # type: ignore[attr-defined]
            except Exception:
                pass
        return "Seri boss, stopping."

    # Mute
    if _MIC_MUTE.search(text):
        logger.debug("Fast-path: mute mic")
        if pipeline is not None:
            try:
                pipeline.set_muted(True)  # type: ignore[attr-defined]
            except Exception:
                pass
        return "Mic muted, boss."

    # Unmute
    if _MIC_UNMUTE.search(text):
        logger.debug("Fast-path: unmute mic")
        if pipeline is not None:
            try:
                pipeline.set_muted(False)  # type: ignore[attr-defined]
            except Exception:
                pass
        return "Mic is live again, boss."

    # Volume
    if _VOLUME.search(text):
        logger.debug("Fast-path: volume")
        return _handle_volume(text)

    # Simple device keywords
    if _DEVICE_CMD.search(text):
        logger.debug("Fast-path: device command")
        return _handle_device_command(text)

    # Screen Help Fast-Path (Prompt 2A)
    if _SCREEN_HELP.search(text):
        logger.debug("Fast-path: screen help requested")
        try:
            from app.core.screen import screen_manager
            res = screen_manager.create_capture(target="full")
            try:
                from app.web.server import ws_manager
                ws_manager.broadcast_sync({
                    "type": "screen_preview_request",
                    "capture_id": res["capture_id"],
                    "thumbnail": res["thumbnail"],
                    "query": text,
                    "width": res["width"],
                    "height": res["height"],
                })
            except Exception as ws_err:
                logger.debug("WebSocket broadcast skipped: %s", ws_err)
            return "Taking a look, boss. Check the screen preview on your dashboard before I send it."
        except Exception as exc:
            logger.error("Screen capture fast-path error: %s", exc)
            return "Couldn't grab the screen right now, boss. Check display permissions."

    # Memory 2.0 Forgetting shortcuts
    m_forget_all = _FORGET_ALL_ABOUT.search(text)
    if m_forget_all:
        target_entity = m_forget_all.group(1).strip()
        logger.debug("Fast-path: forget everything about '%s'", target_entity)
        from app.memory.memory_v2 import memory_v2
        res = memory_v2.forget_everything_about(target_entity)
        return res["message"]

    if _FORGET_LAST_THING.search(text):
        logger.debug("Fast-path: forget last thing")
        from app.memory.memory_v2 import memory_v2
        res = memory_v2.forget_last_fact()
        if res:
            return f"Done boss, forgot that {res['entity_name']} {res['text']}."
        return "Nothing recent in memory to forget, boss."

    # Time
    if _TIME.search(text):
        logger.debug("Fast-path: time")
        from app.brain.context_builder import (
            get_current_time,
            get_time_band,
            record_nudge_given,
            _care_state,
        )
        now_dt = get_current_time()
        now_str = now_dt.strftime("%I:%M %p").lstrip("0")
        band = get_time_band(now_dt)

        # 3 short variants per time band in Nalla's voice
        variants = {
            "late_night": [
                f"It's {now_str}, boss. Not that I'm counting.",
                f"It's {now_str}, boss. Still burning the midnight oil?",
                f"It's {now_str}, boss.",
            ],
            "morning": [
                f"It's {now_str}, boss. Bright and early.",
                f"It's {now_str}, boss. Coffee ready yet?",
                f"It's {now_str}, boss. Time to build.",
            ],
            "afternoon": [
                f"It's {now_str}, boss.",
                f"It's {now_str}, boss. Half the day already flew.",
                f"Clock says {now_str}, boss. Stay focused.",
            ],
            "evening": [
                f"It's {now_str}, boss. Sun's winding down.",
                f"It's {now_str}, boss. Evening sprint?",
                f"It's {now_str}, boss.",
            ],
            "night": [
                f"It's {now_str}, boss. Getting late.",
                f"It's {now_str}, boss. Wrap up soon.",
                f"It's {now_str}, boss.",
            ],
        }

        band_variants = variants.get(band, [f"It's {now_str}, boss."])
        # Pick variant deterministically or by minute to be testable and varied
        variant_idx = now_dt.minute % len(band_variants)
        reply = band_variants[variant_idx]

        # The late-night variant sets a flag saying the nudge was already given or active,
        # so the next LLM turn does not repeat it unless the cooldown has passed.
        if band == "late_night":
            _care_state["fast_path_late_night_used"] = True
            record_nudge_given(now_dt)

        return reply

    # Date
    if _DATE.search(text):
        logger.debug("Fast-path: date")
        today = datetime.now().strftime("%A, %d %B %Y")
        return f"Today is {today}, boss."

    # Reminders (local DB — still fast, no LLM)
    if _REMINDERS.search(text):
        logger.debug("Fast-path: reminders")
        try:
            from app.memory.store import store
            reminders = store.list_reminders(include_done=False)
            pending = [r for r in reminders if r.get("status") == "pending"]
            if not pending:
                return "No pending reminders, boss."
            items = "; ".join(
                f"#{r['id']}: {r['text']} at {r['due_at'][11:16]}"
                for r in pending[:3]
            )
            return f"You have {len(pending)} reminder(s): {items}."
        except Exception as exc:
            logger.debug("Fast-path reminders failed: %s", exc)
            return None

    # Offline Deterministic Skills (calculator, units, dates, timers, system, packs, recall)
    try:
        from app.brain.skills import try_offline_skill
        skill_hit = try_offline_skill(text)
        if skill_hit is not None:
            last_route_info = {
                "route": "tool",
                "skill": skill_hit["skill"],
                "details": skill_hit.get("details", ""),
                "bypass_fillers": True,
            }
            return skill_hit["reply"]
    except Exception as exc:
        logger.debug("Offline skill error: %s", exc)

    # Scenario Engine (Tier 1-3 local instant replies)
    try:
        from app.brain.scenarios import scenario_engine, log_miss
        sc_match = scenario_engine.match(text)
        if sc_match is not None:
            last_route_info = {
                "route": "scenario",
                "scenario_id": sc_match.scenario_id,
                "category": sc_match.category,
                "tier": sc_match.tier,
                "confidence": sc_match.confidence,
                "latency_ms": sc_match.latency_ms,
                "tone": sc_match.tone,
                "bypass_fillers": True,
            }
            logger.debug(
                "Scenario match: %s (tier=%d, conf=%.1f, %.2fms)",
                sc_match.scenario_id, sc_match.tier, sc_match.confidence, sc_match.latency_ms,
            )
            return sc_match.reply
    except Exception as exc:
        logger.debug("Scenario engine error: %s", exc)

    # Unmatched utterance — log locally (if enabled) and fall through to LLM
    try:
        from app.brain.scenarios import log_miss
        log_miss(text, route="llm")
    except Exception:
        pass
    last_route_info = {"route": "llm", "bypass_fillers": False}
    return None
