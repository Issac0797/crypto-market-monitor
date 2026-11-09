"""One reviewed public USDe spot market, without loading Bybit's full catalog."""

import asyncio
from typing import Any

from crypto_market_monitor.adapters.native_public import NativePublicAdapter, public_client
from crypto_market_monitor.market_data.models import Instrument
from crypto_market_monitor.models import DataError


async def build_compact_bybit(
    client: Any = None,
) -> tuple[NativePublicAdapter, tuple[Instrument, ...]]:
    client = public_client("bybit") if client is None else client
    adapter = NativePublicAdapter("bybit", client)
    try:
        client.options["fetchMarkets"] = {
            "types": ["spot"],
            "usePrivateInstrumentsInfo": False,
        }
        rows = await asyncio.wait_for(client.fetch_markets({"symbol": "USDEUSDT"}), 10)
        if (
            len(rows) != 1
            or rows[0].get("symbol") != "USDE/USDT"
            or rows[0].get("id") != "USDEUSDT"
            or rows[0].get("active") is not True
        ):
            raise DataError("Reviewed Bybit USDe spot market unavailable")
        client.set_markets(rows)
        return adapter, await adapter.discover(("USDE/USDT",))
    except BaseException:
        await adapter.close()
        raise
