"""Application entry point and CLI runner.

Run with:
    python -m app

Loads config, sets up logging, prints the startup banner via the
logger, and exits cleanly. No print() calls — all output goes
through the logging system per the project coding rules.
"""

import logging

from app.core.logging_setup import setup_logging
from app.config import GEMINI_MODEL, WEB_HOST, WEB_PORT

BANNER = r"""
  _   _    _    _     _        _      ____ _____     ___    __  __ 
 | \ | |  / \  | |   | |      / \    / ___|_ _\ \   / / \  |  \/  |
 |  \| | / _ \ | |   | |     / _ \   \___ \| | \ \ / / _ \ | |\/| |
 | |\  |/ ___ \| |___| |___ / ___ \   ___) | |  \ V / ___ \| |  | |
 |_| \_/_/   \_\_____|_____/_/   \_\ |____/___|  \_/_/   \_\_|  |_|

 >> Think smart. Speak naturally. Act responsibly. Stay curious. <<
"""


def main() -> None:
    """Bootstrap the application."""
    setup_logging()
    logger = logging.getLogger("nalla")

    # Banner goes through the logger — no bare print() allowed
    for line in BANNER.splitlines():
        logger.info(line)

    logger.info("Initializing NALLA SIVAM...")
    logger.info("Model   : %s", GEMINI_MODEL)
    logger.info("Dashboard bound to %s:%d", WEB_HOST, WEB_PORT)
    logger.info("Phase 0 scaffold ready. Everything checks out.")


if __name__ == "__main__":
    main()
