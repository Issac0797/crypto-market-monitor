"""Independent fiat-USD caches share one public client, not a blocking scan batch."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conftest import NOW
from test_bot_phase1 import book

from crypto_market_monitor.market_data import stablecoin_feed as module
from crypto_market_monitor.market_data.contracts import Capability


def instrument(symbol):
    return book(symbol=symbol, venue="bitstamp").instrument


def quote(symbol, clock):
    return book(clock[0], symbol=symbol, venue="bitstamp")


def setup(monkeypatch, fetch, clock, *, close=None):
    adapter = SimpleNamespace(
        venue="bitstamp",
        capabilities=frozenset({Capability.ORDER_BOOK}),
        fetch_book=fetch,
        close=close or AsyncMock(),
    )
    items = (instrument("USDT/USD"), instrument("USDC/USD"))
    monkeypatch.setattr(module, "build_compact_bitstamp", AsyncMock(return_value=(adapter, items)))
    scanner_type = module.Scanner
    monkeypatch.setattr(
        module,
        "Scanner",
        lambda adapters, **kwargs: scanner_type(adapters, **kwargs, clock=lambda: NOW + clock[0]),
    )
    return adapter


def test_blocked_usdc_does_not_delay_independent_usdt_publication(monkeypatch):
    async def run():
        clock = [0]
        third_usdt, hold_usdt, release_usdc = asyncio.Event(), asyncio.Event(), asyncio.Event()
        usdt_calls = 0

        async def fetch(item):
            nonlocal usdt_calls
            if item.symbol == "USDC/USD":
                await release_usdc.wait()
            else:
                usdt_calls += 1
                clock[0] += 1
                if usdt_calls == 3:
                    third_usdt.set()
                    await hold_usdt.wait()
            return quote(item.symbol, clock)

        adapter = setup(monkeypatch, fetch, clock)
        feed = module.StablecoinFeed(0)
        feed.start()
        try:
            await asyncio.wait_for(third_usdt.wait(), 3)
            assert feed.latest["USDT/USD"].source_ms == NOW + 2
            assert "USDC/USD" not in feed.latest
            assert not feed.ready.is_set()
            release_usdc.set()
            await asyncio.wait_for(feed.ready.wait(), 3)
            assert set(feed.latest) == {"USDT/USD", "USDC/USD"}
        finally:
            await feed.close()
        assert not feed.latest and feed.task is None
        adapter.close.assert_awaited_once()

    asyncio.run(run())


def test_one_symbol_timeout_clears_only_itself_and_recovers(monkeypatch):
    async def run():
        clock = [0]
        fourth_usdt, third_usdc, recovered_usdc = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        hold_usdt, release_usdc, hold_usdc = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = {"USDT/USD": 0, "USDC/USD": 0}

        async def fetch(item):
            calls[item.symbol] += 1
            if item.symbol == "USDT/USD":
                clock[0] += 1
                if calls[item.symbol] == 4:
                    fourth_usdt.set()
                    await hold_usdt.wait()
            elif calls[item.symbol] == 2:
                raise TimeoutError("synthetic USDC timeout")
            elif calls[item.symbol] == 3:
                third_usdc.set()
                await release_usdc.wait()
            elif calls[item.symbol] == 4:
                recovered_usdc.set()
                await hold_usdc.wait()
            return quote(item.symbol, clock)

        adapter = setup(monkeypatch, fetch, clock)
        feed = module.StablecoinFeed(0)
        feed.start()
        try:
            await asyncio.wait_for(asyncio.gather(fourth_usdt.wait(), third_usdc.wait()), 3)
            assert feed.latest["USDT/USD"].source_ms == NOW + 3
            assert "USDC/USD" not in feed.latest
            release_usdc.set()
            await asyncio.wait_for(recovered_usdc.wait(), 3)
            assert set(feed.latest) == {"USDT/USD", "USDC/USD"}
        finally:
            await feed.close()
        adapter.close.assert_awaited_once()

    asyncio.run(run())


def test_cancellation_closes_shared_adapter_and_both_inflight_tasks(monkeypatch):
    async def run():
        started = {name: asyncio.Event() for name in ("USDT/USD", "USDC/USD")}
        stopped = set()
        never = asyncio.Event()

        async def fetch(item):
            started[item.symbol].set()
            try:
                await never.wait()
            finally:
                stopped.add(item.symbol)

        adapter = setup(monkeypatch, fetch, [0])
        feed = module.StablecoinFeed(0)
        feed.start()
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 3)
        feed.latest["USDT/USD"] = quote("USDT/USD", [0])
        await feed.close()
        assert stopped == set(started)
        assert feed.task is None and not feed.latest
        adapter.close.assert_awaited_once()

    asyncio.run(run())


def test_metadata_refresh_reconnects_even_when_previous_client_cleanup_fails(monkeypatch):
    async def run():
        clock, rebuilt, hold = [0], asyncio.Event(), asyncio.Event()

        async def fetch(item):
            return quote(item.symbol, clock)

        first = setup(monkeypatch, fetch, clock, close=AsyncMock(side_effect=RuntimeError()))

        async def second_fetch(item):
            rebuilt.set()
            await hold.wait()
            return quote(item.symbol, clock)

        second = SimpleNamespace(**vars(first))
        second.fetch_book, second.close = second_fetch, AsyncMock()
        items = (instrument("USDT/USD"), instrument("USDC/USD"))
        monkeypatch.setattr(
            module,
            "build_compact_bitstamp",
            AsyncMock(side_effect=[(first, items), (second, items)]),
        )
        monkeypatch.setattr(module, "METADATA_REFRESH_SECONDS", 0.01)
        feed = module.StablecoinFeed(0)
        feed.start()
        try:
            await asyncio.wait_for(rebuilt.wait(), 3)
            first.close.assert_awaited_once()
        finally:
            await feed.close()
        assert not feed.latest and feed.task is None
        second.close.assert_awaited_once()

    asyncio.run(run())


def test_shutdown_during_metadata_cleanup_waits_for_adapter_close(monkeypatch):
    async def run():
        close_started, release_close, close_finished = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def close_adapter():
            close_started.set()
            await release_close.wait()
            close_finished.set()

        async def fetch(item):
            return quote(item.symbol, [0])

        adapter = setup(monkeypatch, fetch, [0], close=AsyncMock(side_effect=close_adapter))
        monkeypatch.setattr(module, "METADATA_REFRESH_SECONDS", 0)
        feed = module.StablecoinFeed(0)
        feed.start()
        await asyncio.wait_for(close_started.wait(), 3)
        shutdown = asyncio.create_task(feed.close())
        # Let close() request cancellation while the refresh is already closing
        # its adapter. The adapter must finish before shutdown returns.
        await asyncio.sleep(0)
        release_close.set()
        await asyncio.wait_for(shutdown, 3)
        assert close_finished.is_set()
        assert feed.task is None and not feed.latest
        adapter.close.assert_awaited_once()
        module.build_compact_bitstamp.assert_awaited_once()

    asyncio.run(run())
