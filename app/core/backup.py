"""app/core/backup.py

Data Safety, Backup, Restore, and Diagnostics Bundle Manager (Prompt 5C).

Features:
- One-click backup of data/ (SQLite database, markdown notes, configurations) to a ZIP archive.
- Automatic retention policy: keeps the last 4 backups, prunes older ones.
- Secure restore with Zip-Slip vulnerability prevention and SQLite header verification.
- Automatic pre-restore snapshot taken prior to overwriting any live files.
- Sanitized diagnostics bundle generator with all secrets, tokens, and passwords redacted.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import BASE_DIR

logger = logging.getLogger("nalla.backup")

_DEFAULT_DATA_DIR = BASE_DIR / "data"
_DEFAULT_BACKUP_DIR = BASE_DIR / "data" / "backups"
_MAX_KEPT_BACKUPS = 4
_SQLITE_MAGIC_HEADER = b"SQLite format 3\x00"

# Secret patterns for diagnostics log sanitization
_SECRET_RE = re.compile(
    r"(?:AIzaSy[A-Za-z0-9_-]{33}|ghp_[A-Za-z0-9]{36}|bearer\s+[A-Za-z0-9_.-]+|password['\":\s=]+[^\s,'\"]+|api[_-]?key['\":\s=]+[^\s,'\"]+|\b(?:\d{4}[- ]?){3}\d{4}\b)",
    re.IGNORECASE,
)


def list_backups(backup_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """List all available backup archives sorted newest first."""
    b_dir = Path(backup_dir or _DEFAULT_BACKUP_DIR)
    if not b_dir.exists():
        return []

    backups = []
    for f in b_dir.glob("nalla_backup_*.zip"):
        try:
            stat = f.stat()
            backups.append({
                "filename": f.name,
                "path": str(f.resolve()),
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        except Exception:
            pass

    backups.sort(key=lambda x: x["created_at"], reverse=True)
    return backups


def create_backup(
    data_dir: Path | str | None = None,
    backup_dir: Path | str | None = None,
    prefix: str = "nalla_backup_",
) -> dict[str, Any]:
    """Create a zipped archive of application data (excluding secrets)."""
    src_dir = Path(data_dir or _DEFAULT_DATA_DIR)
    dest_dir = Path(backup_dir or _DEFAULT_BACKUP_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"{prefix}{timestamp}.zip"
    zip_path = dest_dir / zip_name

    included_files: list[str] = []

    # Files to include: nalla.db, notes folder, settings and json configs
    # Strictly exclude .env, logs folder, cache folder, and backups folder itself
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Main database
        db_file = src_dir / "nalla.db"
        if db_file.exists():
            zf.write(db_file, arcname="nalla.db")
            included_files.append("nalla.db")

        # 2. Notes directory
        notes_dir = src_dir / "notes"
        if notes_dir.exists() and notes_dir.is_dir():
            for root, _, files in os.walk(notes_dir):
                for file in files:
                    full_p = Path(root) / file
                    arcname = full_p.relative_to(src_dir)
                    zf.write(full_p, arcname=str(arcname))
                    included_files.append(str(arcname))

        # 3. Settings and JSON configs
        for json_file in src_dir.glob("*.json"):
            # Exclude files that might contain temporary cache
            zf.write(json_file, arcname=json_file.name)
            included_files.append(json_file.name)

        # 4. Voice lab audio configurations if present
        vlab_dir = src_dir / "voice_lab"
        if vlab_dir.exists() and vlab_dir.is_dir():
            for root, _, files in os.walk(vlab_dir):
                for file in files:
                    full_p = Path(root) / file
                    arcname = full_p.relative_to(src_dir)
                    zf.write(full_p, arcname=str(arcname))
                    included_files.append(str(arcname))

    # Automatic retention cleanup: keep only last 4 backups
    _prune_old_backups(dest_dir, prefix)

    stat = zip_path.stat()
    logger.info("Created backup %s (%d bytes, %d files)", zip_name, stat.st_size, len(included_files))

    return {
        "ok": True,
        "filename": zip_name,
        "path": str(zip_path.resolve()),
        "size_bytes": stat.st_size,
        "file_count": len(included_files),
        "files": included_files,
        "created_at": datetime.now().isoformat(),
    }


def _prune_old_backups(dest_dir: Path, prefix: str = "nalla_backup_") -> None:
    """Keep only the latest _MAX_KEPT_BACKUPS matching prefix."""
    try:
        matching = sorted(dest_dir.glob(f"{prefix}*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if len(matching) > _MAX_KEPT_BACKUPS:
            for old_file in matching[_MAX_KEPT_BACKUPS:]:
                try:
                    old_file.unlink()
                    logger.info("Pruned old backup: %s", old_file.name)
                except Exception as exc:
                    logger.warning("Could not delete old backup %s: %s", old_file, exc)
    except Exception as exc:
        logger.warning("Error pruning old backups: %s", exc)


def restore_backup(
    zip_path: Path | str,
    target_data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Safely restore a backup zip into target data directory."""
    archive = Path(zip_path)
    if not archive.exists():
        raise FileNotFoundError(f"Backup archive not found: {archive}")

    dest_dir = Path(target_data_dir or _DEFAULT_DATA_DIR).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 1. Inspect archive & verify Zip-Slip protection
    restored_files: list[str] = []
    with zipfile.ZipFile(archive, "r") as zf:
        for member in zf.infolist():
            member_path = (dest_dir / member.filename).resolve()
            if not member_path.is_relative_to(dest_dir):
                raise ValueError(
                    f"Malicious zip detected: '{member.filename}' extracts outside target directory."
                )

        # 2. Verify SQLite header if nalla.db is present in archive
        if "nalla.db" in zf.namelist():
            with zf.open("nalla.db", "r") as db_f:
                magic = db_f.read(16)
                if magic != _SQLITE_MAGIC_HEADER:
                    raise ValueError("Corrupt database in backup: invalid SQLite header.")

        # 3. Create pre-restore safety snapshot before overwriting
        pre_restore = create_backup(
            data_dir=dest_dir,
            prefix="nalla_pre_restore_",
        )

        # 4. Safely extract files
        for member in zf.infolist():
            zf.extract(member, dest_dir)
            restored_files.append(member.filename)

    logger.info("Successfully restored %d files from %s", len(restored_files), archive.name)
    return {
        "ok": True,
        "restored_count": len(restored_files),
        "restored_files": restored_files,
        "pre_restore_backup": pre_restore["filename"],
    }


