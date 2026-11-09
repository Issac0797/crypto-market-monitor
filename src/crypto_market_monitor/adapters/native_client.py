"""Locally owned public exchange client. No exchange SDK or order/account API."""

from typing import Any

from crypto_market_monitor.adapters.native_http import PublicTransport
from crypto_market_monitor.adapters.native_markets import (
    bybit_result,
    fetch_markets,
    kraken_result,
    okx_data,
)
from crypto_market_monitor.adapters.native_streams import NativeStreams
from crypto_market_monitor.models import DataError


class NativePublicClient:
    def __init__(
        self,
        venue: str,
        *,
        websocket: bool = False,
        timeout_ms: int = 8000,
        transport: PublicTransport | None = None,
    ) -> None:
        self.venue = venue
        self.apiKey = None
        self.options = {
            "fetchCurrencies": False,
            "fetchMargins": False,
            "fetchMarkets": {
                "types": ["spot", "linear"]
                if venue == "binance"
                else ["spot", "swap"]
                if venue in {"okx", "hyperliquid"}
                else ["spot"]
            },
        }
        self.has = {
            "fetchOrderBook": True,
            "fetchFundingRate": venue in {"binance", "okx", "hyperliquid"},
            "watchOrderBook": websocket and venue in {"binance", "okx", "kraken", "hyperliquid"},
            "watchFundingRate": websocket and venue == "okx",
        }
        self.markets: dict[str, dict] = {}
        self.transport = transport if transport is not None else PublicTransport(timeout_ms)
        self.streams = NativeStreams(self.transport)

    async def fetch_markets(self, params: dict | None = None) -> list[dict]:
        try:
            return await fetch_markets(
                self.transport, self.venue, self.options["fetchMarkets"]["types"], params or {}
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DataError("Malformed native market metadata") from exc

    def set_markets(self, rows: list[dict]) -> None:
        markets = {r["symbol"]: r for r in rows}
        if len(markets) != len(rows):
            raise DataError("Duplicate native market identity")
        self.markets = markets

    async def load_markets(self, symbols: tuple[str, ...] | None = None) -> dict[str, dict]:
        needed = tuple(s for s in symbols if s not in self.markets) if symbols else None
        if not self.markets or needed:
            if symbols is None or self.venue == "hyperliquid":
                rows = await self.fetch_markets()
            else:
                rows = []
                for symbol in needed or symbols:
                    swap = ":" in symbol
                    pair = symbol.split(":")[0]
                    params = {}
                    if self.venue == "binance" and not swap:
                        params = {"symbol": pair.replace("/", "")}
                    elif self.venue == "okx":
                        params = {"instId": pair.replace("/", "-") + ("-SWAP" if swap else "")}
                    elif self.venue == "bybit":
                        params = {"symbol": pair.replace("/", "")}
                    elif self.venue == "kraken":
                        params = {"pair": pair.replace("/", "")}
                    kind = (
                        "linear" if swap and self.venue == "binance" else "swap" if swap else "spot"
                    )
                    parsed = await fetch_markets(self.transport, self.venue, [kind], params)
                    rows.extend(r for r in parsed if r["symbol"] == symbol)
            if symbols is not None:
                rows = [r for r in rows if r["symbol"] in (needed or symbols)]
            self.set_markets(
                [r for s, r in self.markets.items() if s not in {item["symbol"] for item in rows}]
                + rows
            )
        return self.markets

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> dict:
        m = self.markets[symbol]
        native_id = m["id"]
        get = self.transport.request
        try:
            if self.venue == "okx":
                rows = okx_data(
                    await get(
                        "okx", "/api/v5/market/books", {"instId": native_id, "sz": str(limit)}
                    )
                )
                if len(rows) != 1:
                    raise DataError("Expected one OKX book")
                r = rows[0]
                bids, asks, ts = r["bids"], r["asks"], r["ts"]
            elif self.venue == "binance":
                swap = not m["spot"]
                r = await get(
                    "binance_perp" if swap else "binance_spot",
                    "/fapi/v1/depth" if swap else "/api/v3/depth",
                    {"symbol": native_id, "limit": limit},
                )
                bids, asks, ts = r["bids"], r["asks"], r.get("E")
            elif self.venue == "bybit":
                r = bybit_result(
                    await self.publicGetV5MarketOrderbook(
                        {"category": "spot", "symbol": native_id, "limit": limit}
                    )
                )
                if r["s"] != native_id:
                    raise DataError("Bybit book identity mismatch")
                bids, asks, ts = r["b"], r["a"], r.get("ts")
            elif self.venue == "bitstamp":
                if native_id not in {"usdtusd", "usdcusd"}:
                    raise DataError("Unreviewed Bitstamp pair")
                r = await get("bitstamp", f"/api/v2/order_book/{native_id}/")
                from crypto_market_monitor.adapters.binance import _positive_int

                bids, asks = r["bids"], r["asks"]
                ts = _positive_int(r["microtimestamp"]) // 1000
            elif self.venue == "kraken":
                rows = kraken_result(
                    await get("kraken", "/0/public/Depth", {"pair": native_id, "count": limit})
                )
                if set(rows) != {native_id}:
                    raise DataError("Kraken book identity mismatch")
                r = rows[native_id]
                # Per-level last modification times do not timestamp this snapshot.
                bids, asks, ts = r["bids"], r["asks"], None
            elif self.venue == "hyperliquid":
                r = await get("hyperliquid", "/info", {"type": "l2Book", "coin": native_id})
                if r["coin"] != native_id:
                    raise DataError("Hyperliquid book identity mismatch")
                bids, asks = ([[p["px"], p["sz"]] for p in side] for side in r["levels"])
                ts = r["time"]
            else:
                raise DataError("Unsupported native order book")
            return {
                "symbol": symbol,
                "bids": [r[:2] for r in bids[:limit]],
                "asks": [r[:2] for r in asks[:limit]],
                "timestamp": ts,
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DataError("Malformed native order book") from exc

    async def fetch_funding_rate(self, symbol: str) -> dict:
        m = self.markets[symbol]
        if not self.has["fetchFundingRate"] or m.get("spot"):
            raise DataError("Unsupported native funding request")
        if self.venue == "okx":
            rows = okx_data(
                await self.transport.request(
                    "okx", "/api/v5/public/funding-rate", {"instId": m["id"]}
                )
            )
            if len(rows) != 1:
                raise DataError("Expected one OKX funding observation")
            return funding_row(symbol, m["id"], rows[0])
        if self.venue == "binance":
            r = await self.fapiPublicGetPremiumIndex({"symbol": m["id"]})
            if r["symbol"] != m["id"]:
                raise DataError("Binance funding identity mismatch")
            return {
                "symbol": symbol,
                "fundingRate": r["lastFundingRate"],
                "fundingTimestamp": r["nextFundingTime"],
                "timestamp": r["time"],
            }
        # metaAndAssetCtxs has no native publication timestamp. Do not invent one.
        raise DataError("Hyperliquid current funding source timestamp unavailable")

    async def watch_order_book(self, symbol: str) -> dict:
        if not self.has["watchOrderBook"]:
            raise DataError("Native order book subscription unavailable")
        return await self.streams.book(self.venue, self.markets[symbol])

    async def watch_funding_rate(self, symbol: str) -> dict:
        if not self.has["watchFundingRate"]:
            raise DataError("Native funding subscription unavailable")
        r = await self.streams.okx(self.markets[symbol], "funding-rate")
        return funding_row(symbol, self.markets[symbol]["id"], r)

    # Explicit legacy research endpoint names; each maps to one fixed public URL.
    async def public_get_ticker_24hr(self, params: dict) -> Any:
        return await self.transport.request("binance_spot", "/api/v3/ticker/24hr", params)

    async def public_get_klines(self, params: dict) -> Any:
        return await self.transport.request("binance_spot", "/api/v3/klines", params)

    async def fapiPublicGetTickerBookTicker(self, params: dict) -> Any:
        return await self.transport.request("binance_perp", "/fapi/v1/ticker/bookTicker", params)

    async def fapiPublicGetPremiumIndex(self, params: dict) -> Any:
        return await self.transport.request("binance_perp", "/fapi/v1/premiumIndex", params)

    async def fapiPublicGetFundingInfo(self) -> Any:
        return await self.transport.request("binance_perp", "/fapi/v1/fundingInfo")

    async def fapiPublicGetFundingRate(self, params: dict) -> Any:
        return await self.transport.request("binance_perp", "/fapi/v1/fundingRate", params)

    async def fapiPublicGetKlines(self, params: dict) -> Any:
        return await self.transport.request("binance_perp", "/fapi/v1/klines", params)

    async def publicGetV5MarketOrderbook(self, params: dict) -> Any:
        return await self.transport.request("bybit", "/v5/market/orderbook", params)

    async def close(self) -> None:
        await self.streams.close()
        await self.transport.close()


def funding_row(symbol: str, native_id: str, row: dict) -> dict:
    if row.get("instId") != native_id:
        raise DataError("OKX funding identity mismatch")
    return {
        "symbol": symbol,
        "fundingRate": row["fundingRate"],
        "fundingTimestamp": row["fundingTime"],
        "timestamp": row.get("ts"),
        "info": row,
    }
