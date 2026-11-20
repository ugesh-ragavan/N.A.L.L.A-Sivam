"""Unit tests for Phase 0 scaffolding."""

from app.config import GEMINI_MODEL, WEB_PORT

def test_config_defaults():
    assert GEMINI_MODEL is not None
    assert WEB_PORT == 8000
