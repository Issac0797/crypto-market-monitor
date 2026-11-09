"""Bounded native public subscriptions with sequence/checksum validation."""

import asyncio
import zlib
from datetime import datetime
from decimal import Decimal

import aiohttp

from crypto_market_monitor.adapters.native_http import NetworkError, loads
from crypto_market_monitor.models import DataError


def source_iso_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise DataError("Source timestamp requires timezone")
    return int(parsed.timestamp() * 1000)


def apply_levels(state: dict, rows, *, objects: bool = False) -> None:
    for row in rows:
        p, q = (row["price"], row["qty"]) if objects else row[:2]
        price, size = Decimal(str(p)), Decimal(str(q))
        if not price.is_finite() or not size.is_finite() or price <= 0 or size < 0:
            raise DataError("Invalid native depth level")
        if size == 0:
            state.pop(price, None)
        else:
            state[price] = size


def levels(state: dict, reverse: bool, depth: int) -> list[list[Decimal]]:
    return [[p, state[p]] for p in sorted(state, reverse=reverse)[:depth]]


def kraken_checksum(bids: dict, asks: dict) -> int:
    def digits(value):
        return format(value, "f").replace(".", "").lstrip("0")

    content = "".join(
        digits(p) + digits(q)
        for side, reverse in ((asks, False), (bids, True))
        for p, q in levels(side, reverse, 10)
    )
    return zlib.crc32(content.encode())


