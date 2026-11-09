"""Bounded, persistent price-move observations for explicitly enabled Bot B.

No broker, targets, funding forecasts, or language-model decisions.
"""

import logging
from dataclasses import asdict, dataclass, replace
from decimal import Decimal

from crypto_market_monitor import bot_b_details, bot_b_lifecycle
from crypto_market_monitor.market_data.models import MarketKind, OrderBook
from crypto_market_monitor.market_data.risk_watch_config import MoveRule, RiskWatchConfig
from crypto_market_monitor.models import DataError
from crypto_market_monitor.notification_events import EventType, NotificationEvent, Priority
from crypto_market_monitor.notification_store import Outbox


@dataclass(frozen=True)
class MoveState:
    active: int = 0
    candidate: int = 0
    since_ms: int = 0
    last_alert_ms: int = 0


def transition(
    state: MoveState, change: Decimal, rule: MoveRule, stamp: int, config: RiskWatchConfig
) -> tuple[MoveState, str | None]:
    magnitude = abs(change)
    desired = state.active
    if magnitude >= rule.threshold:
        desired = (1 if change > 0 else -1) * (2 if magnitude >= rule.threshold * 2 else 1)
        # A lower severity does not generate another alert until recovery.
        if desired * state.active > 0 and abs(desired) < abs(state.active):
            desired = state.active
    elif magnitude <= rule.recovery:
        desired = 0
    if desired == state.active:
        return MoveState(state.active, desired, stamp, state.last_alert_ms), None
    if desired != state.candidate:
        return MoveState(state.active, desired, stamp, state.last_alert_ms), None
    if stamp - state.since_ms < config.confirm_ms:
        return state, None
    if state.active == 0 and stamp - state.last_alert_ms < config.cooldown_ms:
        return state, None
    status = "RECOVERED" if desired == 0 else "ENTERED" if state.active == 0 else "ESCALATED"
    return MoveState(desired, desired, stamp, stamp), status


