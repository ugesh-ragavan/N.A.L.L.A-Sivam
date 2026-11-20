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
from app.config import GEMINI_MODEL, WEB_HOST, WEB_PORT

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
    """Construct and return a ready-to-use Agent."""
    from app.brain.gemini_provider import GeminiProvider
    from app.brain.tools import registry
    from app.brain.persona import persona
    from app.core.approvals import ApprovalManager
    from app.brain.agent import Agent

    provider  = GeminiProvider()
    approvals = ApprovalManager()          # uses terminal callback by default
    return Agent(provider, registry, persona, approvals)


def _run_chat() -> None:
    """Interactive terminal chat loop."""
    logger = logging.getLogger("nalla")
    _print_banner(logger)
    logger.info("Phase 1 chat started. Type 'exit' or 'quit' to leave.")

    agent = _build_agent()
    history: list[dict] = []

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

        # Wake-word fast path — no LLM call needed.
        if user_input.strip().rstrip("!").lower() == "nalla":
            reply = "Yes boss, sollunga!"
            print(f"Nalla: {reply}\n")
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
        logger.info("Phase 1 ready. Run `python -m app chat` to start chatting.")
        return

    if args[0] == "chat":
        _run_chat()
        return

    logger.error("Unknown command: %s. Available: chat", args[0])
    sys.exit(1)


if __name__ == "__main__":
    main()
