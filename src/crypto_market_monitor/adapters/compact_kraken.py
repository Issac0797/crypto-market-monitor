"""Reviewed USDe/USDC public markets; never substitute receipt time for book time."""

import asyncio
from typing import Any

from crypto_market_monitor.adapters.native_public import NativePublicAdapter, public_client
from crypto_market_monitor.market_data.models import Instrument
from crypto_market_monitor.models import DataError


async def build_compact_kraken(
    symbols: tuple[str, ...],
    client: Any = None,
) -> tuple[NativePublicAdapter, tuple[Instrument, ...]]:
    if not symbols or not set(symbols) <= {"USDE/USD", "USDE/USDT", "USDC/USD"}:
        raise ValueError("Reviewed USDe/USDC market identities required")
    client = public_client("kraken", websocket=True) if client is None else client
    adapter = NativePublicAdapter("kraken", client)
    try:
        selected = []
        for symbol in symbols:
            try:
                rows = await asyncio.wait_for(
                    client.fetch_markets({"pair": symbol.replace("/", "")}), 10
                )
                matches = [r for r in rows if r.get("symbol") == symbol and r.get("active") is True]
                if len(matches) == 1:
                    selected.extend(matches)
            except Exception:
                continue  # A missing optional cross market must not disable direct USD.
        if not selected:
            raise DataError("No reviewed live USDe market available")
        client.set_markets(selected)
        instruments = await adapter.discover(tuple(r["symbol"] for r in selected))
        return adapter, instruments
    except BaseException:
        await adapter.close()
        raise
