"""Offline lifecycle, persistence, queue, polling and migration acceptance."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from conftest import NOW
from test_derivative_watch import book, perp
from test_risk_watch import events

from crypto_market_monitor.bot_commands import BotCommands, BotTelegramAPI
from crypto_market_monitor.bot_migrate import migrate
from crypto_market_monitor.bot_queue import BotNotificationService
from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.notification_store import Outbox
from crypto_market_monitor.operations import instance_lock
from crypto_market_monitor.telegram_transport import TelegramFailure


def config():
    return RiskWatchConfig.load(Path("config/bot-phase2-3.toml"))


def watcher(root):
    return RiskWatch(Outbox(root / "data/bot-b.sqlite3"), config())


def phase(w, key="basis:BTC/USDT"):
    with w.outbox.connect() as db:
        return db.execute(
            "select phase,active,episode_id from bot_b_risks where key=?", (key,)
        ).fetchone()


def sample(m, t, price="101.5"):
    m.record_basis(book(t), perp(t, price), NOW + t)


def test_lifecycle_confirm_upgrade_recovery_new_episode_and_restart(tmp_path):
    w = watcher(tmp_path)
    m = DerivativeWatch(w)
    sample(m, 0, "100")
    assert phase(w)[0] == "NORMAL"
    sample(m, 15000)
    assert phase(w)[0] == "PENDING"
    for t in (30000, 45000, 60000, 75000):
        sample(m, t)
    assert phase(w)[0] == "ACTIVE"
    first = phase(w)[2]
    m = DerivativeWatch(watcher(tmp_path))
    sample(m, 90000)
    assert len(events(w)) == 1
    for t in range(105000, 180000, 15000):
        sample(m, t, "103")
    assert events(w)[-1]["status"] == "BASIS_ESCALATED"
    sample(m, 180000, "100")
    assert phase(w)[0] == "RECOVERING"
    sample(m, 195000, "101.5")
    assert phase(w)[0] == "ACTIVE"
    for t in range(210000, 285000, 15000):
        sample(m, t, "100")
    assert phase(w)[0] == "RESOLVED"
    assert events(w)[-1]["status"] == "BASIS_RECOVERED"
    for t in range(285000, 360000, 15000):
        sample(m, t)
    assert events(w)[-1]["status"] == "BASIS_ENTERED" and phase(w)[2] != first


def test_stale_retains_active_and_recovery_needs_new_confirmation(tmp_path):
    w = watcher(tmp_path)
    m = DerivativeWatch(w)
    for t in range(0, 75000, 15000):
        sample(m, t)
    m.invalidate("basis", "BTC/USDT", NOW + 75000)
    assert tuple(phase(w)[:2]) == ("DATA_STALE", 1)
    m = DerivativeWatch(watcher(tmp_path))
    for t in (90000, 105000, 120000, 135000):
        sample(m, t, "100")
    assert events(w)[-1]["status"] == "BASIS_ENTERED"
    sample(m, 150000, "100")
    assert events(w)[-1]["status"] == "BASIS_RECOVERED"


def test_price_gap_keeps_active_risk_while_rewarming(tmp_path):
    from test_risk_watch import book as price_book

    w = watcher(tmp_path)
    for t in range(0, 900000, 15000):
        w.record(price_book(t), NOW + t)
    for t in (900000, 915000, 930000):
        w.record(price_book(t, "106"), NOW + t)
    assert phase(w, "move:BTC/USDT:900000")[1] == 1
    w.record(price_book(1000000, "106"), NOW + 1000000)
    assert tuple(phase(w, "move:BTC/USDT:900000")[:2]) == ("DATA_STALE", 1)
    assert len([e for e in events(w) if e["status"] == "ENTERED"]) == 1


def test_state_and_queue_failure_rollback_together(tmp_path, monkeypatch):
    w = watcher(tmp_path)
    m = DerivativeWatch(w)
    for t in (0, 15000, 30000, 45000):
        sample(m, t)

    def fail(*a):
        raise RuntimeError("synthetic disk fault")

    monkeypatch.setattr(w, "enqueue_in", fail)
    with pytest.raises(RuntimeError):
        sample(m, 60000)
    assert phase(w)[0] == "PENDING" and not events(w)


def test_migration_preserves_old_active_and_never_realerts_on_restart(tmp_path):
    old = Path("config/bot.toml")
    new = Path("config/bot-phase2-3.toml")
    root = tmp_path / "state"
    w = RiskWatch(Outbox(root / "data/bot-b.sqlite3"), RiskWatchConfig.load(old))
    m = DerivativeWatch(w)
    for t in range(0, 75000, 15000):
        sample(m, t)
    with instance_lock(root / "service.lock"), pytest.raises(OSError):
        migrate(root, old, new)
    assert migrate(root, old, new)["status"] == "migrated"
    assert migrate(root, old, new)["status"] == "already_migrated"
    w = RiskWatch(w.outbox, config())
    m = DerivativeWatch(w)
    assert tuple(phase(w)[:2]) == ("DATA_STALE", 1)
    sample(m, 75000)
    assert len(events(w)) == 1 and phase(w)[0] == "ACTIVE"
    changed = tmp_path / "changed.toml"
    changed.write_text(new.read_text().replace('threshold = "0.05"', 'threshold = "0.06"'))
    with pytest.raises(ValueError, match="parameters changed"):
        migrate(root, old, changed)


def event(w, t=0, status="BASIS_ENTERED", symbol="BTC/USDT", episode="episode-1", critical=False):
    return w.event(
        symbol,
        NOW + t,
        status,
        {
            "episode_id": episode,
            "value_percent": "1.5",
            "threshold_percent": "1",
            "confirmation_ms": 60000,
        },
        key=f"synthetic:{symbol}:{t}",
        critical=critical,
    )


def enqueue(w, e):
    with w.outbox.connect() as db:
        w.enqueue_in(db, e)


def test_finite_retry_429_and_restart(tmp_path):
    async def run():
        clock = [NOW + 4000]
        sender = AsyncMock(side_effect=TelegramFailure(17, error_code=429))
        service = BotNotificationService(
            tmp_path / "b.sqlite3",
            sender,
            policy=config().reliability,
            interval_ms=0,
            clock=lambda: clock[0],
        )
        w = RiskWatch(service.outbox, config())
        enqueue(w, event(w))
        for _i in range(5):
            assert await service.deliver_once()
            clock[0] += 20000
        assert sender.await_count == 5
        with service.outbox.connect() as db:
            assert tuple(
                db.execute("select state,attempts from notification_outbox").fetchone()
            ) == ("failed", 5)
        assert not await service.deliver_once()
        await service.close()

    asyncio.run(run())


def test_permanent_failure_no_retry_and_b_only_queue(tmp_path):
    async def run():
        sender = AsyncMock(side_effect=TelegramFailure(error_code=403))
        s = BotNotificationService(
            tmp_path / "b.sqlite3",
            sender,
            policy=config().reliability,
            interval_ms=0,
            clock=lambda: NOW + 4000,
        )
        w = RiskWatch(s.outbox, config())
        enqueue(w, event(w))
        await s.deliver_once()
        assert not await s.deliver_once()
        assert sender.await_count == 1
        await s.close()
        a = Outbox(tmp_path / "a.sqlite3")
        a.bind_channel("A")
        with pytest.raises(ValueError):
            BotNotificationService(a.path, sender, policy=config().reliability)

    asyncio.run(run())


def test_multi_event_merge_priority_and_superseded_alert(tmp_path):
    async def run():
        sent = []

        async def send(text):
            sent.append(text)

        s = BotNotificationService(
            tmp_path / "b.sqlite3",
            send,
            policy=config().reliability,
            interval_ms=0,
            clock=lambda: NOW + 4000,
        )
        w = RiskWatch(s.outbox, config())
        enqueue(w, event(w))
        enqueue(w, event(w, symbol="ETH/USDT", episode="episode-2"))
        enqueue(
            w,
            event(w, status="USDE_ENTERED", symbol="USDE/USD", episode="episode-3", critical=True),
        )
        await s.deliver_once()
        assert "USDE/USD" in sent[0]
        await s.deliver_once()
        assert "BTC/USDT" in sent[1] and "ETH/USDT" in sent[1]
        assert not await s.deliver_once()
        enqueue(w, event(w, t=6000, status="BASIS_RECOVERED"))
        with w.outbox.connect() as db:
            assert db.execute("select count(*) from bot_b_history").fetchone()[0] == 4
        await s.close()

    asyncio.run(run())


def test_read_only_commands_authorization_and_durable_offset(tmp_path):
    w = watcher(tmp_path)
    api = BotTelegramAPI("123:synthetic", 10, {10}, profile="B")
    cmd = BotCommands(api, w, tmp_path)
    cmd.started_ms = NOW
    updates = [
        {"update_id": i, "message": {"chat": {"id": chat}, "date": NOW // 1000, "text": text}}
        for i, chat, text in [
            (1, 99, "/settings"),
            (2, 10, "/settings"),
            (3, 10, "/risks"),
            (4, 10, "/history"),
            (5, 10, "/status"),
        ]
    ]
    cmd.process(updates, NOW)
    assert len(events(w)) == 4
    cmd.process(updates, NOW)
    assert len(events(w)) == 4
    assert w.outbox.get_meta("bot_b_update_offset") == "6"
    assert all(e["status"] == "COMMAND_REPLY" for e in events(w))
    assert cmd.reply("/delete", NOW).startswith("支持")


def test_replaying_recovery_never_supersedes_itself_and_changed_id_is_rejected(tmp_path):
    from dataclasses import replace

    w = watcher(tmp_path)
    entered = event(w)
    enqueue(w, entered)
    recovered = event(w, t=15000, status="BASIS_RECOVERED")
    enqueue(w, recovered)
    enqueue(w, recovered)
    with w.outbox.connect() as db:
        assert (
            db.execute(
                "select state from notification_outbox where event_id=?", (entered.event_id,)
            ).fetchone()[0]
            == "superseded"
        )
        assert (
            db.execute(
                "select state from notification_outbox where event_id=?", (recovered.event_id,)
            ).fetchone()[0]
            == "pending"
        )
    with pytest.raises(ValueError):
        enqueue(w, replace(recovered, symbol="different"))


def test_pending_retry_survives_service_recreation(tmp_path):
    async def run():
        clock = [NOW + 4000]
        failed = AsyncMock(side_effect=TimeoutError())
        path = tmp_path / "b.sqlite3"
        service = BotNotificationService(
            path, failed, policy=config().reliability, interval_ms=0, clock=lambda: clock[0]
        )
        w = RiskWatch(service.outbox, config())
        enqueue(w, event(w))
        await service.deliver_once()
        await service.close()
        delivered = AsyncMock()
        clock[0] += 3000
        service = BotNotificationService(
            path, delivered, policy=config().reliability, interval_ms=0, clock=lambda: clock[0]
        )
        assert await service.deliver_once()
        assert delivered.await_count == 1
        await service.close()

    asyncio.run(run())


def test_poll_offset_and_reply_rollback_together(tmp_path, monkeypatch):
    w = watcher(tmp_path)
    api = BotTelegramAPI("123:synthetic", 10, {10}, profile="B")
    cmd = BotCommands(api, w, tmp_path)
    cmd.started_ms = NOW

    def fail(*a):
        raise RuntimeError("synthetic storage failure")

    monkeypatch.setattr(w, "enqueue_in", fail)
    update = {
        "update_id": 1,
        "message": {"chat": {"id": 10}, "date": NOW // 1000, "text": "/settings"},
    }
    with pytest.raises(RuntimeError):
        cmd.process([update], NOW)
    assert w.outbox.get_meta("bot_b_update_offset", "0") == "0"
    assert not events(w)


def test_feed_outage_health_and_unknown_do_not_fake_risk_recovery(tmp_path):
    w = watcher(tmp_path)
    m = DerivativeWatch(w)
    for t in range(0, 75000, 15000):
        sample(m, t)
    for t in range(75000, 150000, 15000):
        m.invalidate("basis", "BTC/USDT", NOW + t)
        w.feed_health("basis:BTC/USDT", False, NOW + t)
    assert tuple(phase(w)[:2]) == ("DATA_STALE", 1)
    assert len([e for e in events(w) if e["status"] == "DATA_UNAVAILABLE"]) == 1
    w.feed_health("basis:BTC/USDT", True, NOW + 150000)
    sample(m, 150000)
    assert phase(w)[0] == "ACTIVE"
    assert not any(e["status"] == "BASIS_RECOVERED" for e in events(w))


def test_online_snapshot_is_consistent_and_does_not_touch_source(tmp_path):
    import sqlite3

    from crypto_market_monitor.bot_preflight import snapshot

    source = tmp_path / "source"
    w = watcher(source)
    m = DerivativeWatch(w)
    sample(m, 0)
    destination = tmp_path / "isolated"
    snapshot(source, destination)
    with sqlite3.connect(destination / "data/bot-b.sqlite3") as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("select phase from bot_b_risks").fetchone()[0] == "PENDING"
    assert phase(w)[0] == "PENDING"
    with pytest.raises(ValueError):
        snapshot(source, destination)


def test_exhausted_legacy_pending_is_never_sent_again(tmp_path):
    async def run():
        sender = AsyncMock()
        s = BotNotificationService(
            tmp_path / "b.sqlite3",
            sender,
            policy=config().reliability,
            interval_ms=0,
            clock=lambda: NOW + 4000,
        )
        w = RiskWatch(s.outbox, config())
        enqueue(w, event(w))
        with w.outbox.connect() as db:
            db.execute("update notification_outbox set attempts=5")
        assert not await s.deliver_once()
        assert sender.await_count == 0
        await s.close()

    asyncio.run(run())


def test_poll_conflict_code_is_preserved_and_token_redacted(monkeypatch):
    import io
    import urllib.request

    api = BotTelegramAPI("123:synthetic-secret", 10, {10}, profile="B")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(
            b'{"ok":false,"error_code":409,"description":"private upstream text"}'
        ),
    )
    with pytest.raises(TelegramFailure) as caught:
        api.rpc("getUpdates")
    assert caught.value.error_code == 409
    assert "synthetic-secret" not in str(caught.value) and "private upstream" not in str(
        caught.value
    )


@pytest.mark.parametrize(
    "status,body,retry_after",
    [
        (409, b'{"ok":false,"error_code":409}', 0),
        (409, b"upstream non-JSON error", 0),
        (429, b'{"ok":false,"error_code":429,"parameters":{"retry_after":17}}', 17),
        (403, b'{"ok":false,"error_code":403}', 0),
        (409, b'{"ok":true,"result":[]}', 0),
    ],
)
def test_poll_http_errors_preserve_status_and_close_response(
    monkeypatch, status, body, retry_after
):
    import io
    import urllib.error
    import urllib.request

    api = BotTelegramAPI("123:synthetic-secret", 10, {10}, profile="B")
    response = io.BytesIO(body)

    def rejected(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.telegram.org/bot123:synthetic-secret/getUpdates",
            status,
            "private upstream text",
            {},
            response,
        )

    monkeypatch.setattr(urllib.request, "urlopen", rejected)
    with pytest.raises(TelegramFailure) as caught:
        api.rpc("getUpdates")
    assert caught.value.error_code == status
    assert caught.value.retry_after == retry_after
    assert response.closed
    assert "synthetic-secret" not in str(caught.value)
    assert "private upstream" not in str(caught.value)


def test_conflicting_poller_is_not_retried(tmp_path, monkeypatch):
    from unittest.mock import Mock

    async def run():
        w = watcher(tmp_path)
        api = BotTelegramAPI("123:synthetic", 10, {10}, profile="B")
        rpc = Mock(side_effect=[{}, TelegramFailure(error_code=409)])
        monkeypatch.setattr(api, "rpc", rpc)
        cmd = BotCommands(api, w, tmp_path)
        cmd.start()
        await asyncio.sleep(0.1)
        assert cmd.error == "conflicting_polling_owner" and rpc.call_count == 2
        await cmd.close()

    asyncio.run(run())


@pytest.mark.parametrize("retry_after,expected", [(0, 5), (17, 17), (1000, 120)])
def test_polling_retry_after_is_bounded(tmp_path, monkeypatch, retry_after, expected):
    from unittest.mock import Mock

    async def run():
        w = watcher(tmp_path)
        api = BotTelegramAPI("123:synthetic", 10, {10}, profile="B")
        monkeypatch.setattr(
            api, "rpc", Mock(side_effect=[{}, TelegramFailure(retry_after, error_code=429)])
        )
        delays = []

        async def sleep(delay):
            delays.append(delay)
            raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", sleep)
        cmd = BotCommands(api, w, tmp_path)
        with pytest.raises(asyncio.CancelledError):
            await cmd._run()
        assert delays == [expected]
        assert cmd.error == "polling_unavailable"

    asyncio.run(run())
