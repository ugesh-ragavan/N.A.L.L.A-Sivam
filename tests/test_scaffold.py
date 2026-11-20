"""Unit tests for Phase 0 scaffolding."""

from app.config import GEMINI_MODEL, WEB_PORT, WEB_HOST, LOG_LEVEL


def test_gemini_model_set() -> None:
    """GEMINI_MODEL must not be empty and must not be the stale 2.5-flash."""
    assert GEMINI_MODEL, "GEMINI_MODEL is empty"
    assert GEMINI_MODEL != "gemini-2.5-flash", (
        "gemini-2.5-flash is unavailable for new API keys; update GEMINI_MODEL"
    )


def test_web_port_is_integer() -> None:
    """WEB_PORT must be an integer."""
    assert isinstance(WEB_PORT, int)
    assert 1024 <= WEB_PORT <= 65535, f"WEB_PORT {WEB_PORT} is out of valid range"


def test_web_host_is_local() -> None:
    """Dashboard must bind to localhost only (security rule)."""
    assert WEB_HOST == "127.0.0.1", (
        f"WEB_HOST is {WEB_HOST!r}; must be 127.0.0.1 per CONTEXT.md safety rules"
    )


def test_log_level_valid() -> None:
    """LOG_LEVEL must be a standard Python logging level."""
    import logging
    assert hasattr(logging, LOG_LEVEL), f"LOG_LEVEL {LOG_LEVEL!r} is not a valid logging level"


def test_all_subpackages_importable() -> None:
    """All app sub-packages must be importable (requires __init__.py in each)."""
    import app.brain
    import app.core
    import app.memory
    import app.voice
    import app.devices
    import app.web
