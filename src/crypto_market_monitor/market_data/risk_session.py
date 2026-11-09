"""Coordinate independent price, basis and funding data-quality gates."""

from crypto_market_monitor.adapters.compact_bitstamp import STABLE_SYMBOLS
from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.funding_stream import FundingStream
from crypto_market_monitor.market_data.models import MarketKind
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.scanner import ScanReport
from crypto_market_monitor.market_data.stablecoin_watch import record_stablecoin
from crypto_market_monitor.market_data.usde_watch import record_usde
from crypto_market_monitor.models import DataError


class RiskSession:
    def __init__(self, watcher: RiskWatch, stream: FundingStream | None, started_ms: int) -> None:
        self.watcher, self.config, self.stream, self.started_ms = (
            watcher,
            watcher.config,
            stream,
            started_ms,
        )
        self.metrics = DerivativeWatch(watcher)
        with watcher.outbox.connect() as db:
            self._stable_venues = {
                symbol: context.split(":", 1)[0]
                for symbol, context in db.execute(
                    "SELECT symbol,context FROM derivative_state WHERE kind='stablecoin'"
                )
            }

    def process(
        self,
        scan: ScanReport,
        now_ms: int,
        stable_books: dict | None = None,
        usde_books: dict | None = None,
        stable_fallback: dict | None = None,
        stable_errors: dict | None = None,
    ) -> dict:
        books = {b.instrument.symbol: b for b in scan.observations.books}

        def failure_reason(symbol: str, *, perpetual: bool = False) -> str:
            native = symbol.replace("/", "-") + ("-SWAP" if perpetual else "")
            return (
                "; ".join(
                    reason
                    for key, reason in scan.failures
                    if key.startswith(f"okx:{'perpetual' if perpetual else 'spot'}:{native}:")
                )
                or "missing_or_invalid_quote"
            )

        basis_status, funding_status = {}, {}
        for symbol in self.config.symbols:
            spot, perp = books.get(symbol), books.get(f"{symbol}:USDT")
            if spot is not None and spot.instrument.kind == MarketKind.SPOT:
                self.watcher.record(spot, now_ms)
            if spot is None:
                for rule in self.config.rules:
                    self.watcher.stale(
                        f"move:{symbol}:{rule.window_ms}", now_ms, "missing_price_data"
                    )
            self.watcher.feed_health(
                symbol, spot is not None, now_ms, error_type=failure_reason(symbol)
            )
            if self.config.basis:
                good = False
                try:
                    if spot is None or perp is None:
                        raise DataError("Missing basis leg")
                    self.metrics.record_basis(spot, perp, now_ms)
                    good = True
                except DataError as exc:
                    self.metrics.invalidate("basis", symbol, now_ms)
                    basis_reason = str(exc)
                basis_status[symbol] = "fresh" if good else "unavailable"
                self.watcher.feed_health(
                    f"basis:{symbol}",
                    good,
                    now_ms,
                    source="okx-public",
                    error_type=""
                    if good
                    else (
                        f"{basis_reason}; spot={failure_reason(symbol)}; "
                        f"perpetual={failure_reason(symbol, perpetual=True)}"
                    ),
                )
            if self.config.funding:
                funding_status[f"{symbol}:USDT"] = self._funding(f"{symbol}:USDT", now_ms)
        stable_status, stable_details = {}, {}
        if self.config.stablecoin:
            for symbol in STABLE_SYMBOLS:
                good = False
                candidates = [
                    b
                    for b in ((stable_books or {}).get(symbol), (stable_fallback or {}).get(symbol))
                    if b is not None
                ]
                candidates.sort(
                    key=lambda b: b.instrument.venue != self._stable_venues.get(symbol, "bitstamp")
                )
                reasons = []
                source = "bitstamp-public"
                for book in candidates:
                    try:
                        record_stablecoin(self.metrics, book, now_ms)
                        good = True
                        self._stable_venues[symbol] = book.instrument.venue
                        source = f"{book.instrument.venue}-public"
                        stable_details[symbol] = {
                            "source": source,
                            "source_ms": book.source_ms,
                            "age_ms": now_ms - book.source_ms,
                        }
                        break
                    except DataError as exc:
                        reasons.append(f"{book.instrument.venue}: {exc}")
                if not good:
                    self.metrics.invalidate("stablecoin", symbol, now_ms)
                    reason = "; ".join(reasons) or (stable_errors or {}).get(
                        symbol, "missing_quote"
                    )
                    stable_details[symbol] = {"reason": reason}
                    if (
                        symbol == "USDC/USD"
                        and self.config.reliability
                        and self.config.reliability.usdc_fallback
                    ):
                        source = "bitstamp / kraken"
                stable_status[symbol] = "fresh" if good else "unavailable"
                self.watcher.feed_health(
                    f"stablecoin:{symbol}",
                    good,
                    now_ms,
                    source=source,
                    error_type=stable_details[symbol].get("reason", ""),
                )
        usde_status = {"status": "disabled"}
        if self.config.usde:
            c, ub = self.config.usde, usde_books or {}
            try:
                usde_status = record_usde(
                    self.metrics,
                    ub.get(c.direct_symbol),
                    ub.get(c.cross_symbol),
                    (stable_books or {}).get(c.conversion_symbol),
                    now_ms,
                )
                self.watcher.feed_health(
                    "usde:USDE/USD", True, now_ms, source=usde_status["source"]
                )
            except DataError as exc:
                self.metrics.invalidate("usde", "USDE/USD", now_ms)
                usde_status = {"status": "unavailable", "reason": str(exc)}
                self.watcher.feed_health(
                    "usde:USDE/USD",
                    False,
                    now_ms,
                    source=" / ".join(
                        dict.fromkeys((c.direct_venue, c.cross_venue, c.conversion_venue))
                    ),
                    error_type=str(exc),
                )
        good = all(s in books for s in self.config.symbols)
        good = good and usde_status["status"] in {"disabled", "fresh"}
        good = good and all(v == "fresh" for v in stable_status.values())
        good = good and all(v == "fresh" for v in basis_status.values())
        good = good and all(v["status"] == "fresh" for v in funding_status.values())
        return {
            "status": "sampling" if good else "sampling_partial",
            "valid_symbols": sorted(books),
            "basis_feed": basis_status,
            "funding_feed": funding_status,
            "stablecoin_feed": stable_status,
            "stablecoin_details": stable_details,
            "usde_feed": usde_status,
            "derivatives": self.metrics.summary(),
        }

    def _funding(self, symbol: str, now_ms: int) -> dict:
        rule = self.config.funding
        observation = self.stream.latest.get(symbol) if self.stream else None
        information = {"status": "awaiting_publication"}
        if observation:
            information.update(
                {
                    "source_ms": observation.source_ms,
                    "received_ms": observation.received_ms,
                    "raw_rate": str(observation.rate),
                    "interval_ms": observation.interval_ms,
                    "next_settlement_ms": observation.next_settlement_ms,
                }
            )
            age = now_ms - observation.source_ms
            if (
                self.config.max_age_ms < age <= rule.max_gap_ms
                and observation.source_ms <= observation.received_ms <= now_ms
                and now_ms - observation.received_ms <= rule.max_gap_ms
                and observation.next_settlement_ms > now_ms
                and observation.interval_ms is not None
            ):
                # Waiting for another publication is source health, not a fresh
                # risk observation. Keep recovery confirmation moving without
                # feeding an old rate back into the risk state machine.
                self.watcher.feed_health(f"funding:{symbol}", True, now_ms, source="okx-perpetual")
                information["status"] = "waiting_for_fresh_publication"
                return information
            try:
                self.metrics.record_funding(observation, now_ms)
                information["status"] = "fresh"
                self.watcher.feed_health(f"funding:{symbol}", True, now_ms, source="okx-perpetual")
                return information
            except DataError as exc:
                self.metrics.invalidate("funding", symbol, now_ms)
                self.watcher.feed_health(
                    f"funding:{symbol}",
                    False,
                    now_ms,
                    source="okx-perpetual",
                    error_type=str(exc),
                )
                information["status"] = "unavailable"
                return information
        if (
            self.stream
            and symbol in self.stream.errors
            or now_ms - (observation.source_ms if observation else self.started_ms)
            > rule.max_gap_ms
        ):
            self.metrics.invalidate("funding", symbol, now_ms)
            self.watcher.feed_health(
                f"funding:{symbol}",
                False,
                now_ms,
                source="okx-perpetual",
                error_type=getattr(self.stream, "reasons", {}).get(
                    symbol, "missing_or_expired_funding_publication"
                ),
            )
            information["status"] = "unavailable"
        return information
