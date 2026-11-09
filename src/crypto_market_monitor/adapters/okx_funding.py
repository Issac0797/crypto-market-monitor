"""Normalize OKX funding fields from the same successful native public response.

Native funding fields are checked against the local response envelope. Never borrow
time from a book, HTTP headers, settlement schedules or the local clock.
"""

from crypto_market_monitor.adapters.binance import _decimal, _positive_int
from crypto_market_monitor.market_data.models import FundingObservation, Instrument, MarketKind
from crypto_market_monitor.models import DataError


def normalize_okx_funding(
    instrument: Instrument, row: dict, received_ms: int
) -> FundingObservation:
    try:
        info = row["info"]
        if (
            instrument.venue != "okx"
            or instrument.kind != MarketKind.PERPETUAL
            or row["symbol"] != instrument.symbol
            or info["instId"] != instrument.native_id
            or info["instType"] != "SWAP"
        ):
            raise DataError("OKX funding identity mismatch")
        source_ms = _positive_int(info["ts"])
        settlement_ms = _positive_int(info["fundingTime"])
        following_ms = _positive_int(info["nextFundingTime"])
        interval_ms = following_ms - settlement_ms
        if interval_ms <= 0:
            raise DataError("OKX funding schedule is not increasing")
        rate = _decimal(info["fundingRate"])
        if (
            _decimal(row["fundingRate"]) != rate
            or _positive_int(row["fundingTimestamp"]) != settlement_ms
            or (row.get("timestamp") is not None and _positive_int(row["timestamp"]) != source_ms)
        ):
            raise DataError("OKX unified and native funding fields disagree")
        return FundingObservation(
            instrument,
            rate,
            interval_ms,
            settlement_ms,
            source_ms,
            received_ms,
            "native:fetch_funding_rate+native:okx_funding_fields",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError("Invalid OKX funding identity, source time or schedule") from exc
