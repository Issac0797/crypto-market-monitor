"""Read-only adapter and detector contracts; execution is deliberately absent."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from crypto_market_monitor.market_data.models import FundingObservation, Instrument, OrderBook


class Capability(StrEnum):
    ORDER_BOOK = "order_book"
    WATCH_ORDER_BOOK = "watch_order_book"
    FUNDING = "funding"
    WATCH_FUNDING = "watch_funding"


class PublicAdapter(Protocol):
    venue: str
    capabilities: frozenset[Capability]

    async def fetch_book(self, instrument: Instrument) -> OrderBook: ...
    async def watch_book(self, instrument: Instrument) -> OrderBook: ...
    async def fetch_funding(self, instrument: Instrument) -> FundingObservation: ...
    async def watch_funding(self, instrument: Instrument) -> FundingObservation: ...
    async def close(self) -> None: ...


class NativeSupplement(Protocol):
    """Named exchange-specific gap only; never silently replace failed public requests."""

    async def funding_interval_ms(self, instrument: Instrument) -> int: ...


@dataclass(frozen=True)
class DataRequest:
    instrument: Instrument
    capability: Capability = Capability.ORDER_BOOK

    @property
    def key(self) -> str:
        return f"{self.instrument.key}:{self.capability}"


@dataclass(frozen=True)
class Finding:
    detector_id: str
    category: str  # opportunity or anomaly; both are observations, never orders
    summary: str
    evidence_keys: tuple[str, ...]


@dataclass(frozen=True)
class ObservationSet:
    observed_ms: int
    books: tuple[OrderBook, ...]
    funding: tuple[FundingObservation, ...]


class Detector(Protocol):
    detector_id: str
    required_keys: frozenset[str]

    def evaluate(self, observations: ObservationSet) -> tuple[Finding, ...]: ...
