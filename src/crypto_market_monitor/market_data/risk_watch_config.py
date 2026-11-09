"""Explicit, small Bot B price-watch configuration; no default scanner activation."""

import hashlib
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from crypto_market_monitor.bot_b_policy import BotBPolicy, load_policy

if TYPE_CHECKING:
    from crypto_market_monitor.market_data.usde_config import UsdeConfig


@dataclass(frozen=True)
class MoveRule:
    window_ms: int
    threshold: Decimal
    recovery: Decimal


@dataclass(frozen=True)
class MetricRule:
    threshold: Decimal
    recovery: Decimal
    confirm_ms: int
    max_gap_ms: int
    max_skew_ms: int = 10000
    min_depth_usdt: Decimal = Decimal("1000")


@dataclass(frozen=True)
class RiskWatchConfig:
    symbols: tuple[str, ...]
    poll_seconds: int
    max_age_ms: int
    max_gap_ms: int
    confirm_ms: int
    cooldown_ms: int
    rules: tuple[MoveRule, ...]
    fingerprint: str
    basis: MetricRule | None = None
    funding: MetricRule | None = None
    stablecoin: MetricRule | None = None
    usde: "UsdeConfig | None" = None
    reliability: BotBPolicy | None = None

    @classmethod
    def load(cls, path: Path) -> "RiskWatchConfig":
        content = path.read_bytes()
        raw = tomllib.loads(content.decode("utf-8"))
        expected = {
            "mode",
            "symbols",
            "poll_seconds",
            "max_age_ms",
            "max_gap_ms",
            "confirm_ms",
            "cooldown_ms",
            "rules",
        }
        if (
            set(raw) - {"basis", "funding", "stablecoin", "usde", "reliability"} != expected
            or raw["mode"] != "market-risk"
        ):
            raise ValueError("Explicit market-risk configuration required")
        symbols = raw["symbols"]
        if (
            not isinstance(symbols, list)
            or not symbols
            or len(set(symbols)) != len(symbols)
            or not set(symbols) <= {"BTC/USDT", "ETH/USDT"}
        ):
            raise ValueError("Reviewed BTC/ETH spot symbols required")
        bounds = {
            "poll_seconds": (5, 30),
            "max_age_ms": (1, 30000),
            "max_gap_ms": (1000, 60000),
            "confirm_ms": (15000, 120000),
            "cooldown_ms": (60000, 3600000),
        }
        for key, (lower, upper) in bounds.items():
            if type(raw[key]) is not int or not lower <= raw[key] <= upper:
                raise ValueError("Risk-watch sampling bounds invalid")
        if raw["max_gap_ms"] < raw["poll_seconds"] * 2000:
            raise ValueError("Gap bound must allow two sampling intervals")
        rules = []
        if not isinstance(raw["rules"], list) or not 1 <= len(raw["rules"]) <= 2:
            raise ValueError("One or two explicit rules required")
        for row in raw["rules"]:
            if set(row) != {"window_ms", "threshold", "recovery"}:
                raise ValueError("Unknown move rule fields")
            if type(row["window_ms"]) is not int or row["window_ms"] not in {300000, 900000}:
                raise ValueError("Reviewed 5/15 minute windows required")
            if not all(isinstance(row[k], str) for k in ("threshold", "recovery")):
                raise ValueError("Decimal strings required")
            threshold, recovery = Decimal(row["threshold"]), Decimal(row["recovery"])
            if not (
                threshold.is_finite()
                and recovery.is_finite()
                and 0 < recovery < threshold <= Decimal("0.5")
            ):
                raise ValueError("Finite hysteresis thresholds required")
            rules.append(MoveRule(row["window_ms"], threshold, recovery))
        if len({r.window_ms for r in rules}) != len(rules):
            raise ValueError("Duplicate move windows")
        from crypto_market_monitor.market_data.usde_config import load_usde

        return cls(
            tuple(symbols),
            *(raw[k] for k in bounds),
            tuple(rules),
            hashlib.sha256(content).hexdigest(),
            _metric(raw.get("basis"), "basis", raw["max_gap_ms"]),
            _metric(raw.get("funding"), "funding", raw["max_gap_ms"]),
            _metric(raw.get("stablecoin"), "stablecoin", raw["max_gap_ms"]),
            load_usde(raw.get("usde")),
            load_policy(raw.get("reliability")),
        )


def _metric(row: dict | None, kind: str, default_gap: int) -> MetricRule | None:
    if row is None:
        return None
    expected = {"threshold", "recovery", "confirm_ms"}
    expected |= (
        {"max_skew_ms", "min_depth_usdt"}
        if kind == "basis"
        else {"min_depth_usd"}
        if kind == "stablecoin"
        else {"max_gap_ms"}
    )
    if not isinstance(row, dict) or set(row) != expected:
        raise ValueError("Unknown derivative rule fields")
    if not all(isinstance(row[k], str) for k in ("threshold", "recovery")):
        raise ValueError("Derivative thresholds require Decimal strings")
    threshold, recovery = Decimal(row["threshold"]), Decimal(row["recovery"])
    if not (
        threshold.is_finite()
        and recovery.is_finite()
        and 0 < recovery < threshold <= Decimal("0.5")
    ):
        raise ValueError("Invalid derivative hysteresis thresholds")
    confirm, gap = row["confirm_ms"], row.get("max_gap_ms", default_gap)
    skew = row.get("max_skew_ms", 10000)
    if any(type(v) is not int for v in (confirm, gap, skew)) or not (
        30000 <= confirm <= 180000 and default_gap <= gap <= 300000 and 1 <= skew <= 10000
    ):
        raise ValueError("Invalid derivative confirmation/freshness bounds")
    depth_raw = row.get("min_depth_usd" if kind == "stablecoin" else "min_depth_usdt", "1000")
    if not isinstance(depth_raw, str):
        raise ValueError("Decimal depth string required")
    depth = Decimal(depth_raw)
    if not depth.is_finite() or not Decimal("1000") <= depth <= Decimal("1000000"):
        raise ValueError("Invalid minimum book depth")
    return MetricRule(threshold, recovery, confirm, gap, skew, depth)
