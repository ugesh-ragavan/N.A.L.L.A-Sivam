"""app/core/briefings.py

Deterministic data gathering and Nalla Persona v3 speech generators for:
  • Morning Briefing (weather + top 3 tasks + reminders)
  • Welcome Back (after 90+ minutes away)
  • Smart Check-ins (hydration, stretching, stuck on task)
  • Evening Wrap-up (tasks done today + tomorrow's priorities)

Enforces Identity Guard: weather is cached for 30 mins; if weather fails, skip that line
and never fabricate it. Spoken 15-25 seconds at most, with details following `---`.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

from app import config
from app.brain.context_builder import get_current_time
from app.brain.humanisation import record_opener_and_closer
from app.memory.store import store as default_store

logger = logging.getLogger("nalla.briefings")

# In-memory 30-minute weather cache
_WEATHER_CACHE: dict[str, Any] = {"data": None, "timestamp": 0.0}
_WEATHER_CACHE_TTL = 1800.0  # 30 minutes


def get_local_weather(
    lat: float | None = None,
    lon: float | None = None,
    simulated_now: float | None = None,
) -> dict[str, Any] | None:
    """Fetch current weather from Open-Meteo (no API key required).

    Caches for 30 minutes. Returns None on network or parsing failure.
    """
    global _WEATHER_CACHE
    now_ts = simulated_now or time.time()
    if _WEATHER_CACHE["data"] and (now_ts - _WEATHER_CACHE["timestamp"]) < _WEATHER_CACHE_TTL:
        return _WEATHER_CACHE["data"]

    latitude = lat if lat is not None else getattr(config, "WEATHER_LATITUDE", 11.0168)
    longitude = lon if lon is not None else getattr(config, "WEATHER_LONGITUDE", 76.9558)
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={latitude}&longitude={longitude}&current=temperature_2m,relative_humidity_2m,weather_code"
    )

    try:
        import urllib.request
        import json

        req = urllib.request.Request(url, headers={"User-Agent": "NallaSivam/1.0"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            current = data.get("current", {})
            temp = current.get("temperature_2m")
            code = current.get("weather_code", 0)
            humidity = current.get("relative_humidity_2m")

            desc = "Clear sky"
            if code in (1, 2, 3):
                desc = "Partly cloudy"
            elif code in (45, 48):
                desc = "Foggy"
            elif code in (51, 53, 55, 61, 63, 65, 80, 81, 82):
                desc = "Rainy"
            elif code in (95, 96, 99):
                desc = "Thunderstorm"

            weather_obj = {
                "temp_c": temp,
                "description": desc,
                "humidity": humidity,
            }
            _WEATHER_CACHE = {"data": weather_obj, "timestamp": now_ts}
            return weather_obj
    except Exception as exc:
        logger.debug("Weather fetch skipped or failed: %s", exc)
        return None


def generate_morning_briefing(payload: dict[str, Any] | None = None, simulated_now: datetime | None = None) -> str:
    """Generate Morning Briefing (date + weather + top 3 tasks + reminders)."""
    now = simulated_now or get_current_time()
    date_str = now.strftime("%A, %B %d")

    # Fetch weather (safe, cached)
    weather = get_local_weather()
    weather_line = ""
    if weather and weather.get("temp_c") is not None:
        weather_line = f"It's {weather['temp_c']}°C and {weather['description'].lower()} outside."

    # Fetch top 3 open tasks
    open_tasks = default_store.list_tasks(status="open")[:3]
    reminders = default_store.list_reminders(include_done=False)

    if open_tasks:
        task_names = [f"#{t['id']} {t['title']}" for t in open_tasks]
        tasks_spoken = f"Top priority today: {open_tasks[0]['title']}."
    else:
        tasks_spoken = "Todo list is completely clear right now."

    rem_count = len(reminders)
    rem_spoken = f" You've got {rem_count} reminder{'s' if rem_count != 1 else ''} lined up." if rem_count else ""

    variants = [
        f"Kaalai vanakkam boss! Today is {date_str}. {weather_line} {tasks_spoken}{rem_spoken}",
        f"Good morning, boss! Happy {now.strftime('%A')}. {weather_line} {tasks_spoken}{rem_spoken}",
        f"Vanthanam boss! Fresh start for {date_str}. {weather_line} {tasks_spoken}{rem_spoken}",
        f"Morning, boss. Ready to build? {weather_line} {tasks_spoken}{rem_spoken}",
    ]
    spoken = random.choice(variants).strip()

    # Screen details
    screen_lines = [f"### Morning Briefing — {date_str}"]
    if weather:
        screen_lines.append(f"- **Weather:** {weather['temp_c']}°C, {weather['description']} (Humidity: {weather.get('humidity')}%)")
    if open_tasks:
        screen_lines.append("- **Top Tasks:**")
        for t in open_tasks:
            screen_lines.append(f"  • P{t['priority']} #{t['id']}: {t['title']}")
    else:
        screen_lines.append("- **Tasks:** No open tasks! Enjoy the clear board.")

    if reminders:
        screen_lines.append("- **Reminders:**")
        for r in reminders[:3]:
            screen_lines.append(f"  • Due {r['due_at']}: {r['text']}")

    full_output = f"{spoken}\n---\n" + "\n".join(screen_lines)
    record_opener_and_closer(spoken)
    return full_output


def generate_welcome_back(payload: dict[str, Any] | None = None, simulated_now: datetime | None = None) -> str:
    """Generate Welcome Back greeting after being away (>90 mins)."""
    now = simulated_now or get_current_time()
    hr = now.hour
    if 5 <= hr < 12:
        tod = "morning"
    elif 12 <= hr < 17:
        tod = "afternoon"
    elif 17 <= hr < 22:
        tod = "evening"
    else:
        tod = "night"

    next_up = default_store.next_task()
    if next_up:
        detail = f"Next up on your board is '{next_up['title']}'. Shall we pick that up?"
    else:
        detail = "All clear on the board. Ready when you are, boss."

    variants = [
        f"Welcome back, boss. Good {tod}! {detail}",
        f"Machan, you're back. {detail}",
        f"Back in action, boss? {detail}",
        f"Hey, welcome back! Hope you took a breather. {detail}",
    ]
    spoken = random.choice(variants)
    record_opener_and_closer(spoken)
    return spoken


def generate_smart_check_in(payload: dict[str, Any] | None = None, simulated_now: datetime | None = None) -> str:
    """Generate a periodic stretch, hydration, or stuck-task check-in."""
    open_tasks = default_store.list_tasks(status="open")
    stuck_task = None
    now = simulated_now or get_current_time()

    # Check if a task has been open for > 2 hours without completions
    stuck_hours = getattr(config, "STUCK_TASK_HOURS", 2)
    for t in open_tasks:
        try:
            created = datetime.fromisoformat(t["created_at"])
            if (now - created).total_seconds() > (stuck_hours * 3600):
                stuck_task = t
                break
        except Exception:
            continue

    if stuck_task:
        variants = [
            f"Boss, task #{stuck_task['id']} ('{stuck_task['title']}') has been open for a while. Stuck anywhere? Want me to help break it into smaller steps?",
            f"Machan, still grinding on '{stuck_task['title']}'? Take a quick breather or tell me if you want another angle on it.",
        ]
        return random.choice(variants)

    # General wellness check-in
    variants = [
        "Boss, quick reminder: drink some water and give your eyes a twenty-second break.",
        "Machan, roll your shoulders back and hydrate. You've been in the zone for a good stretch.",
        "Hey boss, stand up for a minute and stretch. Code runs better when your back doesn't ache.",
        "Quick pause, boss: grab a sip of water. Don't let dehydration slow down that brain.",
    ]
    spoken = random.choice(variants)
    record_opener_and_closer(spoken)
    return spoken


def generate_evening_wrap_up(payload: dict[str, Any] | None = None, simulated_now: datetime | None = None) -> str:
    """Generate Evening Wrap-up (tasks done today, left open, and tomorrow's top 3)."""
    now = simulated_now or get_current_time()
    today_iso = now.date().isoformat()

    # Tasks done today vs open
    all_done = default_store.list_tasks(status="done")
    done_today = [
        t for t in all_done
        if t.get("completed_at") and t["completed_at"].startswith(today_iso)
    ]
    open_tasks = default_store.list_tasks(status="open")

    done_count = len(done_today)
    open_count = len(open_tasks)

    if done_count > 0:
        summary_spoken = f"Great work today, boss! You completed {done_count} task{'s' if done_count != 1 else ''}."
    else:
        summary_spoken = f"That's a wrap for today, boss. Still {open_count} open task{'s' if open_count != 1 else ''} on deck."

    if open_count > 0:
        top_tomorrow = f" Tomorrow we can tackle '{open_tasks[0]['title']}'. Close up and rest well."
    else:
        top_tomorrow = " Zero tasks left open — total victory. Time to power down."

    variants = [
        f"{summary_spoken}{top_tomorrow}",
        f"Evening wrap-up time, boss. {summary_spoken}{top_tomorrow}",
        f"End of the shift! {summary_spoken}{top_tomorrow}",
        f"Machan, good day of work. {summary_spoken}{top_tomorrow}",
    ]
    spoken = random.choice(variants)

    # Screen breakdown
    screen_lines = [f"### Evening Wrap-Up — {now.strftime('%A, %B %d')}"]
    screen_lines.append(f"- **Completed Today ({done_count}):**")
    if done_today:
        for t in done_today:
            screen_lines.append(f"  ✓ #{t['id']}: {t['title']}")
    else:
        screen_lines.append("  (No tasks marked done today)")

    screen_lines.append(f"- **Remaining Open ({open_count}):**")
    for t in open_tasks[:3]:
        screen_lines.append(f"  • P{t['priority']} #{t['id']}: {t['title']}")

    full_output = f"{spoken}\n---\n" + "\n".join(screen_lines)
    record_opener_and_closer(spoken)
    return full_output


# Register presence handlers with scheduler singleton
from app.core.scheduler import scheduler

scheduler.register_handler("briefing", generate_morning_briefing)
scheduler.register_handler("welcome_back", generate_welcome_back)
scheduler.register_handler("check_in", generate_smart_check_in)
scheduler.register_handler("wrap_up", generate_evening_wrap_up)
