"""Read only the original bound Chat ID; no token, event text or network access."""

import argparse
import re
import sqlite3
from pathlib import Path


def saved_target(path: Path) -> int | None:
    if not path.is_file():
        return None
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT value FROM notification_meta WHERE key='destination'").fetchone()
    if not row or not isinstance(row[0], str):
        return None
    match = re.fullmatch(r"telegram:(-?[1-9][0-9]*)", row[0])
    if match is None:
        return None
    value = int(match[1])
    return value if -(2**63) <= value < 2**63 else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Read a saved Telegram destination locally")
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        target = saved_target(args.db)
    except (OSError, sqlite3.Error):
        parser.exit(1, "Saved destination lookup failed (details redacted).\n")
    if target is not None:
        print(target)


if __name__ == "__main__":
    main()
