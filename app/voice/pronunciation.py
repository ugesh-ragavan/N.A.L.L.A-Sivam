"""app/voice/pronunciation.py

Pronunciation mapping mechanism for NALLA SIVAM.

Maps phonetic/Tanglish words written in Latin script (e.g. 'sollunga', 'aiyo')
to Tamil script (e.g. 'சொல்லுங்க', 'ஐயோ') prior to Gemini TTS synthesis,
ensuring authentic Tamil phonology and cadence.

Applied ONLY when synthesizing with Gemini TTS.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import BASE_DIR

logger = logging.getLogger("nalla.voice.pronunciation")

_DEFAULT_MAP_PATH = BASE_DIR / "data" / "pronunciation.json"

_cache_map: dict[str, str] = {}
_cache_mtime: float = 0.0
_compiled_patterns: list[tuple[re.Pattern, str]] = []


def _recompile_patterns(mapping: dict[str, str]) -> None:
    """Precompile regex patterns for all pronunciation mapping keys."""
    global _compiled_patterns
    patterns = []
    # Sort keys by length descending to match multi-word phrases first
    for k in sorted(mapping.keys(), key=lambda x: len(x), reverse=True):
        if not k.strip():
            continue
        pat = re.compile(rf"\b{re.escape(k.strip())}\b", re.IGNORECASE)
        patterns.append((pat, mapping[k]))
    _compiled_patterns = patterns


def load_pronunciation_map(path: Path | None = None) -> dict[str, str]:
    """Load the pronunciation mapping from disk, cached by mtime."""
    global _cache_map, _cache_mtime
    file_path = path or _DEFAULT_MAP_PATH
    if not file_path.exists():
        _cache_map = {}
        _cache_mtime = 0.0
        _compiled_patterns.clear()
        return {}

    try:
        mtime = file_path.stat().st_mtime
        if mtime != _cache_mtime:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _cache_map = {str(k): str(v) for k, v in data.items()}
            else:
                _cache_map = {}
            _cache_mtime = mtime
            _recompile_patterns(_cache_map)
            logger.debug("Loaded %d pronunciation mappings from %s", len(_cache_map), file_path)
        return dict(_cache_map)
    except Exception as exc:
        logger.warning("Could not load pronunciation map from %s: %s", file_path, exc)
        return dict(_cache_map)


def save_pronunciation_map(mapping: dict[str, str], path: Path | None = None) -> None:
    """Save the pronunciation mapping to disk and update in-memory cache."""
    global _cache_map, _cache_mtime
    file_path = path or _DEFAULT_MAP_PATH
    file_path.parent.mkdir(parents=True, exist_ok=True)
    clean_map = {str(k).strip(): str(v).strip() for k, v in mapping.items() if str(k).strip()}
    file_path.write_text(json.dumps(clean_map, indent=2, ensure_ascii=False), encoding="utf-8")
    _cache_map = clean_map
    _cache_mtime = file_path.stat().st_mtime
    _recompile_patterns(_cache_map)
    logger.info("Saved %d pronunciation mappings to %s", len(_cache_map), file_path)


def apply_pronunciation_map(text: str, mapping_or_path: dict[str, str] | Path | str | None = None) -> str:
    """Apply the pronunciation map to the given text.

    Replaces whole-word matches with their phonetic/Tamil script target.
    Can accept either a custom mapping dict or a Path/str to a JSON map file.
    """
    if not text:
        return ""
    if isinstance(mapping_or_path, dict):
        patterns = []
        for k in sorted(mapping_or_path.keys(), key=lambda x: len(x), reverse=True):
            if not k.strip():
                continue
            pat = re.compile(rf"\b{re.escape(k.strip())}\b", re.IGNORECASE)
            patterns.append((pat, mapping_or_path[k]))
        result = text
        for pat, replacement in patterns:
            result = pat.sub(replacement, result)
        return result

    target_path = Path(mapping_or_path) if isinstance(mapping_or_path, (str, Path)) else None
    load_pronunciation_map(target_path)
    if not _compiled_patterns:
        return text

    result = text
    for pat, replacement in _compiled_patterns:
        result = pat.sub(replacement, result)
    return result
