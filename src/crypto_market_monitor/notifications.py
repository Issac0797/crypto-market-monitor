"""Unified service: persistent IDs, priority, rate limits and restart-safe retries."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from crypto_market_monitor.notification_events import NotificationEvent, format_message
from crypto_market_monitor.notification_store import Outbox
from crypto_market_monitor.telegram_transport import TelegramFailure

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self,
        path: Path,
        sender: Callable[[str], Awaitable[None]] | None = None,
        interval_ms: int = 3100,
        clock: Callable[[], int] | None = None,
        route: str = "injected-sender",
        formatter: Callable[[NotificationEvent], str] = format_message,
        delivery_guard: Callable[[NotificationEvent], bool] | None = None,
    ) -> None:
        if interval_ms < 0:
            raise ValueError("Nonnegative delivery interval required")
        self.outbox = Outbox(path)
        if sender is not None:
            with self.outbox.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                channel = db.execute(
                    "SELECT value FROM notification_meta WHERE key='bot_channel'"
                ).fetchone()
                if (
                    channel
                    and route != "injected-sender"
                    and not route.startswith(f"telegram:{channel[0]}:")
                ):
                    raise ValueError("Sender identity does not match Bot channel")
                bound = db.execute(
                    "SELECT value FROM notification_meta WHERE key='destination'"
                ).fetchone()
                if bound and bound[0] != route:
                    raise ValueError("Outbox destination differs; use a separate database")
                db.execute(
                    "INSERT OR IGNORE INTO notification_meta VALUES ('destination',?)", (route,)
                )
        self.sender = sender
        self.formatter = formatter
        self.delivery_guard = delivery_guard
        self.interval_ms = interval_ms
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self.task: asyncio.Task | None = None
        self.collector_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def publish(self, event: NotificationEvent) -> bool:
        """Persist before acknowledgement. No network activity in the producer path."""
        return await asyncio.to_thread(self.outbox.enqueue, event)

    async def deliver_once(self) -> bool:
        async with self._lock:
            if self.sender is None:
                return False
            now = self.clock()
            row = await asyncio.to_thread(self.outbox.claim_due, now, self.interval_ms)
            if row is None:
                return False
            event = NotificationEvent.from_json(row["body"])
            if self.delivery_guard is not None and not self.delivery_guard(event):
                with self.outbox.connect() as db:
                    db.execute(
                        "UPDATE notification_outbox SET state='expired' WHERE event_id=?",
                        (event.event_id,),
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO notification_meta VALUES ('delivery_lease_ms','0')"
                    )
                return True
            try:
                await asyncio.wait_for(self.sender(self.formatter(event)), timeout=6)
            except Exception as exc:
                attempts = row["attempts"] + 1
                delay = min(3_600_000, 1000 * 2 ** min(attempts, 12))
                if isinstance(exc, TelegramFailure) and exc.retry_after:
                    delay = max(delay, exc.retry_after * 1000)
                    await asyncio.to_thread(
                        self.outbox.set_meta, "next_send_ms", str(self.clock() + delay)
                    )
                await asyncio.to_thread(
                    self.outbox.failed, event.event_id, self.clock(), attempts, delay
                )
                logger.warning("Notification delivery failed; pending record retained")
            else:
                await asyncio.to_thread(self.outbox.sent, event.event_id, self.clock())
                logger.info("Notification delivered")
            finally:
                await asyncio.to_thread(self.outbox.set_meta, "delivery_lease_ms", "0")
            return True

    def start(self, collector: Callable[[], None] | None = None) -> None:
        if self.task is not None:
            raise RuntimeError("Notification service already started")
        self.task = asyncio.create_task(self._worker())
        if collector is not None:
            self.collector_task = asyncio.create_task(self._collect(collector))

    async def _collect(self, collector: Callable[[], None]) -> None:
        while True:
            operation = asyncio.create_task(asyncio.to_thread(collector))
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await operation
                raise
            except Exception:
                logger.error("Notification collection failed; committed PAPER journal retained")
            await asyncio.sleep(1)

    async def _worker(self) -> None:
        while True:
            try:
                await self.deliver_once()
            except Exception:
                logger.error("Notification worker unavailable; durable records retained")
            await asyncio.sleep(0.1)

    async def close(self) -> None:
        if self.collector_task is not None:
            self.collector_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.collector_task
            self.collector_task = None
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
