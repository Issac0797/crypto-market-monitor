"""Deterministic basis/funding observations with durable, independent state.

Midpoint basis is a market-risk observation, not executable arbitrage profit.
Funding is the exchange forecast, linearly normalized to eight hours for comparison.
"""

from dataclasses import asdict, replace
from decimal import Decimal

from crypto_market_monitor import bot_details, bot_lifecycle
from crypto_market_monitor.market_data.models import FundingObservation, MarketKind, OrderBook
from crypto_market_monitor.market_data.risk_watch import MoveState, RiskWatch, transition
from crypto_market_monitor.market_data.risk_watch_config import MetricRule, MoveRule
from crypto_market_monitor.models import DataError


class DerivativeWatch:
    def __init__(self, watcher: RiskWatch) -> None:
        self.watcher, self.outbox, self.config = watcher, watcher.outbox, watcher.config
        with self.outbox.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS derivative_state(
                kind TEXT, symbol TEXT, last_ms INTEGER, value TEXT, context TEXT,
                active INTEGER, candidate INTEGER, since_ms INTEGER, last_alert_ms INTEGER,
                PRIMARY KEY(kind,symbol))""")
            if self.config.usde:
                db.execute("""CREATE TABLE IF NOT EXISTS derivative_source_versions(
                    kind TEXT, symbol TEXT, source TEXT, ms INTEGER, digest TEXT,
                    PRIMARY KEY(kind,symbol,source))""")

    def invalidate(self, kind: str, symbol: str, now_ms: int | None = None) -> None:
        """Unknown data cancels pending confirmation, never invents a recovery."""
        with self.outbox.connect() as db:
            if self.config.reliability:
                prior = db.execute(
                    "SELECT last_ms FROM derivative_state WHERE kind=? AND symbol=?", (kind, symbol)
                ).fetchone()
                stamp = now_ms if now_ms is not None else prior[0] if prior else 1
                bot_lifecycle.stale(db, f"{kind}:{symbol}", stamp, "invalid_or_missing_data")
            db.execute(
                "UPDATE derivative_state SET candidate=active,since_ms=0 WHERE kind=? AND symbol=?",
                (kind, symbol),
            )

    def _record(
        self,
        kind: str,
        symbol: str,
        value: Decimal,
        stamp: int,
        context: str,
        rule: MetricRule,
        evidence: dict,
        *,
        source: str = "okx-public",
        source_versions: tuple[tuple[str, int, str], ...] = (),
    ) -> int:
        with self.outbox.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed_source = False
            for identity, source_ms, digest in source_versions:
                prior = db.execute(
                    "SELECT ms,digest FROM derivative_source_versions "
                    "WHERE kind=? AND symbol=? AND source=?",
                    (kind, symbol, identity),
                ).fetchone()
                if prior and source_ms < prior[0]:
                    raise DataError("Composite quote leg moved backwards in source time")
                if prior and source_ms == prior[0] and digest != prior[1]:
                    raise DataError("Composite quote leg changed without a new source timestamp")
                changed_source = changed_source or prior is None or source_ms > prior[0]
            row = db.execute(
                "SELECT last_ms,value,context,active,candidate,since_ms,last_alert_ms "
                "FROM derivative_state WHERE kind=? AND symbol=?",
                (kind, symbol),
            ).fetchone()
            state = MoveState(*row[3:]) if row else MoveState()
            if row:
                if stamp < row[0] or stamp == row[0] and not changed_source:
                    if (
                        kind in {"funding", "stablecoin", "usde"}
                        and stamp == row[0]
                        and value != Decimal(row[1])
                    ):
                        raise DataError("Value changed without a new source timestamp")
                    return 0
                if stamp - row[0] > rule.max_gap_ms or context != row[2]:
                    state = replace(state, candidate=state.active, since_ms=0)
            # Invalidated or interrupted candidates must start from the next real observation.
            if state.since_ms == 0:
                state = replace(state, since_ms=stamp)
            previous = state
            if self.config.reliability:
                values, status, episode = bot_lifecycle.evaluate(
                    db,
                    f"{kind}:{symbol}",
                    kind,
                    symbol,
                    value,
                    stamp,
                    rule.threshold,
                    rule.recovery,
                    rule.confirm_ms,
                    rule.max_gap_ms,
                    context,
                    allow_equal_stamp=changed_source,
                )
                state = MoveState(*values)
            else:
                state, status = transition(
                    state,
                    value,
                    MoveRule(0, rule.threshold, rule.recovery),
                    stamp,
                    replace(self.config, confirm_ms=rule.confirm_ms),
                )
                episode = bot_details.episode(
                    db, f"{kind}:{symbol}", previous.active, state.active, status, value, stamp
                )
            db.execute(
                "INSERT OR REPLACE INTO derivative_state VALUES (?,?,?,?,?,?,?,?,?)",
                (kind, symbol, stamp, str(value), context, *asdict(state).values()),
            )
            for identity, source_ms, digest in source_versions:
                db.execute(
                    "INSERT OR REPLACE INTO derivative_source_versions VALUES (?,?,?,?,?)",
                    (kind, symbol, identity, source_ms, digest),
                )
            if not status:
                return 0
            event = replace(
                self.watcher.event(
                    symbol,
                    stamp,
                    f"{kind.upper()}_{status}",
                    {
                        "value_percent": value * 100,
                        "threshold_percent": rule.threshold * 100,
                        "confirmation_ms": rule.confirm_ms,
                        **episode,
                        "severity": abs(state.active),
                        "direction": "positive" if value >= 0 else "negative",
                        **evidence,
                    },
                    key=f"{kind}:{status}",
                    critical=abs(state.active) == 2,
                ),
                source=source,
                strategy=(
                    "usde-peg-v1"
                    if kind == "usde"
                    else "stablecoin-depeg-v1"
                    if kind == "stablecoin"
                    else f"btc-eth-{kind}-v1"
                ),
            )
            self.watcher.enqueue_in(db, event)
            return 1

    def record_basis(self, spot: OrderBook, perp: OrderBook, now_ms: int) -> int:
        rule = self.config.basis
        if rule is None:
            return 0
        spot.validate_at(now_ms, self.config.max_age_ms)
        perp.validate_at(now_ms, self.config.max_age_ms)
        left, right = spot.instrument, perp.instrument
        if (
            left.venue != "okx"
            or right.venue != left.venue
            or left.kind != MarketKind.SPOT
            or right.kind != MarketKind.PERPETUAL
            or left.symbol not in self.config.symbols
            or right.symbol != f"{left.symbol}:USDT"
            or right.base != left.base
            or left.quote != "USDT"
            or right.quote != left.quote
            or right.settlement != "USDT"
        ):
            raise DataError("Basis instrument identity or quotation mismatch")
        if abs(spot.source_ms - perp.source_ms) > rule.max_skew_ms:
            raise DataError("Basis source timestamps too far apart")
        depths = []
        for book in (spot, perp):
            multiplier = book.instrument.contract_size or Decimal(1)
            for side in (book.bids, book.asks):
                depth = sum((v.price * v.quantity * multiplier for v in side), Decimal(0))
                if depth < rule.min_depth_usdt:
                    raise DataError("Insufficient two-leg book depth for basis observation")
                depths.append(depth)
        spot_mid = (spot.bids[0].price + spot.asks[0].price) / 2
        perp_mid = (perp.bids[0].price + perp.asks[0].price) / 2
        value = perp_mid / spot_mid - 1
        return self._record(
            "basis",
            left.symbol,
            value,
            min(spot.source_ms, perp.source_ms),
            f"{left.key}|{right.key}|{right.contract_size}",
            rule,
            {
                "spot_mid_usdt": spot_mid,
                "perp_mid_usdt": perp_mid,
                "spot_source_ms": spot.source_ms,
                "perp_source_ms": perp.source_ms,
                "minimum_side_depth_usdt": min(depths),
                "note": "永续/现货中价基差，未扣费用，不是可实现套利收益",
            },
        )

    def record_funding(self, observation: FundingObservation, now_ms: int) -> int:
        rule = self.config.funding
        if rule is None:
            return 0
        observation.validate_at(now_ms, self.config.max_age_ms)
        instrument = observation.instrument
        if (
            instrument.venue != "okx"
            or instrument.symbol not in {f"{s}:USDT" for s in self.config.symbols}
            or instrument.quote != "USDT"
            or instrument.settlement != "USDT"
        ):
            raise DataError("Funding watch instrument mismatch")
        return self._record(
            "funding",
            instrument.symbol,
            observation.equivalent_rate(28800000),
            observation.source_ms,
            f"{instrument.key}|{observation.interval_ms}",
            rule,
            {
                "raw_rate_percent": observation.rate * 100,
                "actual_interval_hours": Decimal(observation.interval_ms) / 3600000,
                "next_settlement_ms": observation.next_settlement_ms,
                "positive_rate": "多头支付空头" if observation.rate >= 0 else "空头支付多头",
                "note": "当前资金费预估；8小时线性等效仅用于比较，不是已结算或保证收益",
            },
        )

    def summary(self) -> dict:
        with self.outbox.connect() as db:
            return {
                f"{r[0]}:{r[1]}": {"source_ms": r[2], "value": r[3], "active": r[4]}
                for r in db.execute("SELECT kind,symbol,last_ms,value,active FROM derivative_state")
            }
