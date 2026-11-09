"""Offline wire contracts for locally owned exchange HTTP and WebSocket code."""

import asyncio
import json
from copy import deepcopy
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from conftest import NOW

from crypto_market_monitor.adapters.native_client import NativePublicClient
from crypto_market_monitor.adapters.native_http import PublicTransport, RateLimitExceeded, loads
from crypto_market_monitor.adapters.native_markets import market
from crypto_market_monitor.adapters.native_streams import NativeStreams, kraken_checksum
from crypto_market_monitor.models import DataError


def transport(*payloads):
    return SimpleNamespace(request=AsyncMock(side_effect=payloads), close=AsyncMock(), timeout=1)


@pytest.mark.parametrize(
    "venue,payload,query,expected",
    [
        (
            "okx",
            {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "instType": "SWAP",
                        "ctType": "linear",
                        "baseCcy": "",
                        "quoteCcy": "",
                        "settleCcy": "USDT",
                        "ctVal": "0.01",
                        "ctValCcy": "BTC",
                        "state": "live",
                    }
                ],
            },
            {"instId": "BTC-USDT-SWAP"},
            ("BTC/USDT:USDT", "BTC-USDT-SWAP", "0.01"),
        ),
        (
            "binance",
            {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "marginAsset": "USDT",
                        "contractType": "PERPETUAL",
                        "status": "TRADING",
                    }
                ]
            },
            {},
            ("BTC/USDT:USDT", "BTCUSDT", "1"),
        ),
        (
            "bybit",
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "USDEUSDT",
                            "baseCoin": "USDE",
                            "quoteCoin": "USDT",
                            "status": "Trading",
                        }
                    ]
                },
            },
            {"symbol": "USDEUSDT"},
            ("USDE/USDT", "USDEUSDT", None),
        ),
        (
            "kraken",
            {"error": [], "result": {"USDCUSD": {"wsname": "USDC/USD", "status": "online"}}},
            {"pair": "USDCUSD"},
            ("USDC/USD", "USDCUSD", None),
        ),
        (
            "bitstamp",
            [{"name": "USDC/USD", "url_symbol": "usdcusd", "trading": "Enabled"}],
            {},
            ("USDC/USD", "usdcusd", None),
        ),
        ("hyperliquid", {"universe": [{"name": "BTC"}]}, {}, ("BTC/USDC:USDC", "BTC", "1")),
    ],
)
def test_native_catalog_parses_official_fields(venue, payload, query, expected):
    async def run():
        c = NativePublicClient(venue, transport=transport(payload))
        c.options["fetchMarkets"] = {"types": ["swap"] if venue != "binance" else ["linear"]}
        (row,) = await c.fetch_markets(query)
        assert (row["symbol"], row["id"], row["contractSize"]) == expected
        assert row["active"] is True
        await c.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "venue,payload",
    [
        ("okx", {"code": "50000", "data": []}),
        ("bybit", {"retCode": 10001, "result": {}}),
        ("kraken", {"error": ["bad"], "result": {}}),
    ],
)
def test_native_catalog_api_errors_are_not_empty_success(venue, payload):
    async def run():
        c = NativePublicClient(venue, transport=transport(payload))
        with pytest.raises(DataError):
            await c.fetch_markets()

    asyncio.run(run())


@pytest.mark.parametrize(
    "venue,native_id,payload,source",
    [
        (
            "okx",
            "USDC-USD",
            {
                "code": "0",
                "data": [
                    {
                        "bids": [["1", "2", "0", "1"]],
                        "asks": [["1.01", "3", "0", "1"]],
                        "ts": str(NOW),
                    }
                ],
            },
            NOW,
        ),
        (
            "bitstamp",
            "usdcusd",
            {
                "bids": [["1", "2"]],
                "asks": [["1.01", "3"]],
                "microtimestamp": str(NOW * 1000 + 999),
                "timestamp": "1",
            },
            NOW,
        ),
        (
            "bybit",
            "USDCUSD",
            {
                "retCode": 0,
                "time": NOW + 1000,
                "result": {
                    "s": "USDCUSD",
                    "b": [["1", "2"]],
                    "a": [["1.01", "3"]],
                    "ts": NOW,
                    "cts": NOW - 100000,
                },
            },
            NOW,
        ),
        (
            "binance",
            "USDCUSD",
            {"bids": [["1", "2"]], "asks": [["1.01", "3"]], "lastUpdateId": 999},
            None,
        ),
        (
            "kraken",
            "USDCUSD",
            {
                "error": [],
                "result": {"USDCUSD": {"bids": [["1", "2", 100]], "asks": [["1.01", "3", 200]]}},
            },
            None,
        ),
        (
            "hyperliquid",
            "USDCUSD",
            {
                "coin": "USDCUSD",
                "time": NOW,
                "levels": [[{"px": "1", "sz": "2"}], [{"px": "1.01", "sz": "3"}]],
            },
            NOW,
        ),
    ],
)
def test_native_books_use_only_documented_source_time(venue, native_id, payload, source):
    async def run():
        c = NativePublicClient(venue, transport=transport(payload))
        c.set_markets([market(native_id, "USDC", "USD", True)])
        r = await c.fetch_order_book("USDC/USD")
        assert r["bids"] == [["1", "2"]] and r["asks"] == [["1.01", "3"]]
        assert r["timestamp"] is None if source is None else int(r["timestamp"]) == source

    asyncio.run(run())


