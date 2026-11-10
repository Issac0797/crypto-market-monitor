"""Offline USDe cross quotes retain the real, independent version of each leg."""

from dataclasses import replace
from pathlib import Path

import pytest
from conftest import NOW
from test_bot_phase1 import book
from test_risk_watch import events

from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.market_data.usde_watch import record_usde
from crypto_market_monitor.models import DataError
from crypto_market_monitor.notification_store import Outbox


def metrics(root, *, reliable=True):
    name = "bot-phase2-3.toml" if reliable else "bot-phase1.toml"
    config = RiskWatchConfig.load(Path("config") / name)
    return DerivativeWatch(RiskWatch(Outbox(root / "b.sqlite3"), config))


def cross(m, left_ms, right_ms, *, left="0.994", right="1", now=None):
    now = max(left_ms, right_ms) if now is None else now
    left_book = replace(
        book(left_ms, left, symbol="USDE/USDT", venue=m.config.usde.cross_venue),
        received_ms=NOW + now,
    )
    right_book = replace(
        book(right_ms, right, symbol="USDT/USD", venue="bitstamp"),
        received_ms=NOW + now,
    )
    return record_usde(m, None, left_book, right_book, NOW + now)


def state(m):
    with m.outbox.connect() as db:
        return db.execute(
            "SELECT last_ms,value,since_ms FROM derivative_state WHERE kind='usde'"
        ).fetchone()


@pytest.mark.parametrize("reliable", [False, True])
def test_newer_leg_price_update_no_longer_conflicts_with_unchanged_older_leg(tmp_path, reliable):
    m = metrics(tmp_path, reliable=reliable)
    cross(m, 0, 0)
    result = cross(m, 0, 5000, right="0.999")
    assert result["status"] == "fresh" and result["source_ms"] == NOW + 5000
    assert state(m)[0] == NOW + 5000 and not events(m.watcher)


@pytest.mark.parametrize("reliable", [False, True])
def test_other_leg_update_at_same_composite_time_is_valid_but_does_not_extend_time(
    tmp_path, reliable
):
    m = metrics(tmp_path, reliable=reliable)
    cross(m, 0, 10000)
    before = state(m)
    cross(m, 5000, 10000, left="0.993")
    after = state(m)
    assert after[0] == before[0] and after[1] != before[1] and after[2] == before[2]
    assert not events(m.watcher)
    for t in (20000, 35000, 50000, 65000):
        cross(m, t, t)
    assert not events(m.watcher)
    cross(m, 70000, 70000)
    assert [e["status"] for e in events(m.watcher)] == ["USDE_ENTERED"]


@pytest.mark.parametrize("fault", ["backwards", "same-time-price", "same-time-depth"])
def test_leg_source_version_rejects_replay_changes_even_after_restart(tmp_path, fault):
    m = metrics(tmp_path)
    cross(m, 5000, 10000)
    m = metrics(tmp_path)
    prior = state(m)
    left = book(5000, "0.994", symbol="USDE/USDT", venue=m.config.usde.cross_venue)
    if fault == "backwards":
        left = replace(left, source_ms=NOW + 4000)
    elif fault == "same-time-price":
        left = book(5000, "0.993", symbol="USDE/USDT", venue=m.config.usde.cross_venue)
    else:
        left = book(
            5000, "0.994", symbol="USDE/USDT", venue=m.config.usde.cross_venue, quantity="20000"
        )
    right = book(14000, symbol="USDT/USD", venue="bitstamp")
    with pytest.raises(DataError, match="leg"):
        record_usde(m, None, left, right, NOW + 15000)
    assert state(m) == prior and not events(m.watcher)


def test_duplicate_legs_and_missing_leg_cannot_complete_confirmation(tmp_path):
    m = metrics(tmp_path)
    cross(m, 0, 10000)
    for t in (15000, 20000, 25000):
        cross(m, 0, 10000, now=t)
    assert state(m)[2] == NOW + 10000 and not events(m.watcher)
    with pytest.raises(DataError):
        record_usde(m, None, book(70000, "0.994", symbol="USDE/USDT"), None, NOW + 70000)
    m.invalidate("usde", "USDE/USD", NOW + 70000)
    for t in (75000, 90000, 105000, 120000):
        cross(m, t, t)
    assert not events(m.watcher)
    cross(m, 135000, 135000)
    assert [e["status"] for e in events(m.watcher)] == ["USDE_ENTERED"]


def test_skew_remains_invalid_and_never_updates_leg_versions(tmp_path):
    m = metrics(tmp_path)
    cross(m, 0, 0)
    with pytest.raises(DataError, match="时间差"):
        cross(m, 10000, 20001)
    assert state(m)[0] == NOW
    with m.outbox.connect() as db:
        assert set(r[0] for r in db.execute("SELECT ms FROM derivative_source_versions")) == {NOW}


def test_source_versions_and_risk_state_rollback_if_notification_enqueue_fails(
    tmp_path, monkeypatch
):
    m = metrics(tmp_path)
    for t in (0, 15000, 30000, 45000):
        cross(m, t, t)

    def fail(*args):
        raise RuntimeError("synthetic storage fault")

    monkeypatch.setattr(m.watcher, "enqueue_in", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        cross(m, 60000, 60000)
    assert state(m)[0] == NOW + 45000
    with m.outbox.connect() as db:
        assert set(r[0] for r in db.execute("SELECT ms FROM derivative_source_versions")) == {
            NOW + 45000
        }


def test_healthy_cross_route_is_not_interrupted_by_brief_direct_recovery(tmp_path):
    m = metrics(tmp_path)
    for t in (0, 15000, 30000, 45000, 60000):
        if t == 30000:
            m = metrics(tmp_path)  # Chosen route also survives a process restart.
        result = record_usde(
            m,
            book(t, "0.994") if t in (15000, 45000) else None,
            book(t, "0.994", symbol="USDE/USDT", venue=m.config.usde.cross_venue),
            book(t, symbol="USDT/USD", venue="bitstamp"),
            NOW + t,
        )
        assert " × " in result["source"]
    assert [e["status"] for e in events(m.watcher)] == ["USDE_ENTERED"]


def test_invalid_current_cross_route_fails_over_without_inventing_risk_recovery(tmp_path):
    m = metrics(tmp_path)
    for t in (0, 15000, 30000, 45000, 60000):
        cross(m, t, t)
    stale_cross = book(30000, "0.994", symbol="USDE/USDT", venue=m.config.usde.cross_venue)
    result = record_usde(
        m,
        book(75000),
        stale_cross,
        book(75000, symbol="USDT/USD", venue="bitstamp"),
        NOW + 75000,
    )
    assert result["source"] == "kraken"
    assert events(m.watcher)[-1]["status"] == "USDE_ENTERED"
    for t in (90000, 105000, 120000, 135000):
        record_usde(m, book(t), None, None, NOW + t)
    assert events(m.watcher)[-1]["status"] == "USDE_RECOVERED"