class NativeStreams:
    def __init__(self, transport) -> None:
        self.transport = transport
        self.sockets: dict[tuple, object] = {}
        self.states: dict[tuple, dict] = {}
        self.locks: dict[tuple, asyncio.Lock] = {}

    async def _receive(
        self, ws, timeout_seconds: float | None = None, *, text_pong: bool = False
    ) -> dict:
        message = await asyncio.wait_for(
            ws.receive(), self.transport.timeout if timeout_seconds is None else timeout_seconds
        )
        if message.type != aiohttp.WSMsgType.TEXT:
            raise NetworkError("Public websocket closed or failed")
        if text_pong and message.data == "pong":
            return {"event": "pong"}
        try:
            row = loads(message.data)
            if not isinstance(row, dict):
                raise DataError("Public websocket message must be an object")
            return row
        except ValueError as exc:
            raise DataError("Malformed public websocket JSON") from exc

    async def _socket(self, key: tuple, url: str, subscription: dict):
        if key not in self.sockets:
            ws = await self.transport.connect(url)
            self.sockets[key] = ws
            self.states[key] = {}
            await ws.send_json(subscription)
        return self.sockets[key]

    async def _drop(self, key: tuple) -> None:
        ws = self.sockets.pop(key, None)
        self.states.pop(key, None)
        if ws:
            try:
                await asyncio.wait_for(ws.close(), 1)
            except Exception:
                pass  # A dead socket must not prevent the next fresh subscription.

    async def _run(self, key: tuple, operation, *, timeout_seconds: float | None = None):
        async with self.locks.setdefault(key, asyncio.Lock()):
            try:
                return await asyncio.wait_for(
                    operation(),
                    self.transport.timeout if timeout_seconds is None else timeout_seconds,
                )
            except BaseException:
                # A lost sequence, timeout or cancellation requires a fresh subscription.
                await self._drop(key)
                raise

    async def okx(self, m: dict, channel: str) -> dict:
        key = ("okx", channel, m["symbol"])

        async def read():
            arg = {"channel": channel, "instId": m["id"]}
            ws = await self._socket(
                key, "wss://ws.okx.com:8443/ws/v5/public", {"op": "subscribe", "args": [arg]}
            )
            while True:
                try:
                    row = await self._receive(ws, 25, text_pong=True)
                except TimeoutError:
                    # OKX uses application text ping/pong, separate from WS control frames.
                    await ws.send_str("ping")
                    row = await self._receive(ws, 10, text_pong=True)
                if row.get("event") == "error":
                    raise DataError("OKX public subscription rejected")
                if "data" not in row:
                    continue
                if row.get("arg") != arg or len(row["data"]) != 1:
                    raise DataError("OKX public subscription identity mismatch")
                # books5 is a complete snapshot, never an incremental books channel.
                return row["data"][0]

        return await self._run(
            key, read, timeout_seconds=100 if channel == "funding-rate" else None
        )

    async def book(self, venue: str, m: dict) -> dict:
        if venue == "okx":
            r = await self.okx(m, "books5")
            return {
                "symbol": m["symbol"],
                "timestamp": r["ts"],
                "bids": r["bids"],
                "asks": r["asks"],
            }
        key = (venue, "book", m["symbol"])

        async def read():
            if venue == "kraken":
                return await self._kraken(key, m)
            if venue == "binance":
                return await self._binance(key, m)
            if venue == "hyperliquid":
                return await self._hyperliquid(key, m)
            raise DataError("Unsupported native stream")

        return await self._run(key, read, timeout_seconds=60 if venue == "kraken" else None)

    async def _kraken(self, key: tuple, m: dict) -> dict:
        symbol = m.get("ws_symbol") or m["symbol"]
        ws = await self._socket(
            key,
            "wss://ws.kraken.com/v2",
            {
                "method": "subscribe",
                "params": {"channel": "book", "symbol": [symbol], "depth": 10, "snapshot": True},
            },
        )
        state = self.states[key]
        while True:
            row = await self._receive(ws)
            if row.get("success") is False:
                raise DataError("Kraken public subscription rejected")
            if row.get("channel") != "book":
                continue
            if len(row["data"]) != 1 or row["data"][0]["symbol"] != symbol:
                raise DataError("Kraken subscription identity mismatch")
            r = row["data"][0]
            if row.get("type") == "snapshot":
                state.update(bids={}, asks={})
            elif row.get("type") != "update" or "bids" not in state:
                raise DataError("Kraken update without snapshot")
            for side in ("bids", "asks"):
                apply_levels(state[side], r[side], objects=True)
                state[side] = dict(levels(state[side], side == "bids", 10))
            if kraken_checksum(state["bids"], state["asks"]) != r["checksum"]:
                raise DataError("Kraken depth checksum mismatch")
            ts = source_iso_ms(r["timestamp"])
            if ts < state.get("timestamp", 0):
                raise DataError("Kraken source time regressed")
            state["timestamp"] = ts
            return {
                "symbol": m["symbol"],
                "timestamp": ts,
                "bids": levels(state["bids"], True, 10),
                "asks": levels(state["asks"], False, 10),
            }

    async def _binance(self, key: tuple, m: dict) -> dict:
        swap = not m["spot"]
        stream = m["id"].lower() + ("@depth20@100ms" if swap else "@depth@100ms")
        ws = await self._socket(
            key,
            "wss://fstream.binance.com/ws" if swap else "wss://stream.binance.com:9443/ws",
            {"method": "SUBSCRIBE", "params": [stream], "id": 1},
        )
        state = self.states[key]
        if not swap and "last" not in state:
            seed = await self.transport.request(
                "binance_spot", "/api/v3/depth", {"symbol": m["id"], "limit": 5000}
            )
            state.update(last=seed["lastUpdateId"], bids={}, asks={})
            apply_levels(state["bids"], seed["bids"])
            apply_levels(state["asks"], seed["asks"])
            state["bid_boundary"] = min(state["bids"])
            state["ask_boundary"] = max(state["asks"])
        while True:
            r = await self._receive(ws)
            if "code" in r:
                raise DataError("Binance public subscription rejected")
            if "result" in r:
                continue
            if r.get("e") != "depthUpdate" or r.get("s") != m["id"]:
                raise DataError("Binance depth identity mismatch")
            if swap:
                return {"symbol": m["symbol"], "timestamp": r["E"], "bids": r["b"], "asks": r["a"]}
            if r["u"] <= state["last"]:
                continue
            if not r["U"] <= state["last"] + 1 <= r["u"]:
                raise DataError("Binance depth sequence gap")
            for side, field in (("bids", "b"), ("asks", "a")):
                apply_levels(state[side], r[field])
                state[side] = dict(levels(state[side], side == "bids", 5000))
            state["last"] = r["u"]
            if min(len(state["bids"]), len(state["asks"])) < 20:
                raise DataError("Binance snapshot depth exhausted; resync required")
            if (
                levels(state["bids"], True, 20)[-1][0] < state["bid_boundary"]
                or levels(state["asks"], False, 20)[-1][0] > state["ask_boundary"]
            ):
                raise DataError("Binance depth left known snapshot boundaries")
            return {
                "symbol": m["symbol"],
                "timestamp": r["E"],
                "bids": levels(state["bids"], True, 20),
                "asks": levels(state["asks"], False, 20),
            }

    async def _hyperliquid(self, key: tuple, m: dict) -> dict:
        ws = await self._socket(
            key,
            "wss://api.hyperliquid.xyz/ws",
            {
                "method": "subscribe",
                "subscription": {"type": "l2Book", "coin": m["id"]},
            },
        )
        while True:
            row = await self._receive(ws)
            if row.get("channel") == "error":
                raise DataError("Hyperliquid public subscription rejected")
            if row.get("channel") != "l2Book":
                continue
            r = row["data"]
            if r["coin"] != m["id"]:
                raise DataError("Hyperliquid subscription identity mismatch")
            bids, asks = ([[p["px"], p["sz"]] for p in side] for side in r["levels"])
            return {"symbol": m["symbol"], "timestamp": r["time"], "bids": bids, "asks": asks}

    async def close(self) -> None:
        for key in tuple(self.sockets):
            await self._drop(key)
        self.locks.clear()
