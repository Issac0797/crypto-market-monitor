"""Stopped-only, audited Bot reliability migration; never change market thresholds.

The bot_b_ tables, meta keys, and existing database filename are persisted contracts.
"""

import argparse
import hashlib
import sqlite3
import time
import tomllib
from pathlib import Path

from crypto_market_monitor import bot_lifecycle
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.operations import instance_lock


def migrate(root: Path, old_path: Path, new_path: Path) -> dict:
    old_bytes, new_bytes = old_path.read_bytes(), new_path.read_bytes()
    old, new = tomllib.loads(old_bytes.decode()), tomllib.loads(new_bytes.decode())
    allowed = {"reliability"} | ({"usde"} if "usde" not in old else set())
    if {k: v for k, v in old.items() if k not in allowed} != {
        k: v for k, v in new.items() if k not in allowed
    }:
        raise ValueError("Market monitoring parameters changed; refuse migration")
    config = RiskWatchConfig.load(new_path)
    if config.reliability is None:
        raise ValueError("Explicit reliability configuration required")
    old_sha, new_sha = (hashlib.sha256(b).hexdigest() for b in (old_bytes, new_bytes))
    database = root / "data/bot-b.sqlite3"
    if not database.is_file():
        raise ValueError("Existing Bot database required")
    with instance_lock(root / "service.lock"), sqlite3.connect(database) as db:
        if db.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise ValueError("Corrupt database")
        if db.execute("select value from notification_meta where key='bot_channel'").fetchone() != (
            "B",
        ):
            raise ValueError("Only Bot database may migrate")
        actual = db.execute(
            "select value from notification_meta where key='risk_config'"
        ).fetchone()
        if actual == (new_sha,):
            return {"status": "already_migrated", "config_sha256": new_sha}
        if actual != (old_sha,):
            raise ValueError("Old configuration fingerprint mismatch")
        bot_lifecycle.initialize(db)
        db.execute("BEGIN IMMEDIATE")
        stamp = time.time_ns() // 1000000
        for symbol, window, active, _, _, last_notice in db.execute(
            "select * from risk_state"
        ).fetchall():
            last = (
                db.execute("select max(ms) from risk_samples where symbol=?", (symbol,)).fetchone()[
                    0
                ]
                or 0
            )
            key = f"move:{symbol}:{window}"
            db.execute(
                "INSERT OR IGNORE INTO bot_b_risks VALUES "
                "(?,?,?,'DATA_STALE',?,?,0,?,NULL,?,?,NULL,NULL,?,?)",
                (
                    key,
                    "move",
                    symbol,
                    active,
                    active,
                    last,
                    str(window),
                    f"legacy:{key}" if active else None,
                    last_notice,
                    stamp,
                ),
            )
        for kind, symbol, last, value, context, active, _, _, notice in db.execute(
            "select * from derivative_state"
        ).fetchall():
            key = f"{kind}:{symbol}"
            db.execute(
                "INSERT OR IGNORE INTO bot_b_risks VALUES "
                "(?,?,?,'DATA_STALE',?,?,0,?,?,?,?,NULL,NULL,?,?)",
                (
                    key,
                    kind,
                    symbol,
                    active,
                    active,
                    last,
                    value,
                    context,
                    f"legacy:{key}" if active else None,
                    notice,
                    stamp,
                ),
            )
        # Import sent records without re-enqueueing; retire old startup notices.
        db.execute(
            "INSERT OR IGNORE INTO bot_b_history SELECT event_id,"
            "json_extract(body,'$.timestamp_ms'),body FROM notification_outbox"
        )
        db.execute(
            "UPDATE notification_outbox SET state='retired' WHERE state='pending' AND "
            "json_extract(body,'$.status')='SERVER_STARTED'"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS risk_config_changes(old_sha TEXT,new_sha TEXT,ms INTEGER)"
        )
        db.execute("INSERT INTO risk_config_changes VALUES (?,?,?)", (old_sha, new_sha, stamp))
        db.execute("UPDATE notification_meta SET value=? WHERE key='risk_config'", (new_sha,))
        db.execute("INSERT OR REPLACE INTO notification_meta VALUES ('bot_b_schema','2')")
    return {"status": "migrated", "old_sha256": old_sha, "config_sha256": new_sha}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--from-config", type=Path, required=True)
    p.add_argument("--to-config", type=Path, required=True)
    a = p.parse_args()
    print(migrate(a.state_dir, a.from_config, a.to_config))


if __name__ == "__main__":
    main()