class Socket:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.send_json = AsyncMock()
        self.close = AsyncMock()

    async def receive(self):
        row = next(self.rows)
        if isinstance(row, BaseException):
            raise row
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(row))


def stream_fixture(rows, *responses):
    t = transport(*responses)
    ws = Socket(rows)
    t.connect = AsyncMock(return_value=ws)
    return NativeStreams(t), ws


@pytest.mark.parametrize("fault", [None, "checksum", "missing_time", "wrong_symbol", "no_snapshot"])
def test_kraken_snapshot_delta_checksum_and_failure_reconnect(fault):
    async def run():
        bids, asks = {D("1.00"): D("2.000")}, {D("1.01"): D("3.000")}
        r = {
            "symbol": "USDC/USD",
            "bids": [{"price": "1.00", "qty": "2.000"}],
            "asks": [{"price": "1.01", "qty": "3.000"}],
            "checksum": kraken_checksum(bids, asks),
            "timestamp": "2026-10-27T00:00:00Z",
        }
        msg = {"channel": "book", "type": "snapshot", "data": [r]}
        if fault == "checksum":
            r["checksum"] ^= 1
        elif fault == "missing_time":
            del r["timestamp"]
        elif fault == "wrong_symbol":
            r["symbol"] = "USDT/USD"
        elif fault == "no_snapshot":
            msg["type"] = "update"
        delta = deepcopy(msg)
        delta["type"] = "update"
        delta["data"][0].update(
            bids=[{"price": "1.00", "qty": "4.000"}],
            asks=[],
            checksum=kraken_checksum({D("1.00"): D("4.000")}, asks),
        )
        streams, ws = stream_fixture([msg, delta])
        m = market("USDCUSD", "USDC", "USD", True)
        if fault:
            with pytest.raises((DataError, KeyError)):
                await streams.book("kraken", m)
            assert not streams.sockets and not streams.states
            ws.close.assert_awaited_once()
        else:
            b = await streams.book("kraken", m)
            assert b["bids"] == [[D("1.00"), D("2.000")]]
            assert (await streams.book("kraken", m))["bids"][0][1] == D("4.000")
            assert streams.transport.connect.await_count == 1
            await streams.close()

    asyncio.run(run())


@pytest.mark.parametrize("gap", [False, True])
def test_binance_diff_bridges_seed_discards_old_and_rejects_gap(gap):
    async def run():
        seed = {
            "lastUpdateId": 100,
            "bids": [[str(100 - i), "1"] for i in range(30)],
            "asks": [[str(101 + i), "1"] for i in range(30)],
        }
        event = {
            "e": "depthUpdate",
            "s": "BTCUSDT",
            "E": NOW,
            "U": 101,
            "u": 102,
            "b": [["100", "3"]],
            "a": [["101", "0"], ["101.5", "4"]],
        }
        old = {**event, "U": 99, "u": 100}
        if gap:
            event["U"] = 103
        streams, ws = stream_fixture([{"result": None, "id": 1}, old, event], seed)
        m = market("BTCUSDT", "BTC", "USDT", True)
        if gap:
            with pytest.raises(DataError, match="sequence gap"):
                await streams.book("binance", m)
            ws.close.assert_awaited_once()
        else:
            b = await streams.book("binance", m)
            assert b["timestamp"] == NOW and b["bids"][0] == [D(100), D(3)]
            assert b["asks"][0] == [D("101.5"), D(4)]
            await streams.close()

    asyncio.run(run())


def test_ws_cancel_discards_partial_state_and_closes_socket():
    async def run():
        streams, ws = stream_fixture([asyncio.CancelledError()])
        with pytest.raises(asyncio.CancelledError):
            await streams.book("kraken", market("USDCUSD", "USDC", "USD", True))
        assert not streams.sockets and not streams.states
        ws.close.assert_awaited_once()

    asyncio.run(run())


