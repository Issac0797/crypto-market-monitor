"""Bounded USDe WS/REST caches in the existing process, with independent recovery."""

import asyncio
import time

from crypto_market_monitor.adapters.compact_bybit import build_compact_bybit
from crypto_market_monitor.adapters.compact_kraken import build_compact_kraken
from crypto_market_monitor.market_data.contracts import PublicAdapter
from crypto_market_monitor.market_data.models import Instrument, OrderBook
from crypto_market_monitor.market_data.usde_config import UsdeConfig


async def _close(adapter: PublicAdapter) -> None:
    # A failed cleanup must not kill this source's retry loop or its healthy peer.
    # Cancellation still propagates, while ordinary close errors remain isolated.
    try:
        await asyncio.wait_for(adapter.close(), 5)
    except Exception:
        pass


class UsdeFeed:
    def __init__(self, config: UsdeConfig) -> None:
        self.config = config
        self.latest: dict[str, OrderBook] = {}
        self.task: asyncio.Task | None = None
        self.ready = asyncio.Event()

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        tasks = [asyncio.create_task(self._kraken())]
        if self.config.fallback and self.config.cross_venue == "bybit":
            tasks.append(asyncio.create_task(self._bybit()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.latest.clear()

    async def _kraken(self) -> None:
        c = self.config
        symbols = (
            (c.direct_symbol, c.cross_symbol)
            if c.fallback and c.cross_venue == "kraken"
            else (c.direct_symbol,)
        )
        while True:
            adapter, tasks = None, []
            try:
                adapter, instruments = await build_compact_kraken(symbols)
                tasks = [asyncio.create_task(self._pump(adapter, i)) for i in instruments]
                await asyncio.sleep(c.refresh_seconds)
            except Exception:
                await asyncio.sleep(c.retry_seconds)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                for symbol in symbols:
                    self.latest.pop(symbol, None)
                if adapter:
                    await _close(adapter)

    async def _bybit(self) -> None:
        """Independent REST snapshots; the native client maps Bybit's real data-generation ts."""
        c = self.config
        while True:
            adapter = None
            try:
                adapter, instruments = await build_compact_bybit()
                instrument = instruments[0]
                deadline = time.monotonic() + c.refresh_seconds
                while time.monotonic() < deadline:
                    try:
                        b = await asyncio.wait_for(adapter.fetch_book(instrument), 10)
                        if b.instrument != instrument:
                            raise ValueError("USDe REST identity mismatch")
                        b.validate_at(b.received_ms, c.max_age_ms)
                        self.latest[c.cross_symbol] = b
                        self.ready.set()
                    except Exception:
                        self.latest.pop(c.cross_symbol, None)
                    await asyncio.sleep(c.quote_poll_seconds)
            except Exception:
                self.latest.pop(c.cross_symbol, None)
                await asyncio.sleep(c.retry_seconds)
            finally:
                if adapter:
                    await _close(adapter)

    async def _pump(self, adapter: PublicAdapter, instrument: Instrument) -> None:
        c = self.config
        while True:
            try:
                b = await asyncio.wait_for(adapter.watch_book(instrument), c.watch_timeout_seconds)
                if b.instrument != instrument:
                    raise ValueError("USDe subscription identity mismatch")
                b.validate_at(b.received_ms, c.max_age_ms)
                # Store immutable levels separately from the mutable stream book.
                self.latest[instrument.symbol] = b
                self.ready.set()
                await asyncio.sleep(0)
            except Exception:
                self.latest.pop(instrument.symbol, None)
                await asyncio.sleep(c.retry_seconds)

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
