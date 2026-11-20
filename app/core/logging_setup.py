"""Logging setup module."""

import logging
import os
from pathlib import Path
from app.config import LOG_LEVEL, BASE_DIR

def setup_logging():
    log_dir = BASE_DIR / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "nalla.log", encoding="utf-8")
        ]
    )
