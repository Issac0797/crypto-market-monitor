"""Durable risk lifecycle; DATA_STALE retains active risk and resets confirmation.

The bot_b_ table names are persisted schema compatibility identifiers.
"""

import sqlite3
from decimal import Decimal

from crypto_market_monitor.models import DataError


def initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS bot_b_risks(
            key TEXT PRIMARY KEY, kind TEXT, symbol TEXT, phase TEXT, active INTEGER,
            candidate INTEGER, since_ms INTEGER, last_ms INTEGER, value TEXT, context TEXT,
            episode_id TEXT, started_ms INTEGER, peak TEXT, last_notice_ms INTEGER,
            unknown_since_ms INTEGER);
        CREATE TABLE IF NOT EXISTS bot_b_history(
            event_id TEXT PRIMARY KEY, ms INTEGER, body TEXT);
        CREATE TABLE IF NOT EXISTS bot_b_gaps(
            key TEXT PRIMARY KEY, since_ms INTEGER, last_ms INTEGER, reason TEXT,
            recovered_ms INTEGER);
        CREATE TABLE IF NOT EXISTS bot_b_notice_gate(
            key TEXT PRIMARY KEY, ms INTEGER);
    """)


def stale(db: sqlite3.Connection, key: str, now_ms: int, reason: str) -> None:
    row = db.execute("SELECT phase,last_ms FROM bot_b_risks WHERE key=?", (key,)).fetchone()
    if row is None:
        # A never-observed feed is still UNKNOWN, with no invented last source time.
        kind, symbol = key.split(":", 1)
        if kind == "move":
            symbol = symbol.rsplit(":", 1)[0]
        db.execute(
            "INSERT INTO bot_b_risks VALUES (?,?,?,'DATA_STALE',0,0,0,0,NULL,'',"
            "NULL,NULL,NULL,0,?)",
            (key, kind, symbol, now_ms),
        )
    elif row[0] != "DATA_STALE":
        db.execute(
            "UPDATE bot_b_risks SET phase='DATA_STALE',candidate=active,since_ms=0,"
            "unknown_since_ms=? WHERE key=?",
            (now_ms, key),
        )
    else:
        return
    db.execute(
        "INSERT OR REPLACE INTO bot_b_gaps VALUES (?,?,?,?,NULL)",
        (key, now_ms, row[1] if row else None, reason),
    )


def evaluate(
    db: sqlite3.Connection,
    key: str,
    kind: str,
    symbol: str,
    value: Decimal,
    stamp: int,
    threshold: Decimal,
    recovery: Decimal,
    confirm_ms: int,
    max_gap_ms: int,
    context: str,
    *,
    allow_equal_stamp: bool = False,
) -> tuple[tuple[int, int, int, int], str | None, dict]:
    row = db.execute("SELECT * FROM bot_b_risks WHERE key=?", (key,)).fetchone()
    # Explicit names make this independent of sqlite Row configuration in migration/tests.
    active, candidate, since, last, episode, start, peak, notice = (0, 0, 0, 0, None, None, None, 0)
    if row:
        active, candidate, since, last = row[4:8]
        episode, start, peak, notice = row[10:14]
        peak = Decimal(peak) if peak is not None else None
        if stamp < last or stamp == last and not allow_equal_stamp:
            if stamp == last and row[8] is not None and value != Decimal(row[8]):
                raise DataError("Risk value changed without a new source timestamp")
            return (active, candidate, since, notice), None, {}
        if row[3] == "DATA_STALE" or stamp - last > max_gap_ms or row[9] != context:
            candidate, since = active, 0
            if row[3] != "DATA_STALE" and last:
                stale(db, key, stamp, "source_gap_or_context_change")
    desired = active
    if abs(value) >= threshold:
        desired = (1 if value > 0 else -1) * (2 if abs(value) >= threshold * 2 else 1)
        if desired * active > 0 and abs(desired) < abs(active):
            desired = active
    elif abs(value) <= recovery:
        desired = 0
    status = None
    phase = "ACTIVE" if active else "RESOLVED" if episode else "NORMAL"
    if desired != active:
        if candidate != desired or since == 0:
            candidate, since = desired, stamp
        phase = "RECOVERING" if desired == 0 else "PENDING" if active == 0 else "ACTIVE"
        if stamp - since >= confirm_ms:
            status = "RECOVERED" if desired == 0 else "ENTERED" if active == 0 else "ESCALATED"
            if active == 0:
                episode, start, peak = f"{key}:{stamp}", stamp, value
            active, candidate, since, notice = desired, desired, stamp, stamp
            phase = "RESOLVED" if desired == 0 else "ACTIVE"
    else:
        candidate, since = active, stamp
    if active or status == "RECOVERED":
        peak = value if peak is None or abs(value) > abs(peak) else peak
    db.execute(
        "INSERT OR REPLACE INTO bot_b_risks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
        (
            key,
            kind,
            symbol,
            phase,
            active,
            candidate,
            since,
            stamp,
            str(value),
            context,
            episode,
            start,
            str(peak) if peak is not None else None,
            notice,
        ),
    )
    db.execute(
        "UPDATE bot_b_gaps SET recovered_ms=? WHERE key=? AND recovered_ms IS NULL", (stamp, key)
    )
    return (
        (active, candidate, since, notice),
        status,
        {
            "risk_key": key,
            "episode_id": episode,
            "episode_started_ms": start,
            "peak_value_percent": peak * 100 if peak is not None else None,
            "peak_incomplete": start is None,
            "episode_duration_ms": stamp - start if start is not None else None,
            "lifecycle": phase,
        },
    )
