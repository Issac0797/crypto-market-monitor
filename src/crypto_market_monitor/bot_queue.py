"""Bot queue policy: finite retry, bounded batches, persistent cooldown and history.

The bot_b_ table names are persisted schema compatibility identifiers.
"""

import asyncio
import logging

from crypto_market_monitor.bot_policy import BotPolicy
from crypto_market_monitor.bot_templates import format_bot
from crypto_market_monitor.notification_events import NotificationEvent, Priority
from crypto_market_monitor.notification_store import Outbox
from crypto_market_monitor.notifications import NotificationService
from crypto_market_monitor.telegram_transport import TelegramFailure


def enqueue_in(db, event: NotificationEvent, policy: BotPolicy) -> bool:
    if event.details.get("bot_channel") != "B":
        raise ValueError("Bot queue refuses another channel")
    existing = db.execute(
        "SELECT body FROM notification_outbox WHERE event_id=?", (event.event_id,)
    ).fetchone()
    if existing:
        if existing[0] != event.to_json():
            raise ValueError("Bot event ID reused with different contents")
        return False
    episode = event.details.get("episode_id")
    if episode and event.status.endswith(("RECOVERED", "ESCALATED")):
        db.execute(
            "UPDATE notification_outbox SET state='superseded' WHERE state='pending' "
            "AND json_extract(body,'$.details.episode_id')=?",
            (episode,),
        )
    # Cooldown is per episode+status: a recovered/new episode is never suppressed.
    gate = f"{episode}:{event.status}" if episode else event.event_id
    row = db.execute("SELECT ms FROM bot_b_notice_gate WHERE key=?", (gate,)).fetchone()
    if (
        row
        and not event.status.endswith("ESCALATED")
        and event.priority != Priority.CRITICAL
        and event.timestamp_ms - row[0] < policy.cooldown_ms
    ):
        return False
    added = Outbox.enqueue_in(db, event)
    if added:
        db.execute(
            "INSERT OR REPLACE INTO bot_b_notice_gate VALUES (?,?)", (gate, event.timestamp_ms)
        )
        db.execute(
            "INSERT OR IGNORE INTO bot_b_history VALUES (?,?,?)",
            (event.event_id, event.timestamp_ms, event.to_json()),
        )
        if event.priority != Priority.CRITICAL and event.status != "COMMAND_REPLY":
            db.execute(
                "UPDATE notification_outbox SET next_ms=? WHERE event_id=?",
                (event.timestamp_ms + policy.merge_ms, event.event_id),
            )
    return added


class BotNotificationService(NotificationService):
    def __init__(self, *args, policy: BotPolicy, **kwargs) -> None:
        super().__init__(*args, formatter=format_bot, **kwargs)
        self.outbox.bind_channel("B")
        self.policy = policy

    async def deliver_once(self) -> bool:
        async with self._lock:
            if self.sender is None:
                return False
            now = self.clock()
            with self.outbox.connect() as db:
                db.execute(
                    "UPDATE notification_outbox SET state='failed',last_error='exhausted' "
                    "WHERE state='pending' AND attempts>=?",
                    (self.policy.max_attempts,),
                )
            row = self.outbox.claim_due(now, self.interval_ms)
            if row is None:
                return False
            rows = [row]
            first = NotificationEvent.from_json(row["body"])
            if (
                self.policy.max_batch > 1
                and first.priority != Priority.CRITICAL
                and first.status != "COMMAND_REPLY"
            ):
                with self.outbox.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    for candidate in db.execute(
                        "SELECT * FROM notification_outbox WHERE state='pending' AND next_ms<=? "
                        "AND event_id!=? ORDER BY priority,rowid LIMIT 50",
                        (now, first.event_id),
                    ).fetchall():
                        e = NotificationEvent.from_json(candidate["body"])
                        if (e.source, e.status, e.priority) == (
                            first.source,
                            first.status,
                            first.priority,
                        ):
                            if now <= e.details.get("expires_ms", 0):
                                rows.append(candidate)
                                db.execute(
                                    "UPDATE notification_outbox SET next_ms=? WHERE event_id=?",
                                    (now + 10000, e.event_id),
                                )
                        if len(rows) >= self.policy.max_batch:
                            break
            try:
                text = "\n\n━━━━━━━━━━━━━━\n\n".join(
                    format_bot(NotificationEvent.from_json(r["body"])) for r in rows
                )
                # Keep the merged payload under Telegram's limit without cutting HTML tags.
                if len(text.encode("utf-16-le")) > 7800:
                    rows = rows[:1]
                    text = format_bot(first)
                await asyncio.wait_for(self.sender(text), 6)
            except Exception as exc:
                code = exc.error_code if isinstance(exc, TelegramFailure) else 0
                delay = min(60000, 1000 * 2 ** min(max(r["attempts"] for r in rows) + 1, 10))
                if isinstance(exc, TelegramFailure) and exc.retry_after:
                    delay = max(delay, exc.retry_after * 1000)
                    self.outbox.set_meta("next_send_ms", str(self.clock() + delay))
                with self.outbox.connect() as db:
                    for r in rows:
                        attempts = r["attempts"] + 1
                        failed = attempts >= self.policy.max_attempts or code in {
                            400,
                            401,
                            403,
                            404,
                        }
                        db.execute(
                            "UPDATE notification_outbox SET attempts=?,next_ms=?,state=?,"
                            "last_error=? WHERE event_id=?",
                            (
                                attempts,
                                self.clock() + delay,
                                "failed" if failed else "pending",
                                "permanent_or_exhausted" if failed else "delivery_failed",
                                r["event_id"],
                            ),
                        )
                logging.warning("Bot delivery failed; redacted durable retry status saved")
            else:
                for r in rows:
                    self.outbox.sent(r["event_id"], self.clock())
            finally:
                self.outbox.set_meta("delivery_lease_ms", "0")
            return True

    async def _worker(self) -> None:
        while True:
            try:
                await self.deliver_once()
            except Exception:
                logging.error("Bot queue unavailable; durable records retained (redacted)")
            await asyncio.sleep(0.5)