class RiskWatch:
    def __init__(
        self, outbox: Outbox, config: RiskWatchConfig, *, data_notifications: bool = True
    ) -> None:
        self.outbox, self.config = outbox, config
        self.data_notifications = data_notifications
        self.outbox.bind_channel("B")
        with outbox.connect() as db:
            bot_b_details.initialize(db)
            if config.reliability:
                bot_b_lifecycle.initialize(db)
            db.execute("PRAGMA max_page_count=8192")  # 32 MiB with SQLite's default 4 KiB pages.
            db.executescript("""
                CREATE TABLE IF NOT EXISTS risk_samples(
                    symbol TEXT, ms INTEGER, price TEXT, PRIMARY KEY(symbol,ms));
                CREATE TABLE IF NOT EXISTS risk_state(
                    symbol TEXT, window INTEGER, active INTEGER, candidate INTEGER,
                    since_ms INTEGER, last_alert_ms INTEGER, PRIMARY KEY(symbol,window));
                CREATE TABLE IF NOT EXISTS risk_health(
                    symbol TEXT PRIMARY KEY, failures INTEGER, alerted INTEGER);
                CREATE TABLE IF NOT EXISTS bot_b_feed_recovery(
                    symbol TEXT PRIMARY KEY, since_ms INTEGER, last_ms INTEGER);
                CREATE TABLE IF NOT EXISTS bot_b_price_warmup(
                    symbol TEXT PRIMARY KEY, since_ms INTEGER);
            """)
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT value FROM notification_meta WHERE key='risk_config'"
            ).fetchone()
            if previous and previous[0] != config.fingerprint:
                raise ValueError("Risk configuration changed; use reviewed new state database")
            db.execute(
                "INSERT OR IGNORE INTO notification_meta VALUES ('risk_config',?)",
                (config.fingerprint,),
            )
            if not data_notifications:
                # Preserve history while preventing pre-restart diagnostics from being sent.
                db.execute(
                    "UPDATE notification_outbox SET state='suppressed' WHERE state='pending' "
                    "AND event_id LIKE 'engine:bot-b:%' "
                    "AND json_extract(body,'$.status') IN "
                    "('DATA_UNAVAILABLE','DATA_RECOVERED','DATA_GAP_REWARMING')"
                )

    @staticmethod
    def event(
        symbol: str,
        stamp: int,
        status: str,
        details: dict,
        *,
        key: str,
        critical: bool = False,
        system: bool = False,
    ) -> NotificationEvent:
        return NotificationEvent(
            f"engine:bot-b:{key}:{symbol}:{stamp}",
            EventType.SYSTEM if system else EventType.MARKET,
            "okx-spot",
            "btc-eth-price-move-v1",
            stamp,
            symbol,
            status,
            {"bot_channel": "B", "expires_ms": stamp + 300000, **details},
            Priority.CRITICAL if critical else Priority.WARNING,
            mode="MONITOR",
        )

    def record(self, book: OrderBook, now_ms: int) -> int:
        book.validate_at(now_ms, self.config.max_age_ms)
        instrument, stamp = book.instrument, book.source_ms
        if (
            instrument.venue != "okx"
            or instrument.kind != MarketKind.SPOT
            or instrument.symbol not in self.config.symbols
        ):
            raise DataError("Risk watch received unexpected instrument")
        symbol = instrument.symbol
        price = (book.bids[0].price + book.asks[0].price) / 2
        with self.outbox.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            last = db.execute(
                "SELECT MAX(ms) FROM risk_samples WHERE symbol=?", (symbol,)
            ).fetchone()[0]
            if last is not None and stamp <= last:
                return 0  # Duplicate/out-of-order samples never extend confirmation time.
            count = 0
            if last is not None and stamp - last > self.config.max_gap_ms:
                db.execute("DELETE FROM risk_samples WHERE symbol=?", (symbol,))
                if self.config.reliability:
                    for rule in self.config.rules:
                        bot_b_lifecycle.stale(
                            db, f"move:{symbol}:{rule.window_ms}", stamp, "price_source_gap"
                        )
                    db.execute(
                        "UPDATE risk_state SET candidate=active,since_ms=0 WHERE symbol=?",
                        (symbol,),
                    )
                else:
                    db.execute("DELETE FROM risk_state WHERE symbol=?", (symbol,))
                warming = (
                    db.execute(
                        "SELECT since_ms FROM bot_b_price_warmup WHERE symbol=?", (symbol,)
                    ).fetchone()
                    if self.config.reliability
                    else None
                )
                if warming is None:
                    added = self.enqueue_in(
                        db,
                        self.event(
                            symbol,
                            stamp,
                            "DATA_GAP_REWARMING",
                            {"gap_ms": stamp - last, "last_valid_ms": last},
                            key="gap",
                            system=True,
                        ),
                    )
                    count += int(added)
                if self.config.reliability:
                    db.execute(
                        "INSERT OR IGNORE INTO bot_b_price_warmup VALUES (?,?)", (symbol, stamp)
                    )
            db.execute("INSERT INTO risk_samples VALUES (?,?,?)", (symbol, stamp, str(price)))
            retention = max(r.window_ms for r in self.config.rules) + self.config.max_gap_ms
            db.execute(
                "DELETE FROM risk_samples WHERE symbol=? AND ms<?", (symbol, stamp - retention)
            )
            db.execute(
                "DELETE FROM risk_samples WHERE symbol=? AND ms NOT IN "
                "(SELECT ms FROM risk_samples WHERE symbol=? ORDER BY ms DESC LIMIT 256)",
                (symbol, symbol),
            )
            for rule in self.config.rules:
                target = stamp - rule.window_ms
                baseline = db.execute(
                    "SELECT ms,price FROM risk_samples WHERE symbol=? AND ms<=? "
                    "ORDER BY ms DESC LIMIT 1",
                    (symbol, target),
                ).fetchone()
                if baseline is None or target - baseline[0] > self.config.max_gap_ms:
                    continue
                if rule.window_ms == max(r.window_ms for r in self.config.rules):
                    db.execute("DELETE FROM bot_b_price_warmup WHERE symbol=?", (symbol,))
                change = price / Decimal(baseline[1]) - 1
                prior = db.execute(
                    "SELECT active,candidate,since_ms,last_alert_ms FROM risk_state "
                    "WHERE symbol=? AND window=?",
                    (symbol, rule.window_ms),
                ).fetchone()
                previous = MoveState(*prior) if prior else MoveState()
                if self.config.reliability:
                    values, status, evidence = bot_b_lifecycle.evaluate(
                        db,
                        f"move:{symbol}:{rule.window_ms}",
                        "move",
                        symbol,
                        change,
                        stamp,
                        rule.threshold,
                        rule.recovery,
                        self.config.confirm_ms,
                        self.config.max_gap_ms,
                        str(rule.window_ms),
                    )
                    state = MoveState(*values)
                else:
                    state, status = transition(previous, change, rule, stamp, self.config)
                    evidence = bot_b_details.episode(
                        db,
                        f"move:{symbol}:{rule.window_ms}",
                        previous.active,
                        state.active,
                        status,
                        change,
                        stamp,
                    )
                db.execute(
                    "INSERT OR REPLACE INTO risk_state VALUES (?,?,?,?,?,?)",
                    (symbol, rule.window_ms, *asdict(state).values()),
                )
                if status:
                    event = self.event(
                        symbol,
                        stamp,
                        status,
                        {
                            "window_minutes": rule.window_ms // 60000,
                            "change_percent": change * 100,
                            "mid_price_usdt": price,
                            "baseline_price_usdt": Decimal(baseline[1]),
                            "baseline_ms": baseline[0],
                            "severity": abs(state.active),
                            "direction": "up" if change > 0 else "down",
                            "threshold_percent": rule.threshold * 100,
                            "confirmation_ms": self.config.confirm_ms,
                            **evidence,
                            "note": "行情观察，不是交易信号",
                        },
                        key=f"move:{rule.window_ms}:{status}",
                        critical=abs(state.active) == 2,
                    )
                    self.enqueue_in(db, event)
                    count += 1
        return count

    def feed_health(
        self,
        symbol: str,
        good: bool,
        stamp: int,
        *,
        source: str = "okx-spot",
        error_type: str = "数据缺失、过期或校验失败",
    ) -> None:
        with self.outbox.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT failures,alerted FROM risk_health WHERE symbol=?", (symbol,)
            ).fetchone()
            failures, alerted = row if row else (0, 0)
            recovering = False
            duration = self.config.reliability.data_recovery_ms if self.config.reliability else 0
            if duration and good and alerted:
                prior = db.execute(
                    "SELECT since_ms,last_ms FROM bot_b_feed_recovery WHERE symbol=?", (symbol,)
                ).fetchone()
                since = (
                    prior[0] if prior and 0 <= stamp - prior[1] <= self.config.max_gap_ms else stamp
                )
                recovering = stamp - since < duration
                db.execute(
                    "INSERT OR REPLACE INTO bot_b_feed_recovery VALUES (?,?,?)",
                    (symbol, since, stamp),
                )
            else:
                db.execute("DELETE FROM bot_b_feed_recovery WHERE symbol=?", (symbol,))
            details = bot_b_details.feed(db, symbol, good, stamp, recovering=recovering)
            failures = 0 if good else failures + 1
            if good and alerted and not recovering or not good and failures >= 3 and not alerted:
                self.enqueue_in(
                    db,
                    replace(
                        self.event(
                            symbol,
                            stamp,
                            "DATA_RECOVERED" if good else "DATA_UNAVAILABLE",
                            {
                                "consecutive_failures": failures,
                                "error_type": error_type,
                                **details,
                                "note": "数据缺口不视为正常行情",
                            },
                            key="health",
                            system=True,
                        ),
                        source=source,
                    ),
                )
                alerted = 0 if good else 1
            db.execute(
                "INSERT OR REPLACE INTO risk_health VALUES (?,?,?)", (symbol, failures, alerted)
            )

    def enqueue_in(self, db, event):
        if not self.data_notifications and event.status in {
            "DATA_UNAVAILABLE",
            "DATA_RECOVERED",
            "DATA_GAP_REWARMING",
        }:
            logging.log(
                logging.INFO if event.status == "DATA_RECOVERED" else logging.WARNING,
                "Bot B data status: %s %s; Telegram diagnostics disabled",
                event.symbol,
                event.status,
            )
            return False
        if not self.config.reliability:
            return self.outbox.enqueue_in(db, event)
        from crypto_market_monitor.bot_b_queue import enqueue_in

        return enqueue_in(db, event, self.config.reliability)

    def stale(self, key: str, stamp: int, reason: str) -> None:
        if self.config.reliability:
            with self.outbox.connect() as db:
                bot_b_lifecycle.stale(db, key, stamp, reason)

    def prune(self, now_ms: int) -> None:
        with self.outbox.connect() as db:
            if self.config.reliability:
                limit = self.config.reliability.history_limit
                db.execute(
                    "DELETE FROM bot_b_history WHERE event_id NOT IN "
                    "(SELECT event_id FROM bot_b_history ORDER BY ms DESC LIMIT ?)",
                    (limit,),
                )
                db.execute("DELETE FROM bot_b_notice_gate WHERE ms<?", (now_ms - 86400000,))
            db.execute(
                "UPDATE notification_outbox SET state='expired' WHERE state='pending' "
                "AND event_id LIKE 'engine:bot-b:%' "
                "AND json_extract(body,'$.details.expires_ms')<?",
                (now_ms,),
            )
            db.execute(
                "DELETE FROM notification_outbox WHERE state!='pending' AND event_id NOT IN "
                "(SELECT event_id FROM notification_outbox WHERE state!='pending' "
                "ORDER BY rowid DESC LIMIT 1000)"
            )

    def summary(self) -> dict:
        with self.outbox.connect() as db:
            samples = {
                row[0]: {"samples": row[1], "first_ms": row[2], "last_ms": row[3]}
                for row in db.execute(
                    "SELECT symbol,COUNT(*),MIN(ms),MAX(ms) FROM risk_samples GROUP BY symbol"
                )
            }
            queue = dict(
                db.execute("SELECT state,COUNT(*) FROM notification_outbox GROUP BY state")
            )
        return {"samples": samples, "outbox": queue}
