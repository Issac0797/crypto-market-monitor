import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

import pytest
from conftest import NOW

from crypto_market_monitor.bot_commands import BotCommands, BotTelegramAPI
from crypto_market_monitor.market_data.models import Instrument, Level, MarketKind, OrderBook
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.risk_watch_config import MoveRule, RiskWatchConfig
from crypto_market_monitor.notification_store import Outbox


def config():
    return RiskWatchConfig.load(Path("config/bot-phase4.toml"))


def book(symbol="BTC/USDT", t=0, price="100"):
    base, quote = symbol.split("/")
    instrument = Instrument("okx", symbol, symbol.replace("/", "-"), MarketKind.SPOT, base, quote)
    return OrderBook(
        instrument,
        (Level(D(price), D(2)),),
        (Level(D(price), D(2)),),
        NOW + t,
        NOW + t,
        "synthetic-test-only",
    )


def watcher(tmp_path):
    return RiskWatch(Outbox(tmp_path / "b.sqlite3"), config())


def events(watcher):
    with watcher.outbox.connect() as db:
        return [
            json.loads(row[0])
            for row in db.execute("SELECT body FROM notification_outbox ORDER BY rowid")
        ]


def test_phase4_only_adds_the_five_minute_price_rule():
    old = RiskWatchConfig.load(Path("config/bot-phase2-3.toml"))
    new = config()
    for name in old.__dataclass_fields__:
        if name not in {"rules", "fingerprint"}:
            assert getattr(old, name) == getattr(new, name)
    assert new.rules == (
        MoveRule(300000, D("0.01"), D("0.005")),
        MoveRule(900000, D("0.05"), D("0.025")),
    )
    assert new.fingerprint != old.fingerprint
    assert hashlib.sha256(Path("config/bot-phase2-3.toml").read_bytes()).hexdigest() == (
        "9cee084e6c47e92108667a800d7ea77680da0b8c6e53ae59f7e61c9e83b9cee3"
    )
    assert (
        new.fingerprint == hashlib.sha256(Path("config/bot-phase4.toml").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT"])
@pytest.mark.parametrize("direction", [1, -1])
def test_five_minute_move_confirms_and_shares_no_state_with_fifteen_minutes(
    tmp_path, symbol, direction
):
    w = watcher(tmp_path)
    for t in range(0, 300001, 15000):
        assert w.record(book(symbol, t), NOW + t) == 0

    moved = "101" if direction > 0 else "99"
    recovered = "100.5" if direction > 0 else "99.5"
    assert w.record(book(symbol, 315000, moved), NOW + 315000) == 0
    assert w.record(book(symbol, 330000, moved), NOW + 330000) == 0
    assert w.record(book(symbol, 345000, moved), NOW + 345000) == 1

    entered = events(w)
    assert [row["status"] for row in entered] == ["ENTERED"]
    assert entered[0]["event_id"] == (f"engine:bot-b:move:300000:ENTERED:{symbol}:{NOW + 345000}")
    assert entered[0]["details"]["window_minutes"] == 5
    assert abs(D(str(entered[0]["details"]["change_percent"]))) == D("1")
    assert D(str(entered[0]["details"]["threshold_percent"])) == D("1.0")
    assert entered[0]["details"]["direction"] == ("up" if direction > 0 else "down")
    with w.outbox.connect() as db:
        states = {
            window: active
            for window, active in db.execute(
                "SELECT window,active FROM risk_state WHERE symbol=?", (symbol,)
            )
        }
    assert states == {300000: direction}

    for t in (360000, 375000, 390000):
        w.record(book(symbol, t, recovered), NOW + t)
    assert events(w)[-1]["status"] == "RECOVERED"
    assert events(w)[-1]["details"]["window_minutes"] == 5
    assert all(row["details"].get("window_minutes") != 15 for row in events(w))


def test_sub_threshold_five_minute_moves_do_not_alert(tmp_path):
    w = watcher(tmp_path)
    for t in range(0, 300001, 15000):
        w.record(book(t=t), NOW + t)
    for t in (315000, 330000, 345000, 360000):
        w.record(book(t=t, price="100.9"), NOW + t)
    assert not events(w)
    assert w.summary()["outbox"] == {}


def test_settings_exposes_both_anomaly_windows(tmp_path):
    w = watcher(tmp_path)
    commands = BotCommands(
        BotTelegramAPI("123:synthetic", 10, {10}, profile="B"),
        w,
        tmp_path,
    )
    reply = commands.reply("/settings", NOW)
    assert "[(5, '1.00%'), (15, '5.00%')]" in reply
