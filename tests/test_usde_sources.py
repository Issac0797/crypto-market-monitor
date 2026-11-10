"""Offline contracts for the reviewed USDe snapshot source and feed isolation."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import NOW
from test_bot_phase1 import book

from crypto_market_monitor.adapters.compact_bybit import build_compact_bybit
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.market_data.usde_config import load_usde
from crypto_market_monitor.models import DataError


def client(row=None):
    row = row or {
        "id": "USDEUSDT",
        "symbol": "USDE/USDT",
        "base": "USDE",
        "quote": "USDT",
        "spot": True,
        "active": True,
    }
    c = SimpleNamespace(options={}, has={"fetchOrderBook": True})
    c.fetch_markets = AsyncMock(return_value=[row])
    c.set_markets = lambda rows: setattr(c, "markets", {r["symbol"]: r for r in rows})
    c.load_markets = AsyncMock(side_effect=lambda: c.markets)
    c.close = AsyncMock()
    return c


def test_filtered_bybit_discovery_uses_public_spot_only():
    async def run():
        c = client()
        a, items = await build_compact_bybit(c)
        assert len(items) == 1 and items[0].venue == "bybit"
        c.fetch_markets.assert_awaited_once_with({"symbol": "USDEUSDT"})
        assert c.options["fetchMarkets"] == {
            "types": ["spot"],
            "usePrivateInstrumentsInfo": False,
        }
        assert list(c.markets) == ["USDE/USDT"]
        await a.close()

    asyncio.run(run())


@pytest.mark.parametrize("change", [{"active": False}, {"id": "OTHER"}, {"spot": False}])
def test_bybit_inactive_wrong_or_nonspot_markets_are_rejected(change):
    async def run():
        c = client()
        c.fetch_markets.return_value[0].update(change)
        with pytest.raises(DataError):
            await build_compact_bybit(c)
        c.close.assert_awaited_once()

    asyncio.run(run())


def test_native_bybit_uses_snapshot_source_ts_not_old_matching_or_local_time():
    from crypto_market_monitor.adapters.native_public import public_client

    async def run():
        c = public_client("bybit")
        c.markets = {"USDE/USDT": {"id": "USDEUSDT", "symbol": "USDE/USDT", "spot": True}}
        c.publicGetV5MarketOrderbook = AsyncMock(
            return_value={
                "retCode": 0,
                "time": NOW + 999,
                "result": {
                    "s": "USDEUSDT",
                    "ts": NOW,
                    "cts": NOW - 600000,
                    "b": [["0.9999", "10000"]],
                    "a": [["1.0001", "10000"]],
                },
            }
        )
        try:
            result = await c.fetch_order_book("USDE/USDT", limit=20)
            assert result["timestamp"] == NOW
            c.publicGetV5MarketOrderbook.return_value["result"].pop("ts")
            assert (await c.fetch_order_book("USDE/USDT", limit=20))["timestamp"] is None
        finally:
            await c.close()

    asyncio.run(run())


def test_candidate_changes_only_usde_source_and_reliability():
    old = RiskWatchConfig.load(Path("config/bot.toml"))
    candidate = RiskWatchConfig.load(Path("config/bot-phase2-3.toml"))
    for key in old.__dataclass_fields__:
        if key not in {"usde", "reliability", "fingerprint"}:
            assert getattr(old, key) == getattr(candidate, key)
    assert candidate.usde.cross_venue == "bybit"
    assert candidate.usde.quote_poll_seconds == 5
    assert candidate.usde.max_age_ms == 30000
    assert candidate.usde.rule.max_skew_ms == 10000
    with pytest.raises(ValueError):
        load_usde({"cross_venue": "unreviewed"})


def test_kraken_failure_does_not_block_bybit_and_disconnect_clears_only_cross(monkeypatch):
    from crypto_market_monitor.market_data import usde_feed as module

    async def run():
        b = book(symbol="USDE/USDT", venue="bybit")
        disconnected, next_read = asyncio.Event(), asyncio.Event()

        async def fetch(_):
            if not next_read.is_set():
                next_read.set()
                return b
            disconnected.set()
            raise DataError("synthetic disconnect")

        a = SimpleNamespace(fetch_book=fetch, close=AsyncMock())
        monkeypatch.setattr(module, "build_compact_kraken", AsyncMock(side_effect=DataError()))
        monkeypatch.setattr(
            module, "build_compact_bybit", AsyncMock(return_value=(a, (b.instrument,)))
        )
        config = RiskWatchConfig.load(Path("config/bot-phase2-3.toml")).usde
        f = module.UsdeFeed(replace(config, quote_poll_seconds=0.01))
        f.start()
        try:
            await asyncio.wait_for(f.ready.wait(), 1)
            assert f.latest[b.instrument.symbol] == b
            await asyncio.wait_for(disconnected.wait(), 1)
            await asyncio.sleep(0)
            assert b.instrument.symbol not in f.latest
        finally:
            await f.close()
        assert not f.latest and f.task is None
        a.close.assert_awaited_once()

    asyncio.run(run())


def test_cleanup_error_does_not_cancel_peer_and_failed_source_rebuilds(monkeypatch):
    from crypto_market_monitor.market_data import usde_feed as module

    async def run():
        direct = book()
        cross = book(symbol="USDE/USDT", venue="bybit")
        rebuilt = asyncio.Event()
        peer_stopped = asyncio.Event()
        calls = 0
        bybit = SimpleNamespace(
            fetch_book=AsyncMock(return_value=cross),
            close=AsyncMock(side_effect=[RuntimeError("synthetic close failure"), None, None]),
        )

        async def build():
            nonlocal calls
            calls += 1
            if calls == 2:
                rebuilt.set()
            return bybit, (cross.instrument,)

        async def healthy_peer(self):
            self.latest[direct.instrument.symbol] = direct
            try:
                await asyncio.Event().wait()
            finally:
                peer_stopped.set()

        monkeypatch.setattr(module.UsdeFeed, "_kraken", healthy_peer)
        monkeypatch.setattr(module, "build_compact_bybit", build)
        config = RiskWatchConfig.load(Path("config/bot-phase2-3.toml")).usde
        f = module.UsdeFeed(replace(config, quote_poll_seconds=0.01, refresh_seconds=0.005))
        f.start()
        try:
            await asyncio.wait_for(rebuilt.wait(), 1)
            await asyncio.sleep(0)
            assert f.latest[direct.instrument.symbol] == direct
            assert f.latest[cross.instrument.symbol] == cross
            assert not f.task.done()
            assert not peer_stopped.is_set()
        finally:
            await f.close()
        assert not f.latest
        assert bybit.close.await_count >= 2

    asyncio.run(run())
