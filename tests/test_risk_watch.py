import asyncio
import json
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import NOW

from crypto_market_monitor.adapters.compact_okx import build_compact_okx
from crypto_market_monitor.market_data.models import Instrument, Level, MarketKind, OrderBook
from crypto_market_monitor.market_data.risk_watch import MoveState, RiskWatch, transition
from crypto_market_monitor.market_data.risk_watch_config import MoveRule, RiskWatchConfig
from crypto_market_monitor.models import DataError
from crypto_market_monitor.notification_store import Outbox


def configuration():
    return replace(
        RiskWatchConfig.load(Path("config/bot-b.toml")),
        rules=(MoveRule(300000, D("0.03"), D("0.015")), MoveRule(900000, D("0.05"), D("0.025"))),
    )


def book(t=0, price="100", **kwargs):
    instrument = Instrument("okx", "BTC/USDT", "BTC-USDT", MarketKind.SPOT, "BTC", "USDT")
    return OrderBook(
        instrument,
        (Level(D(price), D(2)),),
        (Level(D(price), D(2)),),
        kwargs.get("source_ms", NOW + t),
        NOW + t,
        "synthetic-test-only",
    )


def watch(tmp_path):
    return RiskWatch(Outbox(tmp_path / "b.sqlite3"), configuration())


def warm(watcher):
    for t in range(0, 300000, 15000):
        assert watcher.record(book(t), NOW + t) == 0


def events(watcher):
    with watcher.outbox.connect() as db:
        return [
            json.loads(r[0])
            for r in db.execute("SELECT body FROM notification_outbox ORDER BY rowid")
        ]


def test_warmup_confirmation_restart_duplicate_and_recovery(tmp_path):
    watcher = watch(tmp_path)
    warm(watcher)
    assert watcher.record(book(300000, "104"), NOW + 300000) == 0
    assert watcher.record(book(315000, "104"), NOW + 315000) == 0
    watcher = watch(tmp_path)  # Restart preserves the real continuous history and candidate.
    assert watcher.record(book(330000, "104"), NOW + 330000) == 1
    assert watcher.record(book(330000, "104"), NOW + 330000) == 0
    assert watcher.record(book(329999, "104"), NOW + 330000) == 0
    for t in (345000, 360000, 375000):
        watcher.record(book(t), NOW + t)
    assert [e["status"] for e in events(watcher)] == ["ENTERED", "RECOVERED"]
    entered = events(watcher)[0]
    assert entered["details"]["change_percent"] == "4.00"
    assert entered["details"]["bot_channel"] == "B"
    assert entered["mode"] == "MONITOR"


def test_gap_rewarms_and_never_invents_recovery(tmp_path):
    watcher = watch(tmp_path)
    warm(watcher)
    for t in (300000, 315000, 330000):
        watcher.record(book(t, "104"), NOW + t)
    watcher.record(book(400000, "100"), NOW + 400000)
    assert [e["status"] for e in events(watcher)] == ["ENTERED", "DATA_GAP_REWARMING"]
    assert watcher.summary()["samples"]["BTC/USDT"]["samples"] == 1
    with watcher.outbox.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM risk_state").fetchone()[0] == 0


@pytest.mark.parametrize("stamp", [None, NOW + 1, NOW - 30001])
def test_bad_source_times_never_enter_history(tmp_path, stamp):
    watcher = watch(tmp_path)
    with pytest.raises(DataError):
        watcher.record(book(source_ms=stamp), NOW)
    assert watcher.summary()["samples"] == {}


def test_state_and_outbox_commit_atomically(tmp_path, monkeypatch):
    watcher = watch(tmp_path)
    warm(watcher)
    for t in (300000, 315000):
        watcher.record(book(t, "104"), NOW + t)
    original = Outbox.enqueue_in
    monkeypatch.setattr(
        Outbox, "enqueue_in", staticmethod(Mock(side_effect=RuntimeError("disk fault")))
    )
    with pytest.raises(RuntimeError):
        watcher.record(book(330000, "104"), NOW + 330000)
    assert watcher.summary()["samples"]["BTC/USDT"]["last_ms"] == NOW + 315000
    assert not events(watcher)
    monkeypatch.setattr(Outbox, "enqueue_in", staticmethod(original))
    assert watcher.record(book(330000, "104"), NOW + 330000) == 1


