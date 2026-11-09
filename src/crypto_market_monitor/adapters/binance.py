"""Binance Spot and USD-M public endpoints through official HTTP, without credentials."""

import asyncio
import time
from decimal import Decimal, InvalidOperation

from crypto_market_monitor.adapters.native_http import NetworkError, RateLimitExceeded
from crypto_market_monitor.models import DataError, Funding, Quote, Snapshot


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise DataError("Invalid decimal in exchange response") from exc
    if not result.is_finite():
        raise DataError("Non-finite decimal in exchange response")
    return result


def _positive_int(value: object) -> int:
    number = _decimal(value)
    if number <= 0 or number != number.to_integral_value():
        raise DataError("Expected a positive integer in exchange response")
    return int(number)


def normalize(
    spot: dict, swap: dict, premium: dict, funding_info: list, collected_ms: int, asset: str = "BTC"
) -> Snapshot:
    try:
        if asset not in {"BTC", "ETH"} or any(
            row["symbol"] != f"{asset}USDT" for row in (spot, swap, premium)
        ):
            raise DataError("Unexpected instrument; only BTC/ETH USDT linear swaps are supported")
        if not isinstance(funding_info, list):
            raise DataError("Invalid funding info response")
        adjusted = [row for row in funding_info if row["symbol"] == f"{asset}USDT"]
        if len(adjusted) > 1:
            raise DataError("Duplicate funding interval configuration")
        # Binance lists adjusted contracts; absent contracts retain the default 8h interval.
        interval_hours = _positive_int(adjusted[0]["fundingIntervalHours"]) if adjusted else 8
        timestamp = _positive_int(premium["time"])
        return Snapshot(
            exchange="binance",
            spot=Quote(
                f"{asset}/USDT",
                _decimal(spot["bidPrice"]),
                _decimal(spot["askPrice"]),
                _positive_int(spot["closeTime"]),
            ),
            perpetual=Quote(
                f"{asset}/USDT:USDT",
                _decimal(swap["bidPrice"]),
                _decimal(swap["askPrice"]),
                _positive_int(swap["time"]),
            ),
            funding=Funding(
                _decimal(premium["lastFundingRate"]),
                timestamp,
                _positive_int(premium["nextFundingTime"]),
                interval_hours * 3_600_000,
                _decimal(premium["markPrice"]),
            ),
            collected_ms=collected_ms,
            mark_timestamp_ms=timestamp,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError(f"Invalid Binance market payload: {exc}") from exc


class BinancePublicData:
    def __init__(
        self, timeout_ms: int = 10000, retries: int = 2, client=None, asset: str = "BTC"
    ) -> None:
        if asset not in {"BTC", "ETH"}:
            raise ValueError("Only BTC and ETH are supported")
        self.asset = asset
        if client is None:
            from crypto_market_monitor.adapters.native_client import NativePublicClient

            client = NativePublicClient("binance", timeout_ms=timeout_ms)
        self.client = client
        self.retries = retries

    async def fetch_snapshot(self) -> Snapshot:
        for attempt in range(self.retries + 1):
            try:
                # No load_markets: only public endpoints, never SAPI.
                spot = await self.client.public_get_ticker_24hr({"symbol": f"{self.asset}USDT"})
                swap = await self.client.fapiPublicGetTickerBookTicker(
                    {"symbol": f"{self.asset}USDT"}
                )
                premium = await self.client.fapiPublicGetPremiumIndex(
                    {"symbol": f"{self.asset}USDT"}
                )
                funding_info = await self.client.fapiPublicGetFundingInfo()
                return normalize(
                    spot, swap, premium, funding_info, time.time_ns() // 1_000_000, self.asset
                )
            except (NetworkError, RateLimitExceeded):
                if attempt == self.retries:
                    raise
                await asyncio.sleep(min(2**attempt, 8))
        raise RuntimeError("Unreachable retry state")

    async def close(self) -> None:
        await self.client.close()
