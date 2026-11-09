"""Local native public adapter; immutable models shared across reviewed venues."""

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from crypto_market_monitor.adapters.binance import _decimal, _positive_int
from crypto_market_monitor.adapters.okx_funding import normalize_okx_funding
from crypto_market_monitor.market_data.contracts import Capability, NativeSupplement
from crypto_market_monitor.market_data.models import (
    FundingObservation,
    Instrument,
    Level,
    MarketKind,
    OrderBook,
)
from crypto_market_monitor.market_data.venues import SUPPORTED_VENUES
from crypto_market_monitor.models import DataError


def public_client(venue: str, *, websocket: bool = False) -> Any:
    if venue not in SUPPORTED_VENUES:
        raise ValueError("Exchange requires an explicit public-adapter review")
    from crypto_market_monitor.adapters.native_client import NativePublicClient

    return NativePublicClient(venue, websocket=websocket)


def normalize_instrument(venue: str, row: dict) -> Instrument:
    try:
        if row.get("active") is not True:
            raise DataError("Market is not explicitly active")
        if row.get("spot") is True:
            kind = MarketKind.SPOT
        elif row.get("swap") is True and row.get("linear") is True:
            kind = MarketKind.PERPETUAL
        else:
            raise DataError("Only spot and linear perpetual markets are supported")
        return Instrument(
            venue,
            row["symbol"],
            row["id"],
            kind,
            row["base"],
            row["quote"],
            row["settle"] if kind == MarketKind.PERPETUAL else None,
            _decimal(row["contractSize"]) if kind == MarketKind.PERPETUAL else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError("Invalid native market metadata") from exc


class NativePublicAdapter:
    def __init__(
        self,
        venue: str,
        client: Any = None,
        supplement: NativeSupplement | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if venue not in SUPPORTED_VENUES:
            raise ValueError("Unsupported exchange")
        self.venue = venue
        self._client = client if client is not None else public_client(venue)
        # Even injected real clients must not carry credentials or allow private discovery.
        if any(
            getattr(self._client, key, None)
            for key in ("apiKey", "secret", "password", "privateKey")
        ):
            raise ValueError("Public adapter refuses credentials")
        self._client.options.update(fetchCurrencies=False, fetchMargins=False)
        if venue == "binance":
            self._client.options["fetchMarkets"] = {"types": ["spot", "linear"]}
        self._clock = clock or (lambda: time.time_ns() // 1_000_000)
        self._supplement = supplement
        self._instruments: dict[str, Instrument] = {}
        self.capabilities = frozenset(
            capability
            for capability, method in (
                (Capability.ORDER_BOOK, "fetchOrderBook"),
                (Capability.WATCH_ORDER_BOOK, "watchOrderBook"),
                (Capability.FUNDING, "fetchFundingRate"),
            )
            if self._client.has.get(method) is True
        )
        if self.venue == "okx" and self._client.has.get("watchFundingRate") is True:
            self.capabilities |= {Capability.WATCH_FUNDING}

    async def discover(self, symbols: tuple[str, ...]) -> tuple[Instrument, ...]:
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValueError("Explicit unique symbols required")
        from crypto_market_monitor.adapters.native_client import NativePublicClient

        markets = (
            await self._client.load_markets(symbols)
            if isinstance(self._client, NativePublicClient)
            else await self._client.load_markets()
        )
        selected = tuple(normalize_instrument(self.venue, markets[s]) for s in symbols)
        if any(
            instrument.symbol != symbol
            for instrument, symbol in zip(selected, symbols, strict=True)
        ):
            raise DataError("Market discovery symbol mismatch")
        self._instruments.update({i.key: i for i in selected})
        return selected

    def _check(self, instrument: Instrument, capability: Capability) -> None:
        if capability not in self.capabilities:
            raise DataError("Unsupported adapter capability")
        if self._instruments.get(instrument.key) != instrument:
            raise DataError("Instrument must match discovered exchange metadata")

    async def fetch_book(self, instrument: Instrument) -> OrderBook:
        self._check(instrument, Capability.ORDER_BOOK)
        row = await self._client.fetch_order_book(instrument.symbol, limit=20)
        return self._normalize_book(instrument, row, "native:fetch_order_book")

    async def watch_book(self, instrument: Instrument) -> OrderBook:
        self._check(instrument, Capability.WATCH_ORDER_BOOK)
        row = await self._client.watch_order_book(instrument.symbol)
        # Injected clients may first resolve an untimed REST seed. Await one actual
        # depth event; never stamp the seed with a receipt time or switch transport.
        if (
            self.venue == "binance"
            and instrument.kind == MarketKind.SPOT
            and row.get("timestamp") is None
        ):
            row = await self._client.watch_order_book(instrument.symbol)
            if row.get("timestamp") is None:
                raise DataError("Binance spot depth event source timestamp unavailable")
        return self._normalize_book(instrument, row, "native:watch_order_book")

    def _normalize_book(self, instrument: Instrument, row: dict, provenance: str) -> OrderBook:
        try:
            if row.get("symbol") != instrument.symbol:
                raise DataError("Native book symbol mismatch")
            return OrderBook(
                instrument,
                tuple(Level(_decimal(p[0]), _decimal(p[1])) for p in row["bids"]),
                tuple(Level(_decimal(p[0]), _decimal(p[1])) for p in row["asks"]),
                _positive_int(row["timestamp"]) if row.get("timestamp") is not None else None,
                self._clock(),
                provenance,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DataError("Invalid native order book") from exc

    async def watch_funding(self, instrument: Instrument) -> FundingObservation:
        """Explicit OKX public funding update; callers bound waits as for books."""
        self._check(instrument, Capability.WATCH_FUNDING)
        if instrument.kind != MarketKind.PERPETUAL:
            raise DataError("Funding requires perpetual market")
        row = await self._client.watch_funding_rate(instrument.symbol)
        observation = normalize_okx_funding(instrument, row, self._clock())
        return replace(
            observation,
            provenance="native:watch_funding_rate+native:okx_funding_fields",
        )

    async def fetch_funding(self, instrument: Instrument) -> FundingObservation:
        self._check(instrument, Capability.FUNDING)
        if instrument.kind != MarketKind.PERPETUAL:
            raise DataError("Funding requires perpetual market")
        row = await self._client.fetch_funding_rate(instrument.symbol)
        if self.venue == "okx":
            return normalize_okx_funding(instrument, row, self._clock())
        if self.venue == "hyperliquid" and row.get("timestamp") is None:
            raise DataError(
                "Hyperliquid current funding source timestamp unavailable; "
                "locally calculated settlement time is not source time"
            )
        try:
            if row["symbol"] != instrument.symbol:
                raise DataError("Funding symbol mismatch")
            interval = row.get("interval")
            interval_ms = None
            provenance = "native:fetch_funding_rate"
            if isinstance(interval, str) and interval.endswith("h"):
                interval_ms = _positive_int(interval[:-1]) * 3_600_000
            if interval_ms is None and self._supplement is not None:
                interval_ms = await self._supplement.funding_interval_ms(instrument)
                provenance += "+native:funding_interval"
            return FundingObservation(
                instrument,
                _decimal(row["fundingRate"]),
                interval_ms,
                _positive_int(row["fundingTimestamp"]),
                _positive_int(row["timestamp"]),
                self._clock(),
                provenance,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError("Invalid native funding observation") from exc

    async def close(self) -> None:
        await self._client.close()
