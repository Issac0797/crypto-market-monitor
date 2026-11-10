"""Explicit additive derivative upgrade; preserve frozen price rules and state.

The legacy database filename, table names, and event prefix are compatibility contracts.
"""

import argparse
import hashlib
import sqlite3
import time
import tomllib
from pathlib import Path

from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.operations import instance_lock


def migrate(
    root: Path, old_path: Path, new_path: Path, *, stablecoin_upgrade: bool = False
) -> None:
    old_bytes, new_bytes = old_path.read_bytes(), new_path.read_bytes()
    old, new = tomllib.loads(old_bytes.decode()), tomllib.loads(new_bytes.decode())
    if stablecoin_upgrade:
        expected = dict(old)
        expected["rules"] = [r for r in old["rules"] if r["window_ms"] != 300000]
        expected["stablecoin"] = {
            "threshold": "0.005",
            "recovery": "0.0025",
            "confirm_ms": 60000,
            "min_depth_usd": "1000",
        }
        if (
            "stablecoin" in old
            or len(expected["rules"]) != len(old["rules"]) - 1
            or new != expected
        ):
            raise ValueError("Only reviewed five-minute removal and stablecoin addition allowed")
    elif old != {k: v for k, v in new.items() if k not in {"basis", "funding", "stablecoin"}}:
        raise ValueError("Only additive derivative rules can use this migration")
    RiskWatchConfig.load(new_path)
    old_sha, new_sha = (hashlib.sha256(b).hexdigest() for b in (old_bytes, new_bytes))
    database = root / "data/bot-b.sqlite3"
    if not database.is_file():
        raise ValueError("Existing state database required")
    with instance_lock(root / "service.lock"), sqlite3.connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT value FROM notification_meta WHERE key='risk_config'").fetchone()
        if prior is None or prior[0] != old_sha:
            raise ValueError("Stored configuration does not match reviewed old file")
        db.execute(
            "CREATE TABLE IF NOT EXISTS risk_config_changes(old_sha TEXT,new_sha TEXT,ms INTEGER)"
        )
        db.execute(
            "INSERT INTO risk_config_changes VALUES (?,?,?)",
            (old_sha, new_sha, time.time_ns() // 1000000),
        )
        db.execute("UPDATE notification_meta SET value=? WHERE key='risk_config'", (new_sha,))
        if stablecoin_upgrade:
            db.execute("DELETE FROM risk_state WHERE window=300000")
            db.execute(
                "UPDATE notification_outbox SET state='expired' WHERE state='pending' "
                "AND event_id LIKE 'engine:bot-b:move:300000:%'"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--from-config", type=Path, required=True)
    parser.add_argument("--to-config", type=Path, required=True)
    parser.add_argument("--stablecoin-upgrade", action="store_true")
    args = parser.parse_args()
    migrate(
        args.state_dir, args.from_config, args.to_config, stablecoin_upgrade=args.stablecoin_upgrade
    )
    print("Reviewed configuration migration committed; retained rules keep their history")


if __name__ == "__main__":
    main()
