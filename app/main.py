"""Application entry point and CLI runner."""

import sys
import logging
from app.core.logging_setup import setup_logging
from app.config import GEMINI_MODEL, WEB_PORT

BANNER = r"""
  _   _    _    _     _        _      ____ _____     ___    __  __ 
 | \ | |  / \  | |   | |      / \    / ___|_ _\ \   / / \  |  \/  |
 |  \| | / _ \ | |   | |     / _ \   \___ \| | \ \ / / _ \ | |\/| |
 | |\  |/ ___ \| |___| |___ / ___ \   ___) | |  \ V / ___ \| |  | |
 |_| \_/_/   \_\_____|_____/_/   \_\ |____/___|  \_/_/   \_\_|  |_|
 
 >> Think smart. Speak naturally. Act responsibly. Stay curious. <<
"""

def main():
    setup_logging()
    logger = logging.getLogger("nalla")
    print(BANNER)
    logger.info("Initializing NALLA SIVAM...")
    logger.info("Configured Gemini model: %s", GEMINI_MODEL)
    logger.info("Ready on port: %d (Phase 0 scaffold)", WEB_PORT)
    logger.info("Done. Everything checks out.")

if __name__ == "__main__":
    main()
