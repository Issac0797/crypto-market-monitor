import asyncio
import hashlib
import os
import runpy
import sqlite3
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import NOW
from test_risk_watch import book as price_book
from test_risk_watch import configuration, events, watch

from crypto_market_monitor.adapters.compact_okx import build_compact_okx
from crypto_market_monitor.market_data.contracts import ObservationSet
from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.funding_stream import FundingStream
from crypto_market_monitor.market_data.models import (
    FundingObservation,
    Instrument,
    Level,
    MarketKind,
)
from crypto_market_monitor.market_data.risk_session import RiskSession
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.market_data.scanner import ScanReport
from crypto_market_monitor.models import DataError
from crypto_market_monitor.notification_store import Outbox
from crypto_market_monitor.operations import instance_lock
from crypto_market_monitor.risk_watch_backup import backup_if_due


def book(*args, **kwargs):
    value = price_book(*args, **kwargs)
    return replace(
        value,
        bids=(Level(value.bids[0].price, D("1000")),),
        asks=(Level(value.asks[0].price, D("1000")),),
    )


def perp(t=0, price="101.5", quantity="10000"):
    instrument = Instrument(
        "okx",
        "BTC/USDT:USDT",
        "BTC-USDT-SWAP",
        MarketKind.PERPETUAL,
        "BTC",
        "USDT",
        "USDT",
        D("0.01"),
    )
    return replace(
        book(t, price),
        instrument=instrument,
        bids=(Level(D(price), D(quantity)),),
        asks=(Level(D(price), D(quantity)),),
    )


def funding(t=0, rate="0.0002", interval=3600000):
    return FundingObservation(
        perp().instrument, D(rate), interval, NOW + 3600000, NOW + t, NOW + t, "synthetic-test-only"
    )


def test_basis_confirmation_recovery_and_signed_severity(tmp_path):
    watcher = watch(tmp_path)
    metric = DerivativeWatch(watcher)
    for t in range(0, 75000, 15000):
        metric.record_basis(book(t), perp(t), NOW + t)
    assert [e["status"] for e in events(watcher)] == ["BASIS_ENTERED"]
    assert events(watcher)[0]["details"]["value_percent"] == "1.500"
    for t in range(75000, 150000, 15000):
        metric.record_basis(book(t), perp(t, "103"), NOW + t)
    assert events(watcher)[-1]["status"] == "BASIS_ESCALATED"
    for t in range(150000, 225000, 15000):
        metric.record_basis(book(t), perp(t, "100"), NOW + t)
    assert events(watcher)[-1]["status"] == "BASIS_RECOVERED"


@pytest.mark.parametrize("fault", ["stale", "future", "skew", "quote", "settle", "venue", "depth"])
def test_invalid_basis_never_confirms_or_creates_an_opportunity(tmp_path, fault):
    watcher = watch(tmp_path)
    left, right = book(), perp()
    if fault == "stale":
        right = replace(right, source_ms=NOW - 30001)
    elif fault == "future":
        right = replace(right, source_ms=NOW + 1)
    elif fault == "skew":
        right = replace(right, source_ms=NOW - 10001)
    elif fault in {"quote", "settle", "venue"}:
        key = "settlement" if fault == "settle" else fault
        right = replace(right, instrument=replace(right.instrument, **{key: "OTHER"}))
    else:
        right = perp(quantity="1")  # One contract is 0.01 BTC, not one BTC.
    with pytest.raises(DataError):
        DerivativeWatch(watcher).record_basis(left, right, NOW)
    assert not events(watcher)


def test_missing_basis_cancels_confirmation_without_fake_recovery(tmp_path):
    watcher = watch(tmp_path)
    metric = DerivativeWatch(watcher)
    metric.record_basis(book(), perp(), NOW)
    metric.invalidate("basis", "BTC/USDT")
    for t in (60000, 75000):
        metric.record_basis(book(t), perp(t), NOW + t)
    assert not events(watcher)
    for t in (90000, 105000, 120000):
        metric.record_basis(book(t), perp(t), NOW + t)
    assert events(watcher)[0]["status"] == "BASIS_ENTERED"


