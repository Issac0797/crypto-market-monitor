import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from conftest import NOW

from crypto_market_monitor.adapters.binance import BinancePublicData, normalize
from crypto_market_monitor.adapters.native_http import NetworkError
from crypto_market_monitor.models import DataError


def test_normalize(payloads, snapshot):
    assert normalize(*payloads, NOW) == snapshot


@pytest.mark.parametrize("value", ["", "NaN", "Infinity", "-1", "0"])
def test_bad_prices(payloads, value):
    payloads[0]["askPrice"] = value
    with pytest.raises(DataError):
        normalize(*payloads, NOW)


def test_bad_instrument_and_interval(payloads):
    payloads[1]["symbol"] = "ETHUSDT"
    with pytest.raises(DataError):
        normalize(*payloads, NOW)
    payloads[1]["symbol"] = "BTCUSDT"
    payloads[3][0]["fundingIntervalHours"] = 0
    with pytest.raises(DataError):
        normalize(*payloads, NOW)


@pytest.mark.parametrize("response", [{"code": -1000, "msg": "error"}, {}, None, []])
def test_bad_envelope(payloads, response):
    payloads[0] = response
    with pytest.raises(DataError):
        normalize(*payloads, NOW)


def test_default_and_adjusted_funding_interval(payloads):
    payloads[3] = []
    assert normalize(*payloads, NOW).funding.interval_ms == 8 * 3_600_000
    payloads[3] = [{"symbol": "BTCUSDT", "fundingIntervalHours": 4}]
    assert normalize(*payloads, NOW).funding.interval_ms == 4 * 3_600_000
    payloads[3] *= 2
    with pytest.raises(DataError):
        normalize(*payloads, NOW)


def test_public_calls_and_cleanup(payloads):
    client = AsyncMock()
    client.public_get_ticker_24hr.return_value = payloads[0]
    client.fapiPublicGetTickerBookTicker.return_value = payloads[1]
    client.fapiPublicGetPremiumIndex.return_value = payloads[2]
    client.fapiPublicGetFundingInfo.return_value = payloads[3]
    source = BinancePublicData(client=client)

    async def collect():
        try:
            return await source.fetch_snapshot()
        finally:
            await source.close()

    assert asyncio.run(collect()).exchange == "binance"
    client.public_get_ticker_24hr.assert_awaited_once_with({"symbol": "BTCUSDT"})
    client.fapiPublicGetTickerBookTicker.assert_awaited_once_with({"symbol": "BTCUSDT"})
    client.fapiPublicGetPremiumIndex.assert_awaited_once_with({"symbol": "BTCUSDT"})
    client.fapiPublicGetFundingInfo.assert_awaited_once_with()
    client.close.assert_awaited_once()
    assert not client.create_order.called


def test_network_retry_exhaustion():
    client = AsyncMock()
    client.public_get_ticker_24hr.side_effect = NetworkError("offline")
    source = BinancePublicData(client=client, retries=0)
    with pytest.raises(NetworkError):
        asyncio.run(source.fetch_snapshot())
    assert client.public_get_ticker_24hr.await_count == 1


def test_network_retry_then_success(payloads):
    client = AsyncMock()
    client.public_get_ticker_24hr.side_effect = [NetworkError("timeout"), payloads[0]]
    client.fapiPublicGetTickerBookTicker.return_value = payloads[1]
    client.fapiPublicGetPremiumIndex.return_value = payloads[2]
    client.fapiPublicGetFundingInfo.return_value = payloads[3]
    with patch(
        "crypto_market_monitor.adapters.binance.asyncio.sleep", new_callable=AsyncMock
    ) as sleep:
        result = asyncio.run(BinancePublicData(client=client, retries=1).fetch_snapshot())
    assert result.exchange == "binance"
    assert client.public_get_ticker_24hr.await_count == 2
    sleep.assert_awaited_once_with(1)


def test_malformed_data_is_not_retried(payloads):
    client = AsyncMock()
    client.public_get_ticker_24hr.return_value = {}
    client.fapiPublicGetTickerBookTicker.return_value = payloads[1]
    client.fapiPublicGetPremiumIndex.return_value = payloads[2]
    client.fapiPublicGetFundingInfo.return_value = payloads[3]
    with pytest.raises(DataError):
        asyncio.run(BinancePublicData(client=client, retries=2).fetch_snapshot())
    assert client.public_get_ticker_24hr.await_count == 1


def test_cancellation_is_not_retried():
    client = AsyncMock()
    client.public_get_ticker_24hr.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(BinancePublicData(client=client).fetch_snapshot())
    assert client.public_get_ticker_24hr.await_count == 1
