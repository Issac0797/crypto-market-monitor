"""Small additive Bot B evidence tables, updated inside producer transactions."""

import sqlite3
from decimal import Decimal


def initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS bot_b_episodes(
            key TEXT PRIMARY KEY, started_ms INTEGER, peak TEXT, active INTEGER);
        CREATE TABLE IF NOT EXISTS bot_b_feed_details(
            symbol TEXT PRIMARY KEY, last_valid_ms INTEGER, failed_since_ms INTEGER);
    """)


def episode(
    db: sqlite3.Connection,
    key: str,
    previous: int,
    active: int,
    status: str | None,
    value: Decimal,
    stamp: int,
) -> dict:
    row = db.execute("SELECT started_ms,peak FROM bot_b_episodes WHERE key=?", (key,)).fetchone()
    start = row[0] if row else None
    peak = Decimal(row[1]) if row and row[1] is not None else None
    if status == "ENTERED":
        start, peak = stamp, value
    elif previous or active:
        # Old databases have no historical peak/start: never invent them.
        peak = value if peak is None or abs(value) > abs(peak) else peak
    if previous or active or status:
        db.execute(
            "INSERT OR REPLACE INTO bot_b_episodes VALUES (?,?,?,?)",
            (key, start, str(peak) if peak is not None else None, active),
        )
    return {
        "episode_started_ms": start,
        "peak_incomplete": start is None,
        "peak_value_percent": peak * 100 if peak is not None else None,
        "episode_duration_ms": stamp - start if start is not None else None,
    }


def feed(
    db: sqlite3.Connection, symbol: str, good: bool, stamp: int, *, recovering: bool = False
) -> dict:
    row = db.execute(
        "SELECT last_valid_ms,failed_since_ms FROM bot_b_feed_details WHERE symbol=?", (symbol,)
    ).fetchone()
    last, since = row if row else (None, None)
    if not good and since is None:
        since = stamp
    result = {
        "last_valid_ms": last,
        "outage_duration_ms": stamp - since if since is not None else 0,
    }
    db.execute(
        "INSERT OR REPLACE INTO bot_b_feed_details VALUES (?,?,?)",
        (symbol, stamp if good else last, None if good and not recovering else since),
    )
    return result
