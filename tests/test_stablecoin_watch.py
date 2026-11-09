import asyncio
import runpy
import sqlite3
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import NOW
from test_risk_watch import events, watch

from crypto_market_monitor.adapters.compact_bitstamp import build_compact_bitstamp
from crypto_market_monitor.market_data.contracts import ObservationSet
from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.models import Instrument, Level, MarketKind, OrderBook
from crypto_market_monitor.market_data.risk_session import RiskSession
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.market_data.scanner import ScanReport
from crypto_market_monitor.models import DataError
from crypto_market_monitor.notification_store import Outbox
from crypto_market_monitor.operations import instance_lock


def book(t=0, bid="0.993", ask="0.994", symbol="USDT/USD"):
    return OrderBook(
        Instrument(
            "bitstamp",
            symbol,
            symbol.replace("/", "").lower(),
            MarketKind.SPOT,
            symbol.split("/")[0],
            "USD",
        ),
        (Level(D(bid), D("10000")),),
        (Level(D(ask), D("10000")),),
        NOW + t,
        NOW + t,
        "synthetic-test-only",
    )


def record(metric, b, t):
    from crypto_market_monitor.market_data.stablecoin_watch import record_stablecoin

    return record_stablecoin(metric, b, NOW + t)


@pytest.mark.parametrize("symbol", ["USDT/USD", "USDC/USD"])
@pytest.mark.parametrize("sign", [-1, 1])
def test_peg_confirmation_severity_recovery_restart_and_distinct_sources(tmp_path, symbol, sign):
    watcher = watch(tmp_path)
    metric = DerivativeWatch(watcher)
    bid, ask = ("0.993", "0.994") if sign < 0 else ("1.006", "1.007")
    for t in (0, 15000, 30000):
        record(metric, book(t, bid, ask, symbol), t)
    metric = DerivativeWatch(watch(tmp_path))
    record(metric, book(30000, bid, ask, symbol), 45000)
    assert not events(watcher)
    for t in (45000, 60000):
        record(metric, book(t, bid, ask, symbol), t)
    assert events(watcher)[0]["status"] == "STABLECOIN_ENTERED"
    assert events(watcher)[0]["source"] == "bitstamp-public"
    assert D(events(watcher)[0]["details"]["value_percent"]) * sign > 0
    bid, ask = ("0.98", "0.981") if sign < 0 else ("1.019", "1.02")
    for t in range(75000, 150000, 15000):
        record(metric, book(t, bid, ask, symbol), t)
    assert events(watcher)[-1]["status"] == "STABLECOIN_ESCALATED"
    for t in range(150000, 225000, 15000):
        record(metric, book(t, "0.9999", "1.0001", symbol), t)
    assert events(watcher)[-1]["status"] == "STABLECOIN_RECOVERED"


@pytest.mark.parametrize("fault", ["stale", "future", "thin", "venue", "quote", "identity", "wide"])
def test_invalid_usd_evidence_cannot_confirm(tmp_path, fault):
    metric = DerivativeWatch(watch(tmp_path))
    b = book()
    if fault == "stale":
        b = replace(b, source_ms=NOW - 30001)
    if fault == "future":
        b = replace(b, source_ms=NOW + 1)
    if fault == "thin":
        b = replace(b, bids=(Level(D("0.99"), D(1)),))
    if fault == "venue":
        b = replace(b, instrument=replace(b.instrument, venue="other"))
    if fault == "quote":
        b = replace(b, instrument=replace(b.instrument, quote="USDT"))
    if fault == "identity":
        b = replace(b, instrument=replace(b.instrument, native_id="other"))
    if fault == "wide":
        b = book(bid="0.98", ask="1.02")
    with pytest.raises(DataError):
        record(metric, b, 0)
    assert not events(metric.watcher)


def test_gap_resets_confirmation_and_feed_health_is_independent(tmp_path):
    watcher = watch(tmp_path)
    session = RiskSession(watcher, None, NOW)

    def process(t, books):
        scan = ScanReport(ObservationSet(NOW + t, (), ()), (), (), (), ())
        return session.process(scan, NOW + t, books)

    process(0, {"USDT/USD": book()})
    process(15000, {})
    for t in range(30000, 90000, 15000):
        state = process(t, {"USDT/USD": book(t)})
    assert state["stablecoin_feed"] == {"USDT/USD": "fresh", "USDC/USD": "unavailable"}
    assert not any(e["status"] == "STABLECOIN_ENTERED" for e in events(watcher))
    process(90000, {"USDT/USD": book(90000)})
    assert events(watcher)[-1]["status"] == "STABLECOIN_ENTERED"


def test_current_configuration_removes_five_minute_and_enables_usd_peg():
    c = RiskWatchConfig.load(Path("config/bot-b.toml"))
    assert [r.window_ms for r in c.rules] == [900000]
    assert c.stablecoin.threshold == D("0.005")


def test_same_source_time_with_changed_value_is_rejected(tmp_path):
    metric = DerivativeWatch(watch(tmp_path))
    record(metric, book(), 0)
    with pytest.raises(DataError, match="new source timestamp"):
        record(metric, book(bid="0.98", ask="0.981"), 0)


