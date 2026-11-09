"""Offline OKX partial failure, publication health and warmup episode regression."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import NOW
from test_bot_b_reliability import watcher
from test_derivative_watch import funding
from test_risk_watch import book, events

from crypto_market_monitor.market_data.bot_b_okx_books import BotBOkxBooks
from crypto_market_monitor.market_data.contracts import Capability
from crypto_market_monitor.market_data.funding_stream import FundingStream
from crypto_market_monitor.market_data.risk_session import RiskSession


def eth(b):
    return replace(
        b, instrument=replace(b.instrument, symbol="ETH/USDT", native_id="ETH-USDT", base="ETH")
    )


def test_slow_eth_cannot_queue_btc_and_failures_retry_only_twice():
    async def run():
        started, btc_done = asyncio.Event(), asyncio.Event()
        calls = {"BTC/USDT": 0, "ETH/USDT": 0}

        async def fetch(i):
            calls[i.symbol] += 1
            if i.symbol == "ETH/USDT":
                started.set()
                await btc_done.wait()
                raise TimeoutError("synthetic ETH timeout")
            await started.wait()
            btc_done.set()
            return book()

        adapter = SimpleNamespace(
            venue="okx", capabilities=frozenset({Capability.ORDER_BOOK}), fetch_book=fetch
        )
        sampler = BotBOkxBooks(
            adapter, (eth(book()).instrument, book().instrument), retry_seconds=0, clock=lambda: NOW
        )
        result = await asyncio.wait_for(sampler.run_once(), 2)
        assert [b.instrument.symbol for b in result.observations.books] == ["BTC/USDT"]
        assert result.failures[0][1] == "timeout"
        assert calls == {"BTC/USDT": 1, "ETH/USDT": 2}

    asyncio.run(run())


def test_second_attempt_recovers_but_never_accepts_stale_or_mismatched_data():
    async def run():
        for fault in ("timeout", "stale", "identity"):
            bad = replace(book(), source_ms=NOW - 30001) if fault == "stale" else eth(book())
            fetch = AsyncMock(side_effect=[TimeoutError() if fault == "timeout" else bad, book()])
            adapter = SimpleNamespace(
                venue="okx", capabilities=frozenset({Capability.ORDER_BOOK}), fetch_book=fetch
            )
            result = await BotBOkxBooks(
                adapter, (book().instrument,), retry_seconds=0, clock=lambda: NOW
            ).run_once()
            assert result.observations.books == (book(),) and not result.failures
            assert fetch.await_count == 2
        adapter.fetch_book = AsyncMock(return_value=replace(book(), source_ms=NOW - 30001))
        result = await BotBOkxBooks(
            adapter, (book().instrument,), retry_seconds=0, clock=lambda: NOW
        ).run_once()
        assert not result.observations.books and result.failures[0][1] == "invalid_or_stale_data"

    asyncio.run(run())


def test_cancelling_sampler_cancels_all_inflight_reads():
    async def run():
        started, stopped = set(), set()
        ready, hold = asyncio.Event(), asyncio.Event()

        async def fetch(i):
            started.add(i.symbol)
            if len(started) == 2:
                ready.set()
            try:
                await hold.wait()
            finally:
                stopped.add(i.symbol)

        a = SimpleNamespace(
            venue="okx", capabilities=frozenset({Capability.ORDER_BOOK}), fetch_book=fetch
        )
        task = asyncio.create_task(
            BotBOkxBooks(a, (book().instrument, eth(book()).instrument)).run_once()
        )
        await asyncio.wait_for(ready.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped == started

    asyncio.run(run())


@pytest.mark.parametrize("fault", [None, "stale", "identity"])
def test_funding_rest_fallback_is_same_instrument_and_strictly_fresh(fault):
    async def run():
        completed, hold = asyncio.Event(), asyncio.Event()
        observation = funding()
        if fault == "stale":
            observation = replace(observation, source_ms=NOW - 30001)
        if fault == "identity":
            observation = replace(
                observation, instrument=replace(observation.instrument, base="OTHER")
            )

        async def ws(i):
            if completed.is_set():
                await hold.wait()
            raise TimeoutError("synthetic WS timeout")

        async def rest(i):
            completed.set()
            return observation

        a = SimpleNamespace(watch_funding=ws, fetch_funding=rest)
        stream = FundingStream(a, (funding().instrument,), rest_fallback=True)
        stream.start()
        try:
            await asyncio.wait_for(completed.wait(), 2)
            await asyncio.sleep(0)
            if fault:
                assert not stream.latest and funding().instrument.symbol in stream.errors
            else:
                assert stream.latest[funding().instrument.symbol] == funding()
                assert not stream.errors
        finally:
            await stream.close()

    asyncio.run(run())


def test_waiting_publication_advances_data_recovery_without_risk_confirmation(tmp_path):
    w = watcher(tmp_path)
    symbol = "BTC/USDT:USDT"
    for t in (0, 15000, 30000):
        w.feed_health(f"funding:{symbol}", False, NOW + t)
    stream = SimpleNamespace(latest={symbol: funding()}, errors=set())
    session = RiskSession(w, stream, NOW)
    for t in (45000, 60000, 75000, 90000, 105000):
        result = session._funding(symbol, NOW + t)
        assert result["status"] == "waiting_for_fresh_publication"
        assert result["source_ms"] == NOW
    notices = [e for e in events(w) if e["symbol"] == f"funding:{symbol}"]
    assert [e["status"] for e in notices] == ["DATA_UNAVAILABLE", "DATA_RECOVERED"]
    assert session.metrics.summary() == {}
    assert session._funding(symbol, NOW + 180001)["status"] == "unavailable"


def test_rewarming_notice_is_one_per_incomplete_window_across_restart(tmp_path):
    w = watcher(tmp_path)
    w.record(book(), NOW)
    for t in (60000, 120000, 180000):
        w = watcher(tmp_path)
        w.record(book(t), NOW + t)
    assert len([e for e in events(w) if e["status"] == "DATA_GAP_REWARMING"]) == 1
    # A complete new continuous 15-minute window re-arms the gap notice.
    for t in range(195000, 1080001, 15000):
        w.record(book(t), NOW + t)
    w.record(book(1140000), NOW + 1140000)
    assert len([e for e in events(w) if e["status"] == "DATA_GAP_REWARMING"]) == 2
