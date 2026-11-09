"""SQLite durable outbox; each database is owned by one Notification Service worker."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from crypto_market_monitor.notification_events import NotificationEvent


class Outbox:
    def __init__(self, path: Path, *, timeout: float = 0.2) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.timeout = timeout
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS notification_outbox(
                    event_id TEXT PRIMARY KEY, body TEXT NOT NULL, priority INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_ms INTEGER NOT NULL DEFAULT 0, sent_ms INTEGER, last_error TEXT);
                CREATE INDEX IF NOT EXISTS notification_due
                    ON notification_outbox(state, next_ms, priority);
                CREATE TABLE IF NOT EXISTS notification_meta(key TEXT PRIMARY KEY, value TEXT);
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=self.timeout)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, event: NotificationEvent) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.enqueue_in(db, event)

    @staticmethod
    def enqueue_in(db: sqlite3.Connection, event: NotificationEvent) -> bool:
        """Use an existing transaction to commit producer state and event together."""
        body = event.to_json()
        channel = db.execute(
            "SELECT value FROM notification_meta WHERE key='bot_channel'"
        ).fetchone()
        if channel and event.details.get("bot_channel") != channel[0]:
            raise ValueError("Event belongs to another Bot channel")
        existing = db.execute(
            "SELECT body FROM notification_outbox WHERE event_id=?", (event.event_id,)
        ).fetchone()
        if existing:
            if existing[0] != body:
                raise ValueError("Notification ID reused with different contents")
            return False
        db.execute(
            "INSERT INTO notification_outbox(event_id,body,priority) VALUES (?,?,?)",
            (event.event_id, body, int(event.priority)),
        )
        return True

    def bind_channel(self, channel: str) -> None:
        if channel not in {"A", "B"}:
            raise ValueError("Unknown Bot channel")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            bound = db.execute(
                "SELECT value FROM notification_meta WHERE key='bot_channel'"
            ).fetchone()
            if bound and bound[0] != channel:
                raise ValueError("Outbox belongs to another Bot channel")
            destination = db.execute(
                "SELECT value FROM notification_meta WHERE key='destination'"
            ).fetchone()
            if (
                destination
                and destination[0] != "injected-sender"
                and not destination[0].startswith(f"telegram:{channel}:")
            ):
                raise ValueError("Sender identity does not match Bot channel")
            for row in db.execute("SELECT body FROM notification_outbox"):
                if NotificationEvent.from_json(row[0]).details.get("bot_channel") != channel:
                    raise ValueError("Existing events do not belong to this Bot channel")
            db.execute(
                "INSERT OR IGNORE INTO notification_meta VALUES ('bot_channel',?)", (channel,)
            )

    def next_due(self, now_ms: int) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM notification_outbox WHERE state='pending' AND next_ms<=? "
                "ORDER BY priority,rowid LIMIT 1",
                (now_ms,),
            ).fetchone()

    def claim_due(
        self, now_ms: int, interval_ms: int, *, implication_owner: bool = False
    ) -> sqlite3.Row | None:
        """Atomically reserve one send across workers; crash leases expire after ten seconds."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Retired strategy-monitor records remain auditable but must never be sent.
            db.execute(
                "UPDATE notification_outbox SET state='retired' WHERE state='pending' "
                "AND event_id LIKE 'monitor:%'"
            )
            gates = dict(
                db.execute(
                    "SELECT key,value FROM notification_meta "
                    "WHERE key IN ('next_send_ms','delivery_lease_ms')"
                ).fetchall()
            )
            if any(now_ms < int(value) for value in gates.values()):
                return None
            # Implication records require their live registry/book guard. Legacy A/B workers
            # cannot consume them. Both owners still share the destination's send lease/rate.
            selection = (
                "AND event_id LIKE 'implication:%' "
                if implication_owner
                else "AND event_id NOT LIKE 'implication:%' "
            )
            row = db.execute(
                "SELECT * FROM notification_outbox WHERE state='pending' AND next_ms<=? "
                + selection
                + "ORDER BY priority,rowid LIMIT 1",
                (now_ms,),
            ).fetchone()
            while row and row["event_id"].startswith("engine:"):
                event = NotificationEvent.from_json(row["body"])
                expiry = event.details.get("expires_ms")
                if type(expiry) is int and now_ms <= expiry:
                    break
                db.execute(
                    "UPDATE notification_outbox SET state='expired' WHERE event_id=?",
                    (row["event_id"],),
                )
                row = db.execute(
                    "SELECT * FROM notification_outbox WHERE state='pending' AND next_ms<=? "
                    + selection
                    + "ORDER BY priority,rowid LIMIT 1",
                    (now_ms,),
                ).fetchone()
            if row:
                db.executemany(
                    "INSERT OR REPLACE INTO notification_meta VALUES (?,?)",
                    [
                        ("next_send_ms", str(now_ms + interval_ms)),
                        ("delivery_lease_ms", str(now_ms + 10000)),
                    ],
                )
            return row

    def sent(self, event_id: str, now_ms: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE notification_outbox SET state='sent',sent_ms=?,last_error=NULL "
                "WHERE event_id=?",
                (now_ms, event_id),
            )

    def failed(self, event_id: str, now_ms: int, attempts: int, delay_ms: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE notification_outbox SET attempts=?,next_ms=?,last_error='delivery_failed' "
                "WHERE event_id=?",
                (attempts, now_ms + delay_ms, event_id),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM notification_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO notification_meta VALUES (?,?)", (key, value))
