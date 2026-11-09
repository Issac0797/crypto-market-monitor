"""Public fiat USD books; retain only reviewed stablecoin market metadata."""

import asyncio
from dataclasses import replace
from typing import Any

from crypto_market_monitor.adapters.native_public import NativePublicAdapter, public_client
from crypto_market_monitor.market_data.models import Instrument, OrderBook
from crypto_market_monitor.models import DataError

STABLE_SYMBOLS = ("USDT/USD", "USDC/USD")


class BitstampUsdAdapter(NativePublicAdapter):
    async def fetch_book(self, instrument: Instrument) -> OrderBook:
        book = await super().fetch_book(instrument)
        # Bitstamp ignores the unified limit argument; retain only 20 levels.
        return replace(book, bids=book.bids[:20], asks=book.asks[:20])


async def build_compact_bitstamp(
    client: Any = None,
) -> tuple[NativePublicAdapter, tuple[Instrument, ...]]:
    client = public_client("bitstamp") if client is None else client
    adapter = BitstampUsdAdapter("bitstamp", client)
    try:
        # Bitstamp's unified discovery endpoint has no per-pair filter. Discard
        # the temporary catalog before creating the two-market metadata cache.
        rows = await asyncio.wait_for(client.fetch_markets(), 10)
        selected = [r for r in rows if r.get("symbol") in STABLE_SYMBOLS]
        if len(selected) != 2 or {r["symbol"] for r in selected} != set(STABLE_SYMBOLS):
            raise DataError("Reviewed Bitstamp USD markets unavailable")
        client.set_markets(selected)
        instruments = await adapter.discover(STABLE_SYMBOLS)
        if any(i.quote != "USD" or i.base != i.symbol.split("/")[0] for i in instruments):
            raise DataError("Stablecoin fiat identity mismatch")
        return adapter, instruments
    except BaseException:
        await adapter.close()
        raise