def test_funding_uses_actual_interval_sign_and_distinct_publications(tmp_path):
    watcher = watch(tmp_path)
    metric = DerivativeWatch(watcher)
    metric.record_funding(funding(), NOW)
    metric.record_funding(funding(), NOW + 15000)  # The cached publication does not advance time.
    assert not events(watcher)
    metric.record_funding(funding(60000), NOW + 60000)
    alert = events(watcher)[0]
    assert alert["status"] == "FUNDING_ENTERED"
    assert D(alert["details"]["value_percent"]) == D("0.16")
    assert D(alert["details"]["raw_rate_percent"]) == D("0.02")
    assert "预估" in alert["details"]["note"]
    metric.record_funding(funding(120000, "-0.0002"), NOW + 120000)
    metric.record_funding(funding(180000, "-0.0002"), NOW + 180000)
    assert events(watcher)[-1]["details"]["positive_rate"] == "空头支付多头"


@pytest.mark.parametrize("fault", ["gap", "period_change", "invalidation"])
def test_funding_streak_cannot_cross_gap_or_period_change(tmp_path, fault):
    watcher = watch(tmp_path)
    metric = DerivativeWatch(watcher)
    metric.record_funding(funding(), NOW)
    t, rate, interval = 60000, "0.0002", 3600000
    if fault == "gap":
        t = 180001
    elif fault == "period_change":
        rate, interval = "0.0004", 7200000
    else:
        metric.invalidate("funding", funding().instrument.symbol)
    metric.record_funding(funding(t, rate, interval), NOW + t)
    assert not events(watcher)
    metric.record_funding(funding(t + 60000, rate, interval), NOW + t + 60000)
    assert events(watcher)[0]["status"] == "FUNDING_ENTERED"


@pytest.mark.parametrize("fault", ["past_settlement", "future", "missing_period", "stale"])
def test_bad_funding_cannot_create_alerts(tmp_path, fault):
    observation = funding()
    changes = {
        "past_settlement": {"next_settlement_ms": NOW},
        "future": {"source_ms": NOW + 1},
        "missing_period": {"interval_ms": None},
        "stale": {"source_ms": NOW - 30001},
    }
    with pytest.raises(DataError):
        DerivativeWatch(watch(tmp_path)).record_funding(replace(observation, **changes[fault]), NOW)


def test_metric_event_and_state_rollback_together(tmp_path, monkeypatch):
    watcher = watch(tmp_path)
    metric = DerivativeWatch(watcher)
    metric.record_funding(funding(), NOW)
    original = Outbox.enqueue_in

    def fail(*_):
        raise RuntimeError("synthetic write fault")

    monkeypatch.setattr(Outbox, "enqueue_in", staticmethod(fail))
    with pytest.raises(RuntimeError):
        metric.record_funding(funding(60000), NOW + 60000)
    assert metric.summary()["funding:BTC/USDT:USDT"]["active"] == 0
    monkeypatch.setattr(Outbox, "enqueue_in", staticmethod(original))
    assert metric.record_funding(funding(60000), NOW + 60000) == 1


def test_session_does_not_restamp_normal_funding_wait_as_fresh(tmp_path):
    watcher = watch(tmp_path)
    stream = SimpleNamespace(latest={"BTC/USDT:USDT": funding()}, errors=set())
    session = RiskSession(watcher, stream, NOW)
    fresh = session._funding("BTC/USDT:USDT", NOW)
    waiting = session._funding("BTC/USDT:USDT", NOW + 45000)
    assert fresh["status"] == "fresh"
    assert waiting["status"] == "waiting_for_fresh_publication"
    assert waiting["source_ms"] == NOW
    assert session.metrics.summary()["funding:BTC/USDT:USDT"]["source_ms"] == NOW
    stream.latest["BTC/USDT:USDT"] = replace(funding(), source_ms=NOW + 1)
    assert session._funding("BTC/USDT:USDT", NOW)["status"] == "unavailable"


def test_price_monitor_continues_when_derivative_sources_are_missing(tmp_path):
    watcher = watch(tmp_path)
    session = RiskSession(watcher, None, NOW)
    for t in range(0, 300000, 15000):
        scan = ScanReport(ObservationSet(NOW + t, (book(t),), ()), (), (), (), ())
        session.process(scan, NOW + t)
    for t in (300000, 315000, 330000):
        scan = ScanReport(ObservationSet(NOW + t, (book(t, "104"),), ()), (), (), (), ())
        session.process(scan, NOW + t)
    assert any(e["status"] == "ENTERED" for e in events(watcher))