def test_okx_stream_uses_full_books5_and_native_funding_identity():
    async def run():
        m = market("BTC-USDT", "BTC", "USDT", True)
        r = {
            "arg": {"channel": "books5", "instId": m["id"]},
            "data": [{"bids": [["1", "2"]], "asks": [["2", "3"]], "ts": str(NOW)}],
        }
        streams, ws = stream_fixture([r])
        b = await streams.book("okx", m)
        assert b["timestamp"] == str(NOW)
        ws.send_json.assert_awaited_once_with({"op": "subscribe", "args": [r["arg"]]})
        await streams.close()

    asyncio.run(run())


def test_public_transport_rejects_private_routes_and_attribution_before_network():
    async def run():
        t = PublicTransport()
        for path, params in [
            ("/api/v3/order", {}),
            ("/api/v3/depth", {"brokerId": "x"}),
            ("/api/v3/depth", {"signature": "x"}),
        ]:
            with pytest.raises(ValueError):
                await t.request("binance_spot", path, params)
        with pytest.raises(ValueError):
            await t.request("hyperliquid", "/info", {"type": "clearinghouseState"})
        with pytest.raises(ValueError):
            await t.connect("wss://example.com/ws")
        assert t._session is None

    asyncio.run(run())


def test_decimal_json_retains_full_precision_for_checksum():
    row = loads('{"price": 1.0010000000000000001, "qty": 0.00100000}')
    assert row["price"] == D("1.0010000000000000001")
    assert str(row["qty"]) == "0.00100000"


def test_kraken_official_checksum_reference_vector():
    # Kraken's published book-checksum-v2 example, expected CRC supplied by exchange.
    bids = [
        ("45283.5", "0.10000000"),
        ("45283.4", "1.54582015"),
        ("45282.1", "0.10000000"),
        ("45281.0", "0.10000000"),
        ("45280.3", "1.54592586"),
        ("45279.0", "0.07990000"),
        ("45277.6", "0.03310103"),
        ("45277.5", "0.30000000"),
        ("45277.3", "1.54602737"),
        ("45276.6", "0.15445238"),
    ]
    asks = [
        ("45285.2", "0.00100000"),
        ("45286.4", "1.54571953"),
        ("45286.6", "1.54571109"),
        ("45289.6", "1.54560911"),
        ("45290.2", "0.15890660"),
        ("45291.8", "1.54553491"),
        ("45294.7", "0.04454749"),
        ("45296.1", "0.35380000"),
        ("45297.5", "0.09945542"),
        ("45299.5", "0.18772827"),
    ]
    assert (
        kraken_checksum({D(p): D(q) for p, q in bids}, {D(p): D(q) for p, q in asks}) == 3310070434
    )


def test_filtered_discovery_can_add_another_market_without_losing_first():
    async def run():
        t = transport(
            *(
                {
                    "symbols": [
                        {
                            "symbol": a + "USDT",
                            "baseAsset": a,
                            "quoteAsset": "USDT",
                            "status": "TRADING",
                        }
                    ]
                }
                for a in ("BTC", "ETH")
            )
        )
        c = NativePublicClient("binance", transport=t)
        await c.load_markets(("BTC/USDT",))
        await c.load_markets(("ETH/USDT",))
        assert set(c.markets) == {"BTC/USDT", "ETH/USDT"}
        assert [call.args[2] for call in t.request.await_args_list] == [
            {"symbol": "BTCUSDT"},
            {"symbol": "ETHUSDT"},
        ]

    asyncio.run(run())


def test_okx_native_funding_timestamp_strings_normalize_to_source_milliseconds():
    from crypto_market_monitor.adapters.native_public import NativePublicAdapter

    async def run():
        r = {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "ts": str(NOW),
            "fundingTime": str(NOW + 3600000),
            "nextFundingTime": str(NOW + 9 * 3600000),
            "fundingRate": "0.0001",
        }
        t = transport({"code": "0", "data": [r]})
        c = NativePublicClient("okx", transport=t)
        c.set_markets([market(r["instId"], "BTC", "USDT", True, settle="USDT", size="0.01")])
        a = NativePublicAdapter("okx", c, clock=lambda: NOW)
        (item,) = await a.discover(("BTC/USDT:USDT",))
        f = await a.fetch_funding(item)
        f.validate_at(NOW, 30000)
        assert f.source_ms == NOW and f.interval_ms == 8 * 3600000
        assert f.rate == D("0.0001")

    asyncio.run(run())


def test_okx_wrong_contract_quantity_unit_is_rejected():
    async def run():
        r = {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "ctType": "linear",
            "state": "live",
            "ctVal": "100",
            "ctValCcy": "USDT",
            "settleCcy": "USDT",
        }
        c = NativePublicClient("okx", transport=transport({"code": "0", "data": [r]}))
        c.options["fetchMarkets"] = {"types": ["swap"]}
        with pytest.raises(DataError):
            await c.fetch_markets()

    asyncio.run(run())


