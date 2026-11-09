"""Credential-free HTTP/WS transport restricted to reviewed public endpoints."""

import asyncio
import json
import time
from decimal import Decimal
from typing import Any

import aiohttp

from crypto_market_monitor.models import DataError

ENDPOINTS = {
    "binance_spot": (
        "https://api.binance.com",
        frozenset(
            {
                "/api/v3/exchangeInfo",
                "/api/v3/depth",
                "/api/v3/ticker/24hr",
                "/api/v3/klines",
            }
        ),
    ),
    "binance_perp": (
        "https://fapi.binance.com",
        frozenset(
            {
                "/fapi/v1/exchangeInfo",
                "/fapi/v1/depth",
                "/fapi/v1/ticker/bookTicker",
                "/fapi/v1/premiumIndex",
                "/fapi/v1/fundingInfo",
                "/fapi/v1/fundingRate",
                "/fapi/v1/klines",
            }
        ),
    ),
    "okx": (
        "https://www.okx.com",
        frozenset(
            {
                "/api/v5/public/instruments",
                "/api/v5/market/books",
                "/api/v5/public/funding-rate",
            }
        ),
    ),
    "kraken": (
        "https://api.kraken.com",
        frozenset(
            {
                "/0/public/AssetPairs",
                "/0/public/Depth",
            }
        ),
    ),
    "bitstamp": (
        "https://www.bitstamp.net",
        frozenset(
            {
                "/api/v2/trading-pairs-info/",
                "/api/v2/order_book/usdtusd/",
                "/api/v2/order_book/usdcusd/",
            }
        ),
    ),
    "bybit": (
        "https://api.bybit.com",
        frozenset(
            {
                "/v5/market/instruments-info",
                "/v5/market/orderbook",
            }
        ),
    ),
    "hyperliquid": ("https://api.hyperliquid.xyz", frozenset({"/info"})),
}
WS_ENDPOINTS = frozenset(
    {
        "wss://ws.okx.com:8443/ws/v5/public",
        "wss://ws.kraken.com/v2",
        "wss://api.hyperliquid.xyz/ws",
        "wss://stream.binance.com:9443/ws",
        "wss://fstream.binance.com/ws",
    }
)
PARAMETERS = {
    "/api/v3/exchangeInfo": {"symbol"},
    "/api/v3/depth": {"symbol", "limit"},
    "/api/v3/ticker/24hr": {"symbol"},
    "/api/v3/klines": {"symbol", "interval", "startTime", "endTime", "limit"},
    "/fapi/v1/depth": {"symbol", "limit"},
    "/fapi/v1/ticker/bookTicker": {"symbol"},
    "/fapi/v1/premiumIndex": {"symbol"},
    "/fapi/v1/fundingRate": {"symbol", "startTime", "endTime", "limit"},
    "/fapi/v1/klines": {"symbol", "interval", "startTime", "endTime", "limit"},
    "/api/v5/public/instruments": {"instType", "instId"},
    "/api/v5/market/books": {"instId", "sz"},
    "/api/v5/public/funding-rate": {"instId"},
    "/0/public/AssetPairs": {"pair"},
    "/0/public/Depth": {"pair", "count"},
    "/v5/market/instruments-info": {"category", "symbol"},
    "/v5/market/orderbook": {"category", "symbol", "limit"},
    "/info": {"type", "coin"},
}


class NetworkError(OSError):
    """Transient public transport failure; carries no raw response or credentials."""


class RateLimitExceeded(NetworkError):
    pass


def loads(value: str) -> Any:
    return json.loads(value, parse_float=Decimal)


class PublicTransport:
    def __init__(self, timeout_ms: int = 8000) -> None:
        self.timeout = timeout_ms / 1000
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._next_request = 0.0
        self._blocked_until = 0.0

    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                trust_env=True,
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        return self._session

    async def request(self, venue: str, path: str, params: dict | None = None) -> Any:
        base, paths = ENDPOINTS[venue]
        if path not in paths:
            raise ValueError("Unreviewed public endpoint")
        params = params or {}
        if params.keys() - PARAMETERS.get(path, set()):
            raise ValueError("Unreviewed public endpoint parameter")
        if any(
            k.lower()
            in {
                "apikey",
                "signature",
                "secret",
                "brokerid",
                "broker",
                "affiliate",
                "referral",
                "tag",
                "clientorderid",
                "newclientorderid",
            }
            for k in params
        ):
            raise ValueError("Credentials and attribution parameters are forbidden")
        is_info = venue == "hyperliquid"
        if is_info and (
            path != "/info"
            or params.get("type")
            not in {
                "meta",
                "spotMeta",
                "metaAndAssetCtxs",
                "l2Book",
            }
        ):
            raise ValueError("Unreviewed info request")
        # Shared pacing applies even with Bot B's bounded concurrent reads.
        async with self._lock:
            await asyncio.sleep(
                max(0, max(self._next_request, self._blocked_until) - time.monotonic())
            )
            self._next_request = time.monotonic() + 0.12
        try:
            async with self.session().request(
                "POST" if is_info else "GET",
                base + path,
                params=None if is_info else params,
                json=params if is_info else None,
                allow_redirects=False,
            ) as response:
                if response.status in {418, 429}:
                    try:
                        delay = float(response.headers.get("Retry-After", "1"))
                    except ValueError:
                        delay = 1.0
                    self._blocked_until = time.monotonic() + min(max(delay, 1), 120)
                    raise RateLimitExceeded("Public endpoint rate limited")
                if response.status >= 500:
                    raise NetworkError("Public endpoint unavailable")
                if response.status != 200:
                    raise DataError(f"Public endpoint HTTP {response.status}")
                if response.content_length and response.content_length > 8_000_000:
                    raise DataError("Public response too large")
                chunks, count = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    count += len(chunk)
                    if count > 8_000_000:
                        raise DataError("Public response too large")
                    chunks.append(chunk)
                body = b"".join(chunks)
                try:
                    return loads(body.decode("utf-8"))
                except (ValueError, UnicodeError) as exc:
                    raise DataError("Malformed public JSON") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise NetworkError("Public request failed") from exc

    async def connect(self, url: str) -> aiohttp.ClientWebSocketResponse:
        if url not in WS_ENDPOINTS:
            raise ValueError("Unreviewed public websocket")
        try:
            return await self.session().ws_connect(url, heartbeat=20, max_msg_size=2_000_000)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise NetworkError("Public websocket connection failed") from exc

    async def close(self) -> None:
        if self._session:
            await self._session.close()
