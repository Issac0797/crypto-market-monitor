"""Direct fiat USD or explicitly validated two-leg USDe conversion."""

import hashlib
import json
from decimal import Decimal

from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.models import MarketKind, OrderBook
from crypto_market_monitor.market_data.usde_config import UsdeConfig
from crypto_market_monitor.models import DataError


def _check(book: OrderBook, venue: str, symbol: str, now: int, c: UsdeConfig) -> None:
    book.validate_at(now, c.max_age_ms)
    i = book.instrument
    if (i.venue, i.symbol, i.base, i.quote, i.kind) != (
        venue,
        symbol,
        *symbol.split("/"),
        MarketKind.SPOT,
    ):
        raise DataError("USDe报价身份错误")
    mid = (book.bids[0].price + book.asks[0].price) / 2
    if (book.asks[0].price - book.bids[0].price) / mid > c.max_spread:
        raise DataError("USDe报价价差过大")


def _depth(book: OrderBook, usd_conversion: Decimal, c: UsdeConfig) -> None:
    if any(
        sum((v.price * v.quantity * usd_conversion for v in side), Decimal(0))
        < c.rule.min_depth_usdt
        for side in (book.bids, book.asks)
    ):
        raise DataError("USDe报价美元深度不足")


def _version(book: OrderBook) -> tuple[str, int, str]:
    # The pair of independently versioned legs identifies a cross quote. A single
    # min/max timestamp cannot identify updates to the other leg in the same millisecond.
    levels = [
        [(str(v.price.normalize()), str(v.quantity.normalize())) for v in side]
        for side in (book.bids, book.asks)
    ]
    digest = hashlib.sha256(json.dumps(levels, separators=(",", ":")).encode()).hexdigest()
    return book.instrument.key, book.source_ms, digest


def _cross_quote(cross: OrderBook | None, conversion: OrderBook | None, now: int, c: UsdeConfig):
    if not c.fallback or cross is None or conversion is None:
        raise DataError("缺少有效USDe交叉报价")
    _check(cross, c.cross_venue, c.cross_symbol, now, c)
    _check(conversion, c.conversion_venue, c.conversion_symbol, now, c)
    if abs(cross.source_ms - conversion.source_ms) > c.rule.max_skew_ms:
        raise DataError("USDe交叉报价源时间差过大")
    _depth(cross, conversion.bids[0].price, c)
    _depth(conversion, Decimal(1), c)
    bid = cross.bids[0].price * conversion.bids[0].price
    ask = cross.asks[0].price * conversion.asks[0].price
    if (ask - bid) / ((bid + ask) / 2) > c.max_spread:
        raise DataError("USDe合成交叉价差过大")
    # Latest contributing exchange event; both legs independently pass age/skew.
    stamp = max(cross.source_ms, conversion.source_ms)
    context = f"{cross.instrument.key}*{conversion.instrument.key}"
    return (
        bid,
        ask,
        stamp,
        context,
        f"{c.cross_venue} × {c.conversion_venue}",
        {"cross_source_ms": cross.source_ms, "conversion_source_ms": conversion.source_ms},
        (_version(cross), _version(conversion)),
    )


def record_usde(
    metrics: DerivativeWatch,
    direct: OrderBook | None,
    cross: OrderBook | None,
    conversion: OrderBook | None,
    now_ms: int,
) -> dict:
    c = metrics.config.usde
    if c is None:
        return {"status": "disabled"}
    # Keep a healthy chosen route. A quiet direct market briefly becoming fresh
    # must not repeatedly interrupt the cross route's 60-second confirmation.
    with metrics.outbox.connect() as db:
        prior = db.execute(
            "SELECT context FROM derivative_state WHERE kind='usde' AND symbol='USDE/USD'"
        ).fetchone()
    selected = None
    if prior and "*" in prior[0]:
        try:
            selected = _cross_quote(cross, conversion, now_ms, c)
        except DataError:
            pass  # An invalid current route is never kept merely for stickiness.
    try:
        if selected is None:
            if direct is None:
                raise DataError("缺少USDe直接美元报价")
            _check(direct, c.direct_venue, c.direct_symbol, now_ms, c)
            _depth(direct, Decimal(1), c)
            selected = (
                direct.bids[0].price,
                direct.asks[0].price,
                direct.source_ms,
                direct.instrument.key,
                c.direct_venue,
                {"direct_source_ms": direct.source_ms},
                (_version(direct),),
            )
    except DataError as direct_error:
        if not c.fallback or cross is None or conversion is None:
            raise DataError(f"USDe直接美元报价不可用：{direct_error}；缺少有效交叉报价") from None
        selected = _cross_quote(cross, conversion, now_ms, c)
    bid, ask, stamp, context, source, stamps, versions = selected
    mid = (bid + ask) / 2
    value = mid / c.reference_usd - 1
    metrics._record(
        "usde",
        "USDE/USD",
        value,
        stamp,
        context,
        c.rule,
        {
            "mid_usd": mid,
            "reference_usd": c.reference_usd,
            "bid_usd": bid,
            "ask_usd": ask,
            "quote_route": context,
            "note": "USDe美元参考报价；单所/交叉观察，非全市场定论",
            **stamps,
        },
        source=source,
        source_versions=versions,
    )
    return {
        "status": "fresh",
        "source": source,
        "source_ms": stamp,
        "mid_usd": str(mid),
        "route": context,
    }
