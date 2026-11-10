"""Offline phase-one acceptance. Synthetic observations never reach Telegram."""

import asyncio
import io
import json
from dataclasses import replace
from decimal import Decimal as D
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs

import pytest
from conftest import NOW
from test_risk_watch import events

from crypto_market_monitor.adapters.compact_kraken import build_compact_kraken
from crypto_market_monitor.bot_templates import format_bot
from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.models import Instrument, Level, MarketKind, OrderBook
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.market_data.usde_watch import record_usde
from crypto_market_monitor.models import DataError
from crypto_market_monitor.notification_events import format_message
from crypto_market_monitor.notification_store import Outbox
from crypto_market_monitor.notifications import NotificationService
from crypto_market_monitor.telegram_transport import TelegramAPI


def config():
    return RiskWatchConfig.load(Path("config/bot-phase1.toml"))


def watcher(root):
    return RiskWatch(Outbox(root / "b.sqlite3"), config())


def book(t=0, price="1", *, symbol="USDE/USD", venue="kraken", quantity="10000"):
    p = D(price)
    return OrderBook(
        Instrument(venue, symbol, symbol.replace("/", ""), MarketKind.SPOT, *symbol.split("/")),
        (Level(p - D("0.00001"), D(quantity)),),
        (Level(p + D("0.00001"), D(quantity)),),
        NOW + t,
        NOW + t,
        "synthetic-test-only",
    )


def record(m, t, price="1"):
    return record_usde(m, book(t, price), None, None, NOW + t)


def test_existing_configuration_is_unchanged_and_usde_is_optional():
    old, new = RiskWatchConfig.load(Path("config/bot.toml")), config()
    for name in old.__dataclass_fields__:
        if name not in {"usde", "fingerprint"}:
            assert getattr(old, name) == getattr(new, name)
    assert old.usde is None
    assert new.usde.rule.threshold == D("0.005")
    assert new.usde.rule.confirm_ms == 60000


def test_normal_and_transient_usde_do_not_alert(tmp_path):
    w = watcher(tmp_path)
    m = DerivativeWatch(w)
    for t in range(0, 120000, 15000):
        record(m, t)
    for t in (120000, 135000, 150000):
        record(m, t, "0.994")
    for t in (165000, 180000, 195000):
        record(m, t)
    assert not events(w)


@pytest.mark.parametrize("price", ["0.994", "1.006", "0.995", "1.005"])
def test_usde_confirmation_exact_boundary_restart_and_recovery(tmp_path, price):
    w = watcher(tmp_path)
    m = DerivativeWatch(w)
    for t in (0, 15000, 30000):
        record(m, t, price)
    assert not events(w)
    m = DerivativeWatch(watcher(tmp_path))
    record(m, 45000, price)
    record(m, 60000, price)
    assert [e["status"] for e in events(w)] == ["USDE_ENTERED"]
    assert events(w)[0]["strategy"] == "usde-peg-v1"
    # Persistent peak must include ongoing observations, not just sent messages.
    record(m, 75000, "0.991" if D(price) < 1 else "1.009")
    for t in range(90000, 165000, 15000):
        record(m, t)
    rows = events(w)
    assert rows[-1]["status"] == "USDE_RECOVERED"
    assert abs(D(rows[-1]["details"]["peak_value_percent"])) == D("0.9")
    assert rows[-1]["details"]["episode_duration_ms"] == 90000


@pytest.mark.parametrize(
    "fault", ["missing", "stale", "future", "skew", "thin", "spread", "identity"]
)
def test_cross_rate_rejects_one_bad_leg(tmp_path, fault):
    m = DerivativeWatch(watcher(tmp_path))
    cross = book(price="1", symbol="USDE/USDT")
    usd = book(price="0.99", symbol="USDT/USD", venue="bitstamp")
    if fault == "missing":
        usd = None
    elif fault == "stale":
        usd = replace(usd, source_ms=NOW - 30001)
    elif fault == "future":
        usd = replace(usd, source_ms=NOW + 1)
    elif fault == "skew":
        usd = replace(usd, source_ms=NOW - 10001)
    elif fault == "thin":
        usd = book(symbol="USDT/USD", venue="bitstamp", quantity="1")
    elif fault == "spread":
        usd = replace(usd, asks=(Level(D("1.02"), D("10000")),))
    elif fault == "identity":
        usd = replace(usd, instrument=replace(usd.instrument, quote="USDT"))
    with pytest.raises(DataError):
        record_usde(m, None, cross, usd, NOW)
    assert not events(m.watcher)


def test_cross_rate_multiplies_usdt_dollar_price_and_prefers_direct(tmp_path):
    m = DerivativeWatch(watcher(tmp_path))
    for t in range(0, 75000, 15000):
        cross = book(t, "1", symbol="USDE/USDT")
        usd = book(t, "0.99", symbol="USDT/USD", venue="bitstamp")
        record_usde(m, None, cross, usd, NOW + t)
    assert events(m.watcher)[0]["status"] == "USDE_ENTERED"
    assert abs(D(events(m.watcher)[0]["details"]["value_percent"])) > D("0.99")
    # A newly observed instrument chooses direct USD before the available cross.
    fresh = DerivativeWatch(watcher(tmp_path / "fresh"))
    result = record_usde(fresh, book(75000), cross, usd, NOW + 75000)
    assert result["source"] == "kraken" and result["mid_usd"] == "1.00000"


