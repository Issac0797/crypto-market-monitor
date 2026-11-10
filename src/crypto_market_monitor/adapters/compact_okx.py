"""Select public OKX spot metadata before loading the native client's unified adapter.

Only the two configured markets enter the native client's cache, instead of every venue asset.
All metadata/book parsing still belongs to the native client and NativePublicAdapter.
"""

import asyncio
import logging
from typing import Any

from crypto_market_monitor.adapters.native_public import NativePublicAdapter, public_client
from crypto_market_monitor.market_data.models import Instrument
from crypto_market_monitor.models import DataError


async def build_compact_okx(
    symbols: tuple[str, ...], client: Any = None, *, derivatives: bool = False
) -> tuple[NativePublicAdapter, tuple[Instrument, ...]]:
    if (
        not symbols
        or len(set(symbols)) != len(symbols)
        or not set(symbols) <= {"BTC/USDT", "ETH/USDT"}
    ):
        raise ValueError("Reviewed explicit spot symbols required")
    client = public_client("okx", websocket=derivatives) if client is None else client
    adapter = NativePublicAdapter("okx", client)
    try:
        instruments = await refresh_compact_okx(adapter, symbols, derivatives=derivatives)
        return adapter, instruments
    except BaseException:
        await adapter.close()
        raise


async def refresh_compact_okx(
    adapter: NativePublicAdapter, symbols: tuple[str, ...], *, derivatives: bool = False
) -> tuple[Instrument, ...]:
    """Refresh only reviewed identities, without loading the whole exchange catalog."""
    if not symbols or not set(symbols) <= {"BTC/USDT", "ETH/USDT"}:
        raise ValueError("Reviewed spot identities required")
    requested = symbols + tuple(f"{s}:USDT" for s in symbols) if derivatives else symbols
    selected = []
    client = adapter._client
    for symbol in requested:
        swap = symbol.endswith(":USDT")
        client.options["fetchMarkets"] = {"types": ["swap" if swap else "spot"]}
        native_id = symbol.split(":")[0].replace("/", "-") + ("-SWAP" if swap else "")
        rows = await asyncio.wait_for(client.fetch_markets({"instId": native_id}), 10)
        if len(rows) != 1 or rows[0].get("symbol") != symbol:
            raise DataError("Filtered OKX metadata identity mismatch")
        selected.extend(rows)
    client.options["fetchMarkets"] = {"types": ["spot", "swap"] if derivatives else ["spot"]}
    client.set_markets(selected)
    return await asyncio.wait_for(adapter.discover(requested), 10)


async def refresh_compact_okx_safely(
    adapter: NativePublicAdapter,
    symbols: tuple[str, ...],
    expected: tuple[Instrument, ...],
    *,
    derivatives: bool = False,
) -> bool:
    """Retry transient refresh failures without accepting changed market identities."""
    previous = adapter._client.options["fetchMarkets"]
    try:
        refreshed = await refresh_compact_okx(adapter, symbols, derivatives=derivatives)
    except (OSError, TimeoutError):
        logging.warning("Bot public metadata refresh unavailable; bounded retry pending")
        return False
    finally:
        adapter._client.options["fetchMarkets"] = previous
    if refreshed != expected:
        raise DataError("Market identity changed; restart and revalidate")
    return True