def test_funding_background_tasks_keep_only_latest_and_cancel_cleanly():
    async def check():
        blocker = asyncio.Event()
        calls = 0

        async def read(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                return funding()
            await blocker.wait()

        stream = FundingStream(SimpleNamespace(watch_funding=read), (funding().instrument,))
        stream.start()
        await asyncio.sleep(0.02)
        assert len(stream.latest) == 1 and not stream.errors
        await stream.close()
        assert not stream.tasks

    asyncio.run(check())


@pytest.mark.parametrize("fault", ["network", "identity"])
def test_funding_stream_errors_are_isolated_and_do_not_publish_bad_cache(fault):
    async def check():
        async def read(_):
            if fault == "network":
                raise TimeoutError("synthetic network fault")
            return replace(funding(), instrument=replace(funding().instrument, base="OTHER"))

        stream = FundingStream(SimpleNamespace(watch_funding=read), (funding().instrument,))
        stream.start()
        await asyncio.sleep(0.02)
        assert not stream.latest and stream.errors == {funding().instrument.symbol}
        await stream.close()

    asyncio.run(check())


def test_compact_derivatives_discovery_has_only_four_explicit_public_markets():
    async def check():
        selected = {}
        seen = []
        client = SimpleNamespace(
            options={}, has={"fetchOrderBook": True, "watchFundingRate": True}, close=AsyncMock()
        )

        async def fetch(params):
            native = params["instId"]
            swap = native.endswith("-SWAP")
            base = native.split("-")[0]
            symbol = f"{base}/USDT" + (":USDT" if swap else "")
            seen.append((native, client.options["fetchMarkets"]["types"][:]))
            return [
                {
                    "id": native,
                    "symbol": symbol,
                    "base": base,
                    "quote": "USDT",
                    "spot": not swap,
                    "swap": swap,
                    "linear": swap,
                    "active": True,
                    "contractSize": "0.01" if swap else None,
                    "settle": "USDT" if swap else None,
                }
            ]

        def set_markets(rows):
            selected.update({r["symbol"]: r for r in rows})

        client.fetch_markets = fetch
        client.set_markets = Mock(side_effect=set_markets)
        client.load_markets = AsyncMock(side_effect=lambda: selected)
        adapter, instruments = await build_compact_okx(
            ("BTC/USDT", "ETH/USDT"), client, derivatives=True
        )
        assert len(instruments) == 4 and len(selected) == 4
        assert seen == [
            ("BTC-USDT", ["spot"]),
            ("ETH-USDT", ["spot"]),
            ("BTC-USDT-SWAP", ["swap"]),
            ("ETH-USDT-SWAP", ["swap"]),
        ]
        await adapter.close()

    asyncio.run(check())


def test_online_backups_are_consistent_due_once_daily_and_keep_previous(tmp_path):
    watcher = watch(tmp_path)
    watcher.record(book(), NOW)
    root = tmp_path / "backups"
    assert backup_if_due(watcher.outbox, root, NOW)
    os.utime(root / "latest.sqlite3", (NOW / 1000, NOW / 1000))
    assert not backup_if_due(watcher.outbox, root, NOW + 1000)
    watcher.record(book(15000), NOW + 15000)
    assert backup_if_due(watcher.outbox, root, NOW + 86400000)
    for name, count in (("latest.sqlite3", 2), ("previous.sqlite3", 1)):
        with sqlite3.connect(root / name) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("SELECT COUNT(*) FROM risk_samples").fetchone()[0] == count


def test_additive_config_migration_preserves_price_history_and_rejects_rule_tuning(tmp_path):
    script = runpy.run_path("scripts/migrate-bot-config.py")
    new = Path("config/bot.toml")
    old = tmp_path / "old.toml"
    old.write_text(new.read_text().split("\n[basis]")[0], encoding="utf-8")
    root = tmp_path / "state"
    watcher = RiskWatch(Outbox(root / "data/bot-b.sqlite3"), RiskWatchConfig.load(old))
    watcher.record(book(), NOW)
    with instance_lock(root / "service.lock"), pytest.raises(OSError):
        script["migrate"](root, old, new)
    script["migrate"](root, old, new)
    assert (
        RiskWatch(watcher.outbox, configuration()).summary()["samples"]["BTC/USDT"]["samples"] == 1
    )
    with watcher.outbox.connect() as db:
        assert (
            db.execute("SELECT value FROM notification_meta WHERE key='risk_config'").fetchone()[0]
            == hashlib.sha256(new.read_bytes()).hexdigest()
        )
    changed = tmp_path / "tuned.toml"
    changed.write_text(new.read_text().replace('threshold = "0.05"', 'threshold = "0.02"'))
    with pytest.raises(ValueError, match="Only additive"):
        script["migrate"](root, old, changed)