def test_native_transport_streamed_body_and_rate_limit_without_raw_body(monkeypatch):
    class Body:
        async def iter_chunked(self, _):
            for chunk in [b'{"price":', b"1.00000000000000001}"]:
                yield chunk

    class Response:
        status = 200
        content_length = None
        content = Body()
        headers = {"Retry-After": "2"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def run():
        t = PublicTransport()
        response = Response()
        calls = []

        def request(*args, **kwargs):
            calls.append((args, kwargs))
            return response

        monkeypatch.setattr(t, "session", lambda: SimpleNamespace(request=request))
        assert (await t.request("binance_spot", "/api/v3/depth"))["price"] == D(
            "1.00000000000000001"
        )
        assert calls[0][0] == ("GET", "https://api.binance.com/api/v3/depth")
        assert calls[0][1]["allow_redirects"] is False
        response.status = 429
        t._next_request = 0
        with pytest.raises(RateLimitExceeded):
            await t.request("binance_spot", "/api/v3/depth")
        assert t._blocked_until > 0

    asyncio.run(run())


def test_funding_stream_waits_beyond_http_deadline_without_reconnecting():
    async def run():
        arg = {"channel": "funding-rate", "instId": "BTC-USDT-SWAP"}

        class DelayedSocket(Socket):
            async def receive(self):
                await asyncio.sleep(0.06)
                return await super().receive()

        ws = DelayedSocket([{"arg": arg, "data": [{"ts": str(NOW)}]}])
        t = SimpleNamespace(timeout=0.02, connect=AsyncMock(return_value=ws))
        streams = NativeStreams(t)
        m = market(arg["instId"], "BTC", "USDT", True, settle="USDT", size="0.01")
        try:
            row = await streams.okx(m, "funding-rate")
            assert row["ts"] == str(NOW)
            t.connect.assert_awaited_once()
            ws.close.assert_not_awaited()
        finally:
            await streams.close()

    asyncio.run(run())


@pytest.mark.parametrize("pong", [True, False])
def test_okx_idle_connection_uses_text_ping_and_requires_response(pong):
    async def run():
        arg = {"channel": "funding-rate", "instId": "BTC-USDT-SWAP"}

        class IdleSocket:
            send_json = AsyncMock()
            send_str = AsyncMock()
            close = AsyncMock()
            receive = AsyncMock(
                side_effect=[
                    TimeoutError(),
                    SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="pong")
                    if pong
                    else TimeoutError(),
                    SimpleNamespace(
                        type=aiohttp.WSMsgType.TEXT,
                        data=json.dumps({"arg": arg, "data": [{"ts": str(NOW)}]}),
                    ),
                ]
            )

        ws = IdleSocket()
        streams = NativeStreams(SimpleNamespace(timeout=1, connect=AsyncMock(return_value=ws)))
        m = market(arg["instId"], "BTC", "USDT", True, settle="USDT", size="0.01")
        try:
            if pong:
                assert (await streams.okx(m, "funding-rate"))["ts"] == str(NOW)
                ws.close.assert_not_awaited()
            else:
                with pytest.raises(TimeoutError):
                    await streams.okx(m, "funding-rate")
                assert not streams.sockets
                ws.close.assert_awaited_once()
            ws.send_str.assert_awaited_once_with("ping")
        finally:
            await streams.close()

    asyncio.run(run())


def test_kraken_heartbeats_keep_subscription_until_real_book_update():
    async def run():
        bids, asks = {D("1.00"): D("2.000")}, {D("1.01"): D("3.000")}
        snapshot = {
            "channel": "book",
            "type": "snapshot",
            "data": [
                {
                    "symbol": "USDC/USD",
                    "bids": [{"price": "1.00", "qty": "2.000"}],
                    "asks": [{"price": "1.01", "qty": "3.000"}],
                    "checksum": kraken_checksum(bids, asks),
                    "timestamp": "2026-10-27T00:00:00Z",
                }
            ],
        }

        class QuietSocket(Socket):
            async def receive(self):
                await asyncio.sleep(0.05)
                return await super().receive()

        ws = QuietSocket([*([{"channel": "heartbeat"}] * 5), snapshot])
        t = SimpleNamespace(timeout=0.2, connect=AsyncMock(return_value=ws))
        streams = NativeStreams(t)
        try:
            row = await streams.book("kraken", market("USDCUSD", "USDC", "USD", True))
            assert row["bids"] == [[D("1.00"), D("2.000")]]
            t.connect.assert_awaited_once()
            ws.close.assert_not_awaited()
        finally:
            await streams.close()

    asyncio.run(run())
