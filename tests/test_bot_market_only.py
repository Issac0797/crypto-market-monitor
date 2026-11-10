"""Market alerts stay enabled when transport diagnostics are kept off Telegram."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import NOW
from test_bot_phase1 import book as usde_book
from test_derivative_watch import book, funding, perp
from test_risk_watch import events
from test_stablecoin_watch import book as stable_book

from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.market_data.stablecoin_watch import record_stablecoin
from crypto_market_monitor.market_data.usde_watch import record_usde
from crypto_market_monitor.models import DataError
from crypto_market_monitor.notification_store import Outbox


def make_watcher(tmp_path, *, data_notifications=False):
    return RiskWatch(
        Outbox(tmp_path / "b.sqlite3"),
        RiskWatchConfig.load(Path("config/bot-phase2-3.toml")),
        data_notifications=data_notifications,
    )


def test_data_outage_and_recovery_stay_in_health_without_telegram(tmp_path, caplog):
    watcher = make_watcher(tmp_path)
    for t in range(0, 150000, 15000):
        watcher.feed_health("BTC/USDT", False, NOW + t)
    with watcher.outbox.connect() as db:
        assert tuple(db.execute("SELECT failures,alerted FROM risk_health").fetchone()) == (10, 1)
        assert db.execute("SELECT failed_since_ms FROM bot_b_feed_details").fetchone()[0] == NOW
    for t in range(150000, 225000, 15000):
        watcher.feed_health("BTC/USDT", True, NOW + t)
    with watcher.outbox.connect() as db:
        assert tuple(db.execute("SELECT failures,alerted FROM risk_health").fetchone()) == (0, 0)
        assert db.execute("SELECT failed_since_ms FROM bot_b_feed_details").fetchone()[0] is None
    assert not events(watcher)
    assert "DATA_UNAVAILABLE" in caplog.text


def test_restart_suppresses_only_pending_data_messages_and_keeps_market_alerts(tmp_path):
    watcher = make_watcher(tmp_path, data_notifications=True)
    for t in (0, 15000, 30000):
        watcher.feed_health("BTC/USDT", False, NOW + t)
    market = watcher.event("BTC/USDT", NOW + 30000, "ENTERED", {}, key="market")
    watcher.outbox.enqueue(market)
    watcher = make_watcher(tmp_path)
    with watcher.outbox.connect() as db:
        rows = {
            row[0]: row[1]
            for row in db.execute(
                "SELECT json_extract(body,'$.status'),state FROM notification_outbox"
            )
        }
    assert rows == {"DATA_UNAVAILABLE": "suppressed", "ENTERED": "pending"}


def test_price_alert_still_fires_but_gap_rewarms_silently_and_retains_active(tmp_path):
    watcher = make_watcher(tmp_path)
    for t in range(0, 900000, 15000):
        watcher.record(book(t), NOW + t)
    for t in (900000, 915000, 930000):
        watcher.record(book(t, "106"), NOW + t)
    assert [e["status"] for e in events(watcher)] == ["ENTERED"]
    assert watcher.record(book(1000000), NOW + 1000000) == 0
    assert [e["status"] for e in events(watcher)] == ["ENTERED"]
    assert watcher.summary()["samples"]["BTC/USDT"]["samples"] == 1
    with watcher.outbox.connect() as db:
        assert tuple(
            db.execute(
                "SELECT phase,active FROM bot_b_risks WHERE key='move:BTC/USDT:900000'"
            ).fetchone()
        ) == ("DATA_STALE", 1)
    with pytest.raises(DataError):
        watcher.record(book(1000000), NOW + 1030001)


def test_basis_trigger_escalation_and_recovery_are_still_delivered(tmp_path):
    watcher = make_watcher(tmp_path)
    metric = DerivativeWatch(watcher)
    for t in range(0, 75000, 15000):
        metric.record_basis(book(t), perp(t), NOW + t)
    for t in range(75000, 150000, 15000):
        metric.record_basis(book(t), perp(t, "103"), NOW + t)
    for t in range(150000, 225000, 15000):
        metric.record_basis(book(t), perp(t, "100"), NOW + t)
    assert [e["status"] for e in events(watcher)] == [
        "BASIS_ENTERED",
        "BASIS_ESCALATED",
        "BASIS_RECOVERED",
    ]


@pytest.mark.parametrize("kind", ["funding", "stablecoin", "usde"])
def test_other_market_alerts_are_not_muted_with_data_diagnostics(tmp_path, kind):
    watcher = make_watcher(tmp_path)
    metric = DerivativeWatch(watcher)
    for t in range(0, 75000, 15000):
        if kind == "funding":
            metric.record_funding(funding(t), NOW + t)
        elif kind == "stablecoin":
            record_stablecoin(metric, stable_book(t), NOW + t)
        else:
            record_usde(metric, usde_book(t, "0.994"), None, None, NOW + t)
    assert [e["status"] for e in events(watcher)] == [f"{kind.upper()}_ENTERED"]


def test_cli_defaults_to_market_notifications_and_explicit_diagnostics(monkeypatch, tmp_path):
    from crypto_market_monitor import risk_watch_cli

    calls = []

    async def serve(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(risk_watch_cli, "serve", serve)
    for extra in ([], ["--data-notifications"]):
        monkeypatch.setattr(
            "sys.argv",
            [
                "bot-b",
                "--config",
                "config/bot-phase2-3.toml",
                "--state-dir",
                str(tmp_path.resolve()),
                "--once",
                *extra,
            ],
        )
        risk_watch_cli.main()
    assert [kwargs["data_notifications"] for _, kwargs in calls] == [False, True]


def test_okx_refresh_network_failure_does_not_abort_sampling(monkeypatch):
    from crypto_market_monitor.adapters import compact_okx

    async def run():
        original = (book().instrument,)
        client = SimpleNamespace(
            options={"fetchMarkets": {"types": ["spot"]}},
            fetch_markets=AsyncMock(side_effect=OSError("synthetic metadata outage")),
        )
        adapter = SimpleNamespace(_client=client)
        assert (
            await compact_okx.refresh_compact_okx_safely(
                adapter, ("BTC/USDT",), original, derivatives=False
            )
            is False
        )
        assert client.options["fetchMarkets"] == {"types": ["spot"]}

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["identity", "invalid", "cancel"])
def test_metadata_refresh_still_rejects_invalid_identity_and_propagates_cancel(monkeypatch, fault):
    from crypto_market_monitor.adapters import compact_okx

    async def run():
        original = (book().instrument,)
        previous = {"types": ["spot", "swap"]}
        adapter = SimpleNamespace(_client=SimpleNamespace(options={"fetchMarkets": previous}))
        if fault == "identity":
            refresh = AsyncMock(return_value=(perp().instrument,))
            expected = DataError
        else:
            expected = DataError if fault == "invalid" else asyncio.CancelledError
            refresh = AsyncMock(side_effect=expected())
        monkeypatch.setattr(compact_okx, "refresh_compact_okx", refresh)
        with pytest.raises(expected):
            await compact_okx.refresh_compact_okx_safely(
                adapter, ("BTC/USDT",), original, derivatives=True
            )
        assert adapter._client.options["fetchMarkets"] is previous

    asyncio.run(run())
