"""Offline real-USD failover and persistent recovery debounce; no real Telegram."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import NOW
from test_bot_b_reliability import watcher
from test_risk_watch import events
from test_stablecoin_watch import book

from crypto_market_monitor.bot_b_migrate import migrate
from crypto_market_monitor.market_data.contracts import ObservationSet
from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.risk_session import RiskSession
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.market_data.scanner import ScanReport
from crypto_market_monitor.market_data.stablecoin_watch import record_stablecoin
from crypto_market_monitor.models import DataError
from crypto_market_monitor.notification_store import Outbox


def usd(t, *, venue="bitstamp", abnormal=False):
    b = book(t, "0.993" if abnormal else "0.9998", "0.994" if abnormal else "1.0001", "USDC/USD")
    return (
        replace(b, instrument=replace(b.instrument, venue=venue, native_id="USDCUSD"))
        if venue == "kraken"
        else b
    )


def process(session, t, primary=None, fallback=None):
    return session.process(
        ScanReport(ObservationSet(NOW + t, (), ()), (), (), (), ()),
        NOW + t,
        {"USDC/USD": primary} if primary else {},
        None,
        {"USDC/USD": fallback} if fallback else {},
    )


def test_stale_bitstamp_uses_real_usd_fallback_and_stays_across_restart(tmp_path):
    w = watcher(tmp_path)
    session = RiskSession(w, None, NOW)
    process(session, 0, usd(0))
    state = process(session, 45000, usd(0), usd(45000, venue="kraken"))
    assert state["stablecoin_feed"]["USDC/USD"] == "fresh"
    assert state["stablecoin_details"]["USDC/USD"]["source"] == "kraken-public"
    session = RiskSession(watcher(tmp_path), None, NOW + 45000)
    state = process(session, 60000, usd(60000), usd(60000, venue="kraken"))
    assert state["stablecoin_details"]["USDC/USD"]["source"] == "kraken-public"
    assert not any(e["symbol"] == "stablecoin:USDC/USD" for e in events(w))


def test_source_switch_restarts_risk_confirmation_and_labels_source(tmp_path):
    w = watcher(tmp_path)
    session = RiskSession(w, None, NOW)
    for t in (0, 15000, 30000):
        process(session, t, usd(t, abnormal=True))
    for t in (45000, 60000, 75000, 90000):
        process(session, t, None, usd(t, venue="kraken", abnormal=True))
    assert not any(e["status"] == "STABLECOIN_ENTERED" for e in events(w))
    process(session, 105000, None, usd(105000, venue="kraken", abnormal=True))
    risk = [e for e in events(w) if e["status"] == "STABLECOIN_ENTERED"]
    assert len(risk) == 1 and risk[0]["source"] == "kraken-public"


def test_both_sources_stale_do_not_recover_active_risk(tmp_path):
    w = watcher(tmp_path)
    session = RiskSession(w, None, NOW)
    for t in range(0, 75000, 15000):
        process(session, t, usd(t, abnormal=True))
    state = process(
        session,
        105000,
        replace(usd(60000), received_ms=NOW + 105000),
        replace(usd(60000, venue="kraken"), received_ms=NOW + 105000),
    )
    assert state["stablecoin_feed"]["USDC/USD"] == "unavailable"
    assert "stale source time" in state["stablecoin_details"]["USDC/USD"]["reason"]
    with w.outbox.connect() as db:
        assert tuple(
            db.execute(
                "SELECT phase,active FROM bot_b_risks WHERE key='stablecoin:USDC/USD'"
            ).fetchone()
        ) == ("DATA_STALE", -1)
    assert not any(e["status"] == "STABLECOIN_RECOVERED" for e in events(w))


def test_unenabled_or_wrong_kraken_identity_rejected(tmp_path):
    w = watcher(tmp_path)
    m = DerivativeWatch(w)
    b = usd(0, venue="kraken")
    with pytest.raises(DataError):
        record_stablecoin(m, replace(b, instrument=replace(b.instrument, native_id="OTHER")), NOW)
    m.config = replace(m.config, reliability=replace(m.config.reliability, usdc_fallback=False))
    with pytest.raises(DataError):
        record_stablecoin(m, b, NOW)


def test_recovery_debounce_persists_and_flapping_keeps_original_outage(tmp_path):
    w = watcher(tmp_path)
    key = "stablecoin:USDC/USD"
    for t in (0, 15000, 30000):
        w.feed_health(key, False, NOW + t)
    w.feed_health(key, True, NOW + 45000)
    w.feed_health(key, False, NOW + 60000)
    w.feed_health(key, True, NOW + 75000)
    w.feed_health(key, True, NOW + 90000)
    w = watcher(tmp_path)
    for t in (105000, 120000):
        w.feed_health(key, True, NOW + t)
    assert [e["status"] for e in events(w)] == ["DATA_UNAVAILABLE"]
    w.feed_health(key, True, NOW + 135000)
    assert [e["status"] for e in events(w)] == ["DATA_UNAVAILABLE", "DATA_RECOVERED"]
    assert events(w)[-1]["details"]["outage_duration_ms"] == 135000
    w.feed_health(key, True, NOW + 150000)
    assert len(events(w)) == 2


def test_recovery_gap_cannot_count_as_continuous_success(tmp_path):
    w = watcher(tmp_path)
    for t in (0, 15000, 30000):
        w.feed_health("test", False, NOW + t)
    w.feed_health("test", True, NOW + 45000)
    w = watcher(tmp_path)
    w.feed_health("test", True, NOW + 120000)
    assert len(events(w)) == 1


def test_reliability_only_migration_preserves_existing_records(tmp_path):
    new = Path("config/bot-b-phase2-3.toml")
    old = tmp_path / "old.toml"
    old.write_text(
        new.read_text(encoding="utf-8")
        .replace("usdc_fallback = true", "usdc_fallback = false")
        .replace("data_recovery_ms = 60000", "data_recovery_ms = 0"),
        encoding="utf-8",
    )
    root = tmp_path / "state"
    w = RiskWatch(Outbox(root / "data/bot-b.sqlite3"), RiskWatchConfig.load(old))
    DerivativeWatch(w)
    w.feed_health("test", False, NOW)
    assert migrate(root, old, new)["status"] == "migrated"
    RiskWatch(w.outbox, RiskWatchConfig.load(new))
    with w.outbox.connect() as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM risk_health")] == [("test", 1, 0)]
    assert migrate(root, old, new)["status"] == "already_migrated"


def test_optional_fallback_reconnects_and_closes_without_bitstamp(monkeypatch):
    from crypto_market_monitor.market_data import stablecoin_feed as module

    async def run():
        delivered, hold = asyncio.Event(), asyncio.Event()
        adapter = SimpleNamespace(close=AsyncMock())
        calls = 0

        async def fetch(i):
            nonlocal calls
            calls += 1
            if calls == 1:
                return usd(0, venue="kraken")
            delivered.set()
            await hold.wait()

        adapter.watch_book = fetch
        build = AsyncMock(
            side_effect=[
                DataError("offline synthetic"),
                (adapter, (usd(0, venue="kraken").instrument,)),
            ]
        )
        monkeypatch.setattr(module, "build_compact_kraken", build)
        monkeypatch.setattr(module, "build_compact_bitstamp", AsyncMock(side_effect=DataError()))
        feed = module.StablecoinFeed(0, usdc_fallback=True)
        feed.start()
        try:
            await asyncio.wait_for(delivered.wait(), 3)
            assert "USDC/USD" in feed.fallback_latest and not feed.latest
        finally:
            await feed.close()
        adapter.close.assert_awaited_once()
        assert not feed.fallback_latest and feed.fallback_task is None

    asyncio.run(run())