def test_invalid_usde_is_data_fault_not_risk_or_recovery(tmp_path):
    from crypto_market_monitor.market_data.contracts import ObservationSet
    from crypto_market_monitor.market_data.risk_session import RiskSession
    from crypto_market_monitor.market_data.scanner import ScanReport

    w = watcher(tmp_path)
    session = RiskSession(w, None, NOW)
    for t in (0, 15000, 30000):
        result = session.process(
            ScanReport(ObservationSet(NOW + t, (), ()), (), (), (), ()), NOW + t
        )
    assert result["usde_feed"]["status"] == "unavailable"
    assert any(
        e["status"] == "DATA_UNAVAILABLE" and e["symbol"] == "usde:USDE/USD" for e in events(w)
    )
    assert not any(e["status"].startswith("USDE_") for e in events(w))


def template_events(tmp_path):
    w = watcher(tmp_path)
    m = DerivativeWatch(w)
    for t in range(0, 75000, 15000):
        record(m, t, "0.994")
    record(m, 75000, "0.991")
    for t in range(90000, 165000, 15000):
        record(m, t)
    w.feed_health("USDE/USD", True, NOW + 150000, source="Kraken")
    for t in (165000, 180000, 195000):
        w.feed_health("USDE/USD", False, NOW + t, source="Kraken", error_type="报价过期 <stale>&")
    w.feed_health("USDE/USD", True, NOW + 210000, source="Kraken")
    return events(w)


class SafeHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        assert tag == "b" and not attrs
        self.tags.append(tag)

    def handle_endtag(self, tag):
        assert self.tags.pop() == tag


def test_all_four_templates_render_real_fixture_events_and_escape(tmp_path):
    from crypto_market_monitor.notification_events import NotificationEvent

    rows = template_events(tmp_path)
    rendered = []
    for row, heading in zip(rows, ["风险预警", "风险解除", "数据异常", "数据恢复"], strict=True):
        e = NotificationEvent.from_json(json.dumps(row))
        text = format_bot(e)
        assert heading in text
        parser = SafeHTML()
        parser.feed(text)
        assert not parser.tags
        assert len(text.encode("utf-16-le")) < 7800
        assert "UTC+8" in text or heading == "数据异常"  # no preceding valid sample is unknown
        rendered.append(text)
    assert "&lt;stale&gt;&amp;" in rendered[2]
    assert "0.9%" in rendered[1]
    # Save only simulated examples, explicitly labelled; never send externally.
    output = Path("data/reports/bot-phase1-template-examples.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "# 离线模拟渲染示例（不是实际行情/送达记录）\n\n" + "\n\n---\n\n".join(rendered),
        encoding="utf-8",
    )


def test_external_symbol_and_source_are_html_escaped(tmp_path):
    w = watcher(tmp_path)
    e = w.event(
        '<script>&"',
        NOW,
        "USDE_ENTERED",
        {"value_percent": D("-0.6"), "threshold_percent": D("0.5"), "confirmation_ms": 60000},
        key="synthetic",
    )
    text = format_bot(replace(e, source="<exchange>&"))
    assert "<script>" not in text and "&lt;script&gt;&amp;&quot;" in text
    assert "&lt;exchange&gt;&amp;" in text


def test_transport_html_opt_in_keeps_a_plain_and_templates_isolated(tmp_path):
    a = TelegramAPI("123:synthetic", 10, {10}, profile="A")
    b = TelegramAPI("456:synthetic", 10, {10}, profile="B")

    def response():
        return io.BytesIO(b'{"ok":true,"result":{"chat":{"id":10},"message_id":42}}')

    with patch("urllib.request.urlopen", side_effect=lambda *a, **k: response()) as send:
        a._request("<b>A plain</b>")
        assert "parse_mode" not in parse_qs(send.call_args.args[0].data.decode())
        b._request("<b>B HTML</b>", html=True)
        assert parse_qs(send.call_args.args[0].data.decode())["parse_mode"] == ["HTML"]
    e = watcher(tmp_path).event("BTC/USDT", NOW, "ENTERED", {}, key="synthetic")
    e = replace(e, details={"bot_channel": "A"})
    with pytest.raises(ValueError):
        format_bot(e)

    async def check():
        service = NotificationService(tmp_path / "a.sqlite3", AsyncMock())
        assert service.formatter is format_message
        await service.close()

    asyncio.run(check())


