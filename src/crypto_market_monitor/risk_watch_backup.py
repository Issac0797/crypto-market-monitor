"""Daily, bounded online SQLite backups; never copies credentials or live files."""

import sqlite3
from contextlib import closing
from pathlib import Path

from crypto_market_monitor.notification_store import Outbox


def backup_if_due(outbox: Outbox, root: Path, now_ms: int) -> bool:
    latest, previous, pending = (
        root / name for name in ("latest.sqlite3", "previous.sqlite3", "pending.sqlite3")
    )
    if latest.exists() and 0 <= now_ms - int(latest.stat().st_mtime * 1000) < 86400000:
        return False
    root.mkdir(parents=True, exist_ok=True)
    with outbox.connect() as source, closing(sqlite3.connect(pending)) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Online backup integrity check failed")
    if latest.exists():
        latest.replace(previous)
    pending.replace(latest)
    return True