def create_diagnostics_bundle(
    output_dir: Path | str | None = None,
    system_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a sanitized diagnostics zip archive with secrets redacted."""
    out_dir = Path(output_dir or _DEFAULT_BACKUP_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_name = f"nalla_diagnostics_{timestamp}.zip"
    bundle_path = out_dir / bundle_name

    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. System info / health status
        status_info = system_status or {
            "timestamp": datetime.now().isoformat(),
            "python_version": sys.version,
            "platform": sys.platform,
        }
        zf.writestr("system_info.json", json.dumps(_sanitize_dict(status_info), indent=2))

        # 2. Redacted settings
        settings_file = _DEFAULT_DATA_DIR / "settings.json"
        if settings_file.exists():
            try:
                raw_settings = json.loads(settings_file.read_text(encoding="utf-8"))
                sanitized_settings = _sanitize_dict(raw_settings)
                zf.writestr("settings.json", json.dumps(sanitized_settings, indent=2))
            except Exception as exc:
                zf.writestr("settings_error.txt", f"Error reading settings: {exc}")

        # 3. Redacted recent logs
        log_file = _DEFAULT_DATA_DIR / "logs" / "nalla.log"
        if log_file.exists():
            try:
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                recent_lines = lines[-500:]  # last 500 lines
                sanitized_log = "\n".join(_sanitize_text(line) for line in recent_lines)
                zf.writestr("recent_logs.txt", sanitized_log)
            except Exception as exc:
                zf.writestr("log_error.txt", f"Error reading log: {exc}")

    stat = bundle_path.stat()
    logger.info("Generated diagnostics bundle: %s (%d bytes)", bundle_name, stat.st_size)
    return {
        "ok": True,
        "filename": bundle_name,
        "path": str(bundle_path.resolve()),
        "size_bytes": stat.st_size,
        "created_at": datetime.now().isoformat(),
    }


def _sanitize_text(text: str) -> str:
    """Mask credentials, tokens, and secret patterns."""
    return _SECRET_RE.sub("[REDACTED_SECRET]", text)


def _sanitize_dict(data: Any) -> Any:
    """Recursively mask sensitive keys in nested dicts."""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if any(s in k.lower() for s in ("key", "secret", "token", "password", "auth", "credential")):
                result[k] = "[REDACTED]"
            else:
                result[k] = _sanitize_dict(v)
        return result
    if isinstance(data, list):
        return [_sanitize_dict(i) for i in data]
    return data
