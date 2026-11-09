"""Explicit USDe-only parameters; existing Bot B rules remain unchanged."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from crypto_market_monitor.market_data.risk_watch_config import MetricRule


@dataclass(frozen=True)
class UsdeConfig:
    rule: MetricRule
    reference_usd: Decimal
    max_age_ms: int
    max_spread: Decimal
    direct_venue: str
    direct_symbol: str
    cross_venue: str
    cross_symbol: str
    conversion_venue: str
    conversion_symbol: str
    fallback: bool
    watch_timeout_seconds: int
    retry_seconds: int
    refresh_seconds: int
    quote_poll_seconds: int = 5


def load_usde(row: dict | None) -> UsdeConfig | None:
    if row is None:
        return None
    defaults = {
        "reference_usd": "1",
        "threshold": "0.005",
        "recovery": "0.0025",
        "confirm_ms": 60000,
        "max_gap_ms": 45000,
        "max_age_ms": 30000,
        "max_skew_ms": 10000,
        "max_spread": "0.002",
        "min_depth_usd": "1000",
        "direct_venue": "kraken",
        "direct_symbol": "USDE/USD",
        "cross_venue": "kraken",
        "cross_symbol": "USDE/USDT",
        "conversion_venue": "bitstamp",
        "conversion_symbol": "USDT/USD",
        "fallback": True,
        "watch_timeout_seconds": 60,
        "retry_seconds": 5,
        "refresh_seconds": 21600,
        "quote_poll_seconds": 5,
    }
    if not isinstance(row, dict) or set(row) - set(defaults):
        raise ValueError("Unknown USDe configuration")
    raw = defaults | row
    values = {}
    for key in ("reference_usd", "threshold", "recovery", "max_spread", "min_depth_usd"):
        if not isinstance(raw[key], str):
            raise ValueError("USDe Decimal strings required")
        try:
            values[key] = Decimal(raw[key])
        except InvalidOperation:
            raise ValueError("Invalid USDe Decimal string") from None
        if not values[key].is_finite() or values[key] <= 0:
            raise ValueError("Positive finite USDe parameters required")
    if not 0 < values["recovery"] < values["threshold"] <= Decimal("0.5"):
        raise ValueError("Invalid USDe recovery/trigger thresholds")
    if values["max_spread"] > Decimal("0.05") or values["min_depth_usd"] < 1:
        raise ValueError("USDe liquidity bounds invalid")
    for key, lower, upper in (
        ("confirm_ms", 15000, 300000),
        ("max_gap_ms", 15000, 300000),
        ("max_age_ms", 1, 30000),
        ("max_skew_ms", 1, 30000),
        ("watch_timeout_seconds", 5, 180),
        ("retry_seconds", 1, 60),
        ("refresh_seconds", 60, 86400),
        ("quote_poll_seconds", 1, 10),
    ):
        if type(raw[key]) is not int or not lower <= raw[key] <= upper:
            raise ValueError("USDe timing bounds invalid")
    if type(raw["fallback"]) is not bool:
        raise ValueError("Explicit USDe fallback boolean required")
    for key in (
        "direct_venue",
        "cross_venue",
        "direct_symbol",
        "cross_symbol",
        "conversion_venue",
        "conversion_symbol",
    ):
        allowed = {"kraken", "bybit"} if key == "cross_venue" else {defaults[key]}
        if raw[key] not in allowed:
            raise ValueError("USDe source identity requires adapter review")
    return UsdeConfig(
        MetricRule(
            values["threshold"],
            values["recovery"],
            raw["confirm_ms"],
            raw["max_gap_ms"],
            raw["max_skew_ms"],
            values["min_depth_usd"],
        ),
        values["reference_usd"],
        raw["max_age_ms"],
        values["max_spread"],
        *(
            raw[k]
            for k in (
                "direct_venue",
                "direct_symbol",
                "cross_venue",
                "cross_symbol",
                "conversion_venue",
                "conversion_symbol",
                "fallback",
                "watch_timeout_seconds",
                "retry_seconds",
                "refresh_seconds",
                "quote_poll_seconds",
            )
        ),
    )
