"""Application entry point and CLI runner.

Commands
--------
  python -m app          → show startup banner and exit (Phase 0 test).
  python -m app chat     → start the terminal chat session.

No print() in core logic — all logging goes through the logging module.
The chat UI is the exception: it uses print() / input() as a deliberate
terminal interface (not application logic).
"""

from __future__ import annotations

import logging
import sys

from app.core.logging_setup import setup_logging
from app.config import GEMINI_MODEL, WEB_HOST, WEB_PORT, MEMORY_ENABLED
from app.memory.store import store

BANNER = r"""
  _   _    _    _     _        _      ____ _____     ___    __  __ 
 | \ | |  / \  | |   | |      / \    / ___|_ _\ \   / / \  |  \/  |
 |  \| | / _ \ | |   | |     / _ \   \___ \| | \ \ / / _ \ | |\/| |
 | |\  |/ ___ \| |___| |___ / ___ \   ___) | |  \ V / ___ \| |  | |
 |_| \_/_/   \_\_____|_____/_/   \_\ |____/___|  \_/_/   \\_\_|  |_|

 >> Think smart. Speak naturally. Act responsibly. Stay curious. <<
"""

MAX_HISTORY = 20   # maximum turns kept in memory per session


def _print_banner(logger: logging.Logger) -> None:
    for line in BANNER.splitlines():
        logger.info(line)


def _build_agent():
    """Construct and return a ready-to-use Agent with memory store attached."""
    from app.brain.gemini_provider import GeminiProvider
    from app.brain.tools import registry
    from app.brain.persona import persona
    from app.core.approvals import ApprovalManager
    from app.brain.agent import Agent
    from app.voice.pipeline import default_pipeline

    provider  = GeminiProvider()
    approvals = ApprovalManager()          # uses terminal callback by default
    agent = Agent(
        provider,
        registry,
        persona,
        approvals,
        store=store,
        memory_enabled=MEMORY_ENABLED,
    )
    # Attach agent to voice pipeline
    default_pipeline.agent = agent
    return agent


def _start_reminders_scheduler() -> None:
    """Start the reminders background worker on a daemon thread."""
    import asyncio
    import threading
    from app.memory.scheduler import reminders_loop

    def _on_reminder(r):
        print(f"\n\a[REMINDER]: {r['text']} (due {r['due_at']})\nYou: ", end="", flush=True)
        try:
            from app.voice.pipeline import default_pipeline
            default_pipeline.speak_reminder(r["text"])
        except Exception as exc:
            logging.getLogger("nalla.memory.scheduler").debug("Could not speak reminder: %s", exc)

    def _worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                reminders_loop(
                    callback=_on_reminder,
                    interval=30.0,
                    store_instance=store,
                )
            )
        except Exception as exc:
            logging.getLogger("nalla.memory.scheduler").warning("Reminders loop exited: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True, name="RemindersScheduler")
    thread.start()


def _run_voice() -> None:
    """Interactive voice session (hands-free / push-to-talk)."""
    logger = logging.getLogger("nalla")
    _print_banner(logger)
    logger.info("Phase 4 voice session starting...")
    _start_reminders_scheduler()
    _build_agent()
    from app.voice.pipeline import default_pipeline

    print("\n" + "="*60)
    print("  NALLA SIVAM — Voice Mode (Phase 4)")
    print("  Press Enter to trigger Push-To-Talk, or type 'exit' to leave")
    print("="*60 + "\n")

    while True:
        try:
            cmd = input("[Press Enter to speak / 'exit' to quit]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nNalla: Take care, boss. Bye!")
            break
        if cmd.lower() in ("exit", "quit", "bye"):
            print("Nalla: Take care, boss. Bye!")
            break
        print("Listening... (speak now)")
        reply = default_pipeline.run_single_turn()
        if reply:
            print(f"Nalla: {reply}\n")


def _run_chat() -> None:
    """Interactive terminal chat loop with persisted history across restarts."""
    logger = logging.getLogger("nalla")
    _print_banner(logger)
    logger.info("Phase 3 chat started. Type 'exit' or 'quit' to leave.")

    # Start reminders scheduler in background
    _start_reminders_scheduler()

    agent = _build_agent()
    session_id = "default"
    history: list[dict] = []

    # Restore persisted conversation history from SQLite
    persisted_turns = store.get_messages(session_id, limit=MAX_HISTORY * 2)
    for msg in persisted_turns:
        history.append({"role": msg["role"], "content": msg["text"]})

    if history:
        logger.info("Loaded %d previous message turns from memory.", len(history))

    # UI hint (print is intentional here — it's a terminal UI)
    print("\n" + "="*60)
    print("  NALLA SIVAM — Terminal Chat  (type 'exit' to quit)")
    print("="*60 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nNalla: Take care, boss. Bye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "bye"):
            print("Nalla: Take care, boss. Bye!")
            break

        # Persist user turn
        store.save_message(session_id, "user", user_input)

        # Wake-word fast path — no LLM call needed.
        if user_input.strip().rstrip("!").lower() == "nalla":
            reply = "Yes boss, sollunga!"
            print(f"Nalla: {reply}\n")
            store.save_message(session_id, "assistant", reply)
            history.append({"role": "user",      "content": user_input})
            history.append({"role": "assistant",  "content": reply})
            history = history[-(MAX_HISTORY * 2):]
            continue

        # Normal turn — add to history and call the agent.
        history.append({"role": "user", "content": user_input})

        try:
            reply = agent.chat(history)
        except Exception as exc:
            logger.exception("Unexpected error in agent.chat")
            reply = f"(Something went wrong: {exc})"

        # Persist assistant reply
        store.save_message(session_id, "assistant", reply)
        history.append({"role": "assistant", "content": reply})

        # Cap history (each turn = 2 messages: user + assistant).
        history = history[-(MAX_HISTORY * 2):]

        print(f"Nalla: {reply}\n")


def main() -> None:
    """Bootstrap the application; dispatch to subcommand if given."""
    setup_logging()
    logger = logging.getLogger("nalla")

    args = sys.argv[1:]

    if not args:
        # Phase 0 banner-only mode.
        _print_banner(logger)
        logger.info("Initializing NALLA SIVAM...")
        logger.info("Model   : %s", GEMINI_MODEL)
        logger.info("Dashboard bound to %s:%d", WEB_HOST, WEB_PORT)
        logger.info("Phase 4 ready. Run `python -m app chat`, `python -m app voice`, or `python -m app dashboard`.")
        return

    if args[0] == "chat":
        _run_chat()
        return

    if args[0] == "voice":
        _run_voice()
        return

    if args[0] in ("dashboard", "web"):
        from app.web.server import run_server
        _print_banner(logger)
        run_server()
        return

    logger.error("Unknown command: %s. Available: chat, voice, dashboard", args[0])
    sys.exit(1)


if __name__ == "__main__":
    main()
