from decimal import Decimal as D

import pytest

from crypto_market_monitor.models import Funding, Quote, Snapshot

NOW = 1_800_000_000_000


@pytest.fixture
def snapshot():
    return Snapshot(
        "binance",
        Quote("BTC/USDT", D("99990"), D("100000"), NOW),
        Quote("BTC/USDT:USDT", D("100010"), D("100020"), NOW),
        Funding(D("0.001"), NOW, NOW + 3_600_000, 8 * 3_600_000, D("100000")),
        NOW,
        NOW,
    )


@pytest.fixture
def payloads():
    return [
        {"symbol": "BTCUSDT", "bidPrice": "99990", "askPrice": "100000", "closeTime": NOW},
        {"symbol": "BTCUSDT", "bidPrice": "100010", "askPrice": "100020", "time": NOW},
        {
            "symbol": "BTCUSDT",
            "lastFundingRate": "0.001",
            "time": NOW,
            "nextFundingTime": NOW + 3_600_000,
            "markPrice": "100000",
        },
        [{"symbol": "BTCUSDT", "fundingIntervalHours": 8}],
    ]
