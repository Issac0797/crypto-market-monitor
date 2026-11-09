"""Venue-specific fiat USD peg observations; never infer dollars from USDT."""

from decimal import Decimal

from crypto_market_monitor.adapters.compact_bitstamp import STABLE_SYMBOLS
from crypto_market_monitor.market_data.derivative_watch import DerivativeWatch
from crypto_market_monitor.market_data.models import MarketKind, OrderBook
from crypto_market_monitor.models import DataError


def record_stablecoin(metrics: DerivativeWatch, book: OrderBook, now_ms: int) -> int:
    rule = metrics.config.stablecoin
    if rule is None:
        return 0
    book.validate_at(now_ms, metrics.config.max_age_ms)
    i = book.instrument
    if (
        i.venue not in {"bitstamp", "kraken"}
        or i.kind != MarketKind.SPOT
        or i.symbol not in STABLE_SYMBOLS
        or i.quote != "USD"
        or i.base != i.symbol.split("/")[0]
        or (i.venue == "bitstamp" and i.native_id != i.symbol.replace("/", "").lower())
        or (
            i.venue == "kraken"
            and (
                i.symbol != "USDC/USD"
                or i.native_id != "USDCUSD"
                or not metrics.config.reliability
                or not metrics.config.reliability.usdc_fallback
            )
        )
    ):
        raise DataError("Stablecoin requires reviewed fiat USD spot identity")
    bid, ask = book.bids[0].price, book.asks[0].price
    # Require the entire best quote to lie beyond the threshold. A wide
    # spread crossing $1 is uncertainty, not evidence of a depeg.
    value = bid - 1 if bid > 1 else ask - 1 if ask < 1 else Decimal(0)
    if bid <= 1 <= ask and (1 - bid > rule.recovery or ask - 1 > rule.recovery):
        raise DataError("Wide stablecoin spread cannot prove recovery")
    depths = [
        sum((v.price * v.quantity for v in side), Decimal(0)) for side in (book.bids, book.asks)
    ]
    if min(depths) < rule.min_depth_usdt:
        raise DataError("Insufficient USD book depth")
    # Recovery also needs both best quotes inside the recovery band.
    if abs(value) <= rule.recovery and max(abs(bid - 1), abs(ask - 1)) > rule.recovery:
        raise DataError("Stablecoin spread cannot confirm recovery")
    return metrics._record(
        "stablecoin",
        i.symbol,
        value,
        book.source_ms,
        i.key,
        rule,
        {
            "bid_usd": bid,
            "ask_usd": ask,
            "mid_usd": (bid + ask) / 2,
            "minimum_side_depth_usd": min(depths),
            "note": f"{i.venue}单所美元盘口偏离观察，非全市场脱锚定论；无多源确认",
        },
        source=f"{i.venue}-public",
    )