def test_escalation_hysteresis_and_cooldown():
    config = configuration()
    rule = config.rules[0]
    state = MoveState(1, 1, NOW, NOW)
    state, status = transition(state, D("0.07"), rule, NOW + 15000, config)
    assert status is None
    state, status = transition(state, D("0.07"), rule, NOW + 45000, config)
    assert state.active == 2 and status == "ESCALATED"
    state, status = transition(state, D("0.02"), rule, NOW + 60000, config)
    assert state.active == 2 and status is None
    state = MoveState(0, 1, NOW, NOW)
    state, status = transition(state, D("0.04"), rule, NOW + 30000, config)
    assert state.active == 0 and status is None
    state, status = transition(state, D("0.04"), rule, NOW + config.cooldown_ms, config)
    assert state.active == 1 and status == "ENTERED"


def test_history_and_event_retention_are_bounded(tmp_path):
    watcher = watch(tmp_path)
    for t in range(0, 3_600_000, 15000):
        watcher.record(book(t), NOW + t)
    assert watcher.summary()["samples"]["BTC/USDT"]["samples"] <= 64
    event = watcher.event("BTC/USDT", NOW, "ENTERED", {}, key="fixture")
    watcher.outbox.enqueue(event)
    watcher.prune(NOW + 300001)
    with watcher.outbox.connect() as db:
        assert db.execute("SELECT state FROM notification_outbox").fetchone()[0] == "expired"


def test_health_notifications_do_not_flood_and_recover(tmp_path):
    watcher = watch(tmp_path)
    for i in range(20):
        watcher.feed_health("BTC/USDT", False, NOW + i * 15000)
    watcher.feed_health("BTC/USDT", True, NOW + 300000)
    assert [e["status"] for e in events(watcher)] == ["DATA_UNAVAILABLE", "DATA_RECOVERED"]


def test_changed_parameters_and_wrong_bot_are_rejected(tmp_path):
    watcher = watch(tmp_path)
    with pytest.raises(ValueError, match="configuration changed"):
        RiskWatch(watcher.outbox, replace(configuration(), fingerprint="changed"))
    with pytest.raises(ValueError, match="another Bot"):
        watcher.outbox.enqueue(
            replace(watcher.event("BTC", NOW, "TEST", {}, key="test"), details={"bot_channel": "A"})
        )


def test_compact_discovery_only_loads_explicit_spot_markets_and_closes_on_fault():
    async def check():
        row = {
            "id": "BTC-USDT",
            "symbol": "BTC/USDT",
            "base": "BTC",
            "quote": "USDT",
            "spot": True,
            "active": True,
        }
        client = SimpleNamespace(
            options={},
            has={},
            fetch_markets=AsyncMock(return_value=[row]),
            set_markets=Mock(),
            load_markets=AsyncMock(return_value={"BTC/USDT": row}),
            close=AsyncMock(),
        )
        adapter, instruments = await build_compact_okx(("BTC/USDT",), client)
        client.fetch_markets.assert_awaited_once_with({"instId": "BTC-USDT"})
        client.set_markets.assert_called_once_with([row])
        assert client.options["fetchMarkets"] == {"types": ["spot"]}
        assert instruments[0].venue == "okx"
        await adapter.close()
        client.fetch_markets.return_value = [row, row]
        with pytest.raises(DataError):
            await build_compact_okx(("BTC/USDT",), client)
        assert client.close.await_count == 2

    asyncio.run(check())


@pytest.mark.parametrize(
    "substitution",
    [
        ('threshold = "0.05"', 'threshold = "NaN"'),
        ("poll_seconds = 15", "poll_seconds = 0"),
        ('mode = "market-risk"', 'mode = "live"'),
        ('"ETH/USDT"', '"SOL/USDT"'),
    ],
)
def test_unsafe_config_is_rejected(tmp_path, substitution):
    path = tmp_path / "bad.toml"
    path.write_text(Path("config/bot-b.toml").read_text().replace(*substitution), encoding="utf-8")
    with pytest.raises(ValueError):
        RiskWatchConfig.load(path)
