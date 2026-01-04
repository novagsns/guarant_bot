"""Database backup utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

from sqlalchemy.engine.url import make_url


def _resolve_sqlite_path(database_url: str) -> Path | None:
    """Resolve the filesystem path for a SQLite database URL.

    Args:
        database_url: SQLAlchemy database URL.

    Returns:
        Path to the SQLite database file, or None for non-SQLite URLs.
    """
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    if not url.database:
        return None
    return Path(url.database)


def backup_sqlite_db(database_url: str, backup_dir: str) -> Path | None:
    """Create a timestamped backup of a SQLite database file.

    Args:
        database_url: SQLAlchemy database URL.
        backup_dir: Directory to store backup files.

    Returns:
        Path to the backup file or None if there is nothing to back up.
    """
    db_path = _resolve_sqlite_path(database_url)
    if not db_path or not db_path.exists():
        return None

    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = target_dir / f"{db_path.stem}_{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    return backup_path
