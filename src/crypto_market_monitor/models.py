"""Exchange-independent immutable market data."""

from dataclasses import dataclass
from decimal import Decimal


class DataError(ValueError):
    """Market data is unsafe or incomplete."""


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    timestamp_ms: int

    def __post_init__(self) -> None:
        if not all(p.is_finite() and p > 0 for p in (self.bid, self.ask)):
            raise DataError("Prices must be finite and positive")
        if self.bid > self.ask or self.timestamp_ms <= 0:
            raise DataError("Crossed book or invalid timestamp")

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Funding:
    rate: Decimal
    timestamp_ms: int
    settlement_ms: int
    interval_ms: int
    mark_price: Decimal

    def __post_init__(self) -> None:
        if not self.rate.is_finite() or abs(self.rate) > 1:
            raise DataError("Funding rate outside supported range [-1, 1]")
        if not self.mark_price.is_finite() or self.mark_price <= 0:
            raise DataError("Mark price must be finite and positive")
        if min(self.timestamp_ms, self.settlement_ms, self.interval_ms) <= 0:
            raise DataError("Invalid funding timestamps or interval")


@dataclass(frozen=True)
class Snapshot:
    exchange: str
    spot: Quote
    perpetual: Quote
    funding: Funding
    collected_ms: int
    mark_timestamp_ms: int

    def validate(self, now_ms: int, max_age_ms: int, max_skew_ms: int) -> None:
        timestamps = [
            self.spot.timestamp_ms,
            self.perpetual.timestamp_ms,
            self.funding.timestamp_ms,
            self.mark_timestamp_ms,
            self.collected_ms,
        ]
        if any(t > now_ms or now_ms - t > max_age_ms for t in timestamps):
            raise DataError("Stale or future-dated snapshot")
        if max(timestamps) - min(timestamps) > max_skew_ms:
            raise DataError("Snapshot components exceed allowed time skew")
        if self.funding.settlement_ms <= now_ms:
            raise DataError("Funding settlement time has passed; refresh data")
