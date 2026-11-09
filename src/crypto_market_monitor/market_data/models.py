"""Immutable normalized data. Asset identity and source time are never inferred."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from crypto_market_monitor.models import DataError


class MarketKind(StrEnum):
    SPOT = "spot"
    PERPETUAL = "perpetual"
    PREDICTION = "prediction"


def positive_ms(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise DataError("Positive integer UTC epoch milliseconds required")


def finite(value: Decimal, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or (positive and value <= 0):
        raise DataError("Finite Decimal required")


@dataclass(frozen=True)
class Instrument:
    venue: str
    symbol: str
    native_id: str
    kind: MarketKind
    base: str
    quote: str
    settlement: str | None = None
    contract_size: Decimal | None = None
    condition_id: str | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", MarketKind(self.kind))
        if any(
            not isinstance(v, str) or not v.strip()
            for v in (self.venue, self.symbol, self.native_id, self.base, self.quote)
        ):
            raise DataError("Complete market identity required")
        if self.kind == MarketKind.PERPETUAL:
            if not self.settlement or self.contract_size is None:
                raise DataError("Perpetual settlement and contract size required")
            finite(self.contract_size, positive=True)
        elif self.contract_size is not None or self.settlement is not None:
            raise DataError("Derivative fields on non-derivative instrument")
        if self.kind == MarketKind.PREDICTION:
            if not self.condition_id or not self.outcome or self.venue != "polymarket":
                raise DataError("Prediction condition and outcome required")
        elif self.condition_id is not None or self.outcome is not None:
            raise DataError("Prediction fields on CEX instrument")

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.kind}:{self.native_id}"

    @property
    def quantity_unit(self) -> str:
        return {
            MarketKind.SPOT: "base",
            MarketKind.PERPETUAL: "contracts",
            MarketKind.PREDICTION: "shares",
        }[self.kind]


@dataclass(frozen=True)
class Level:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        finite(self.price, positive=True)
        finite(self.quantity, positive=True)


@dataclass(frozen=True)
class OrderBook:
    instrument: Instrument
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    source_ms: int | None
    received_ms: int
    provenance: str

    def __post_init__(self) -> None:
        positive_ms(self.received_ms)
        if self.source_ms is not None:
            positive_ms(self.source_ms)
        if not isinstance(self.bids, tuple) or not isinstance(self.asks, tuple):
            raise DataError("Immutable book levels required")
        if not self.provenance or not self.bids or not self.asks:
            raise DataError("Two-sided book and provenance required")
        for levels, reverse in ((self.bids, True), (self.asks, False)):
            prices = [v.price for v in levels]
            if prices != sorted(set(prices), reverse=reverse):
                raise DataError("Unordered or duplicate price levels")
            if self.instrument.kind == MarketKind.PREDICTION and any(p > 1 for p in prices):
                raise DataError("Prediction price exceeds one collateral unit")
        if self.bids[0].price > self.asks[0].price:
            raise DataError("Crossed book")

    def validate_at(self, now_ms: int, max_age_ms: int) -> None:
        if self.source_ms is None:
            raise DataError("Source timestamp unavailable; receipt time is not market time")
        validate_times(self.source_ms, self.received_ms, now_ms, max_age_ms)


def validate_times(source_ms: int, received_ms: int, now_ms: int, max_age_ms: int) -> None:
    positive_ms(now_ms)
    if type(max_age_ms) is not int or max_age_ms <= 0:
        raise DataError("Positive freshness bound required")
    if source_ms > received_ms or not 0 <= now_ms - received_ms <= max_age_ms:
        raise DataError("Future or stale receipt time")
    if not 0 <= now_ms - source_ms <= max_age_ms:
        raise DataError("Future or stale source time")


@dataclass(frozen=True)
class FundingObservation:
    instrument: Instrument
    rate: Decimal
    interval_ms: int | None
    next_settlement_ms: int
    source_ms: int
    received_ms: int
    provenance: str

    def __post_init__(self) -> None:
        if self.instrument.kind != MarketKind.PERPETUAL:
            raise DataError("Funding requires a perpetual")
        finite(self.rate)
        if abs(self.rate) > 1 or not self.provenance:
            raise DataError("Unsupported funding observation")
        for value in (self.next_settlement_ms, self.source_ms, self.received_ms):
            positive_ms(value)
        if self.interval_ms is not None:
            positive_ms(self.interval_ms)

    def validate_at(self, now_ms: int, max_age_ms: int) -> None:
        validate_times(self.source_ms, self.received_ms, now_ms, max_age_ms)
        if self.next_settlement_ms <= now_ms or self.interval_ms is None:
            raise DataError("Funding cycle unknown or settlement passed")

    def equivalent_rate(self, interval_ms: int) -> Decimal:
        """Linear comparison assumption, never a forecast or realized funding payment."""
        positive_ms(interval_ms)
        if self.interval_ms is None:
            raise DataError("Funding interval unknown")
        return self.rate * Decimal(interval_ms) / Decimal(self.interval_ms)