def test_stablecoin_feed_is_bounded_and_cancels_on_shutdown(monkeypatch):
    from crypto_market_monitor.market_data import stablecoin_feed as module

    async def check():
        adapter = SimpleNamespace(close=AsyncMock())
        rows = (
            book(bid="0.9999", ask="1.0001"),
            book(bid="0.9999", ask="1.0001", symbol="USDC/USD"),
        )
        monkeypatch.setattr(
            module,
            "build_compact_bitstamp",
            AsyncMock(return_value=(adapter, tuple(row.instrument for row in rows))),
        )
        fake = SimpleNamespace(
            run_once=AsyncMock(
                side_effect=lambda requests: ScanReport(
                    ObservationSet(
                        NOW, tuple(r for r in rows if r.instrument == requests[0].instrument), ()
                    ),
                    (),
                    (),
                    (),
                    (),
                )
            )
        )
        monkeypatch.setattr(module, "Scanner", lambda *a, **k: fake)
        feed = module.StablecoinFeed(15)
        feed.start()
        await asyncio.wait_for(feed.ready.wait(), 1)
        assert len(feed.latest) == 2
        await feed.close()
        adapter.close.assert_awaited_once()
        assert feed.task is None

    asyncio.run(check())


def test_usd_adapter_preserves_source_time_and_caps_levels():
    from crypto_market_monitor.adapters.compact_bitstamp import BitstampUsdAdapter

    async def check():
        c = SimpleNamespace(options={}, has={"fetchOrderBook": True}, close=AsyncMock())
        c.fetch_order_book = AsyncMock(
            return_value={
                "symbol": "USDT/USD",
                "timestamp": NOW,
                "bids": [[str(D("0.999") - D(i) / 10000), "10000"] for i in range(25)],
                "asks": [[str(D("1.001") + D(i) / 10000), "10000"] for i in range(25)],
            }
        )
        a = BitstampUsdAdapter("bitstamp", c, clock=lambda: NOW + 5)
        a._instruments[book().instrument.key] = book().instrument
        b = await a.fetch_book(book().instrument)
        assert b.source_ms == NOW and b.received_ms == NOW + 5
        assert len(b.bids) == len(b.asks) == 20
        c.fetch_order_book.assert_awaited_once_with("USDT/USD", limit=20)
        await a.close()

    asyncio.run(check())


def test_compact_usd_discovery_keeps_only_two_markets():
    async def check():
        rows = [
            {
                "id": s.replace("/", "").lower(),
                "symbol": s,
                "base": s.split("/")[0],
                "quote": "USD",
                "spot": True,
                "active": True,
            }
            for s in ("USDT/USD", "USDC/USD", "BTC/USD")
        ]
        selected = {}
        client = SimpleNamespace(
            options={}, has={}, fetch_markets=AsyncMock(return_value=rows), close=AsyncMock()
        )
        client.set_markets = Mock(
            side_effect=lambda r: selected.update({x["symbol"]: x for x in r})
        )
        client.load_markets = AsyncMock(side_effect=lambda: selected)
        adapter, instruments = await build_compact_bitstamp(client)
        assert len(selected) == 2 and len(instruments) == 2
        await adapter.close()

    asyncio.run(check())


def test_specific_migration_removes_only_retired_state_and_pending_alerts(tmp_path):
    migrate = runpy.run_path("scripts/migrate-bot-b-config.py")["migrate"]
    new = Path("config/bot-b.toml")
    content = new.read_text(encoding="utf-8").split("\n[stablecoin]")[0]
    old = tmp_path / "old.toml"
    old.write_text(
        content.replace(
            "[[rules]]",
            '[[rules]]\nwindow_ms = 300000\nthreshold = "0.03"\nrecovery = "0.015"\n\n[[rules]]',
            1,
        ),
        encoding="utf-8",
    )
    root = tmp_path / "state"
    watcher = RiskWatch(Outbox(root / "data/bot-b.sqlite3"), RiskWatchConfig.load(old))
    with watcher.outbox.connect() as db:
        for window in (300000, 900000):
            db.execute("INSERT INTO risk_state VALUES ('BTC/USDT',?,1,1,?,?)", (window, NOW, NOW))
    watcher.outbox.enqueue(watcher.event("BTC/USDT", NOW, "ENTERED", {}, key="move:300000:ENTERED"))
    with instance_lock(root / "service.lock"), pytest.raises(OSError):
        migrate(root, old, new, stablecoin_upgrade=True)
    migrate(root, old, new, stablecoin_upgrade=True)
    with sqlite3.connect(root / "data/bot-b.sqlite3") as db:
        assert db.execute("SELECT window FROM risk_state").fetchall() == [(900000,)]
        assert db.execute("SELECT state FROM notification_outbox").fetchone()[0] == "expired"
    RiskWatch(watcher.outbox, RiskWatchConfig.load(new))
    tuned = tmp_path / "tuned.toml"
    tuned.write_text(
        new.read_text(encoding="utf-8").replace('threshold = "0.05"', 'threshold = "0.06"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Only reviewed"):
        migrate(root, old, tuned, stablecoin_upgrade=True)
