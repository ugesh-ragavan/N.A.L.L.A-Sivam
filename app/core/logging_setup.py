"""Logging setup module.

Sets up the root logger with:
- A streaming console handler.
- A rotating file handler (5 MB × 3 backups) in data/logs/nalla.log.

Call setup_logging() once at application startup, before any other
module logs anything.
"""

import logging
import logging.handlers
from pathlib import Path

from app.config import BASE_DIR, LOG_LEVEL


def setup_logging() -> None:
    """Configure application-wide logging."""
    log_dir = BASE_DIR / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, LOG_LEVEL, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Rotating file handler: 5 MB per file, keep 3 backups
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "nalla.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    logging.basicConfig(level=level, handlers=[console_handler, file_handler])
