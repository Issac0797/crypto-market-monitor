"""Bot-only bounded concurrent public reads; one slow market cannot queue peers."""

import asyncio
import time
from collections.abc import Callable

from crypto_market_monitor.market_data.contracts import DataRequest, ObservationSet, PublicAdapter
from crypto_market_monitor.market_data.models import Instrument
from crypto_market_monitor.market_data.scanner import Scanner, ScanReport
from crypto_market_monitor.models import DataError


class BotOkxBooks:
    def __init__(
        self,
        adapter: PublicAdapter,
        instruments: tuple[Instrument, ...],
        *,
        max_age_ms: int = 30000,
        timeout_seconds: float = 5,
        retry_seconds: float = 0.25,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if adapter.venue != "okx" or not 1 <= len(instruments) <= 4:
            raise ValueError("Bounded OKX Bot subscriptions required")
        if len({i.key for i in instruments}) != len(instruments) or any(
            i.venue != "okx"
            or i.symbol not in {"BTC/USDT", "ETH/USDT", "BTC/USDT:USDT", "ETH/USDT:USDT"}
            for i in instruments
        ):
            raise ValueError("Reviewed distinct Bot markets required")
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self.instruments = instruments
        self.max_age_ms, self.retry_seconds = max_age_ms, retry_seconds
        # the native client's shared rate limiter remains in force. Each small scanner owns a
        # separate lock; this does not change the foundation/A scanner contract.
        self.scanners = tuple(
            Scanner(
                {"okx": adapter},
                max_age_ms=max_age_ms,
                timeout_seconds=timeout_seconds,
                clock=self.clock,
            )
            for _ in instruments
        )

    async def run_once(self) -> ScanReport:
        async def collect(scanner, instrument):
            for attempt in range(2):
                report = await scanner.run_once((DataRequest(instrument),))
                if report.observations.books or attempt == 1:
                    return report
                await asyncio.sleep(self.retry_seconds)

        reports = await asyncio.gather(
            *(
                collect(scanner, instrument)
                for scanner, instrument in zip(self.scanners, self.instruments, strict=True)
            )
        )
        now, books, failures = self.clock(), [], []
        for report, instrument in zip(reports, self.instruments, strict=True):
            failures.extend(report.failures)
            for book in report.observations.books:
                try:
                    book.validate_at(now, self.max_age_ms)
                    books.append(book)
                except DataError:
                    failures.append((DataRequest(instrument).key, "expired_during_collection"))
        return ScanReport(ObservationSet(now, tuple(books), ()), tuple(failures), (), (), ())