def test_filtered_kraken_discovery_tolerates_absent_optional_cross():
    async def check():
        row = {
            "symbol": "USDE/USD",
            "id": "USDEUSD",
            "base": "USDE",
            "quote": "USD",
            "spot": True,
            "active": True,
        }
        selected = {}
        client = SimpleNamespace(options={}, has={"watchOrderBook": True}, close=AsyncMock())
        client.fetch_markets = AsyncMock(side_effect=[[row], DataError("optional market absent")])
        client.set_markets = Mock(
            side_effect=lambda rows: selected.update({r["symbol"]: r for r in rows})
        )
        client.load_markets = AsyncMock(side_effect=lambda: selected)
        a, instruments = await build_compact_kraken(("USDE/USD", "USDE/USDT"), client)
        assert len(selected) == len(instruments) == 1
        assert client.fetch_markets.call_args_list[0].args == ({"pair": "USDEUSD"},)
        await a.close()

    asyncio.run(check())


def test_existing_metric_templates_use_correct_price_and_funding_labels(tmp_path):
    from test_derivative_watch import book as spot_book
    from test_derivative_watch import funding, perp

    from crypto_market_monitor.notification_events import NotificationEvent

    w = watcher(tmp_path)
    m = DerivativeWatch(w)
    for t in range(0, 75000, 15000):
        m.record_basis(spot_book(t), perp(t), NOW + t)
    m.record_funding(funding(90000), NOW + 90000)
    m.record_funding(funding(150000), NOW + 150000)
    texts = [format_bot(NotificationEvent.from_json(json.dumps(e))) for e in events(w)]
    assert "永续盘口中价" in texts[0] and "标记价" not in texts[0]
    assert "预计原始费率" in texts[1] and "8小时等效" in texts[1]
    assert "下次结算" in texts[1] and "美元参考价" not in texts[1]
    from test_risk_watch import book as price_book

    for t in range(0, 900000, 15000):
        w.record(price_book(t), NOW + t)
    for t in (900000, 915000, 930000):
        w.record(price_book(t, "106"), NOW + t)
    text = format_bot(NotificationEvent.from_json(json.dumps(events(w)[-1])))
    assert "15分钟" in text and "当前 / 起始：106 / 100" in text
    assert "预计原始费率" not in text


def test_usde_feed_reconnects_independently_and_closes(monkeypatch):
    from crypto_market_monitor.market_data import usde_feed as module

    async def check():
        b = book()
        a = SimpleNamespace(watch_book=AsyncMock(return_value=b), close=AsyncMock())
        monkeypatch.setattr(
            module, "build_compact_kraken", AsyncMock(return_value=(a, (b.instrument,)))
        )
        f = module.UsdeFeed(config().usde)
        f.start()
        await asyncio.wait_for(f.ready.wait(), 1)
        assert f.latest[b.instrument.symbol] == b
        await f.close()
        a.close.assert_awaited_once()
        assert not f.latest and f.task is None

    asyncio.run(check())


def test_route_change_and_data_loss_restart_confirmation_without_false_recovery(tmp_path):
    m = DerivativeWatch(watcher(tmp_path))
    record(m, 0, "0.994")
    record(m, 15000, "0.994")
    for t in (30000, 45000, 60000, 75000):
        record_usde(
            m,
            None,
            book(t, "0.994", symbol="USDE/USDT"),
            book(t, symbol="USDT/USD", venue="bitstamp"),
            NOW + t,
        )
    assert not events(m.watcher)  # changing route restarts confirmation
    t = 90000
    record_usde(
        m,
        None,
        book(t, "0.994", symbol="USDE/USDT"),
        book(t, symbol="USDT/USD", venue="bitstamp"),
        NOW + t,
    )
    assert events(m.watcher)[-1]["status"] == "USDE_ENTERED"
    m.invalidate("usde", "USDE/USD")
    record(m, 105000)
    record(m, 120000)
    assert events(m.watcher)[-1]["status"] == "USDE_ENTERED"


def test_phase1_sender_uses_html_formatter_and_existing_durable_queue(tmp_path):
    async def check():
        sent = []

        async def sender(text):
            sent.append(text)

        service = NotificationService(
            tmp_path / "notifications.sqlite3", sender, formatter=format_bot, clock=lambda: NOW
        )
        w = RiskWatch(service.outbox, config())
        e = w.event(
            "USDE/USD",
            NOW,
            "USDE_ENTERED",
            {
                "value_percent": D("-0.6"),
                "threshold_percent": D("0.5"),
                "confirmation_ms": 60000,
                "mid_usd": D("0.994"),
                "bid_usd": D("0.99399"),
                "ask_usd": D("0.99401"),
            },
            key="synthetic",
        )
        service.outbox.enqueue(e)
        assert await service.deliver_once()
        assert sent == [format_bot(e)]
        with service.outbox.connect() as db:
            assert db.execute("select state from notification_outbox").fetchone()[0] == "sent"
        await service.close()

    asyncio.run(check())


@pytest.mark.parametrize(
    "key,value",
    [
        ("max_spread", "NaN"),
        ("max_age_ms", 30001),
        ("direct_symbol", "USDE/USDT"),
        ("fallback", "yes"),
    ],
)
def test_usde_bad_configuration_rejected(key, value):
    from crypto_market_monitor.market_data.usde_config import load_usde

    with pytest.raises(ValueError):
        load_usde({key: value})
