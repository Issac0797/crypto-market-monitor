"""Independent bounded stablecoin public sampler; outages don't stop BTC/ETH."""

import asyncio

from crypto_market_monitor.adapters.compact_bitstamp import build_compact_bitstamp
from crypto_market_monitor.adapters.compact_kraken import build_compact_kraken
from crypto_market_monitor.market_data.contracts import DataRequest, PublicAdapter
from crypto_market_monitor.market_data.models import Instrument, OrderBook
from crypto_market_monitor.market_data.scanner import Scanner

METADATA_REFRESH_SECONDS = 21600


class StablecoinFeed:
    def __init__(self, poll_seconds: int, *, usdc_fallback: bool = False) -> None:
        self.poll_seconds = poll_seconds
        self.latest: dict[str, OrderBook] = {}
        self.task: asyncio.Task | None = None
        self.ready = asyncio.Event()
        self.usdc_fallback = usdc_fallback
        self.fallback_latest: dict[str, OrderBook] = {}
        self.errors: dict[str, str] = {}
        self.fallback_task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())
        if self.usdc_fallback:
            self.fallback_task = asyncio.create_task(self._fallback())

    async def _fallback(self) -> None:
        while True:
            adapter = None
            try:
                adapter, instruments = await build_compact_kraken(("USDC/USD",))
                async with asyncio.timeout(METADATA_REFRESH_SECONDS):
                    while True:
                        try:
                            b = await asyncio.wait_for(adapter.watch_book(instruments[0]), 20)
                            if b.instrument != instruments[0]:
                                raise ValueError("USD fallback identity mismatch")
                            b.validate_at(b.received_ms, 30000)
                            self.fallback_latest["USDC/USD"] = b
                            self.errors.pop("kraken:USDC/USD", None)
                            await asyncio.sleep(0)
                        except TimeoutError:
                            # A quiet WS is not a new quote. Keep the immutable last
                            # quote; consumers still reject it after its real expiry.
                            self.errors["kraken:USDC/USD"] = "waiting_for_book_update"
                            await asyncio.sleep(self.poll_seconds)
                        except Exception:
                            self.fallback_latest.pop("USDC/USD", None)
                            self.errors["kraken:USDC/USD"] = "subscription_or_quote_invalid"
                            await asyncio.sleep(self.poll_seconds)
            except Exception:
                self.errors["kraken:USDC/USD"] = "source_reconnecting"
                await asyncio.sleep(self.poll_seconds)
            finally:
                self.fallback_latest.clear()
                if adapter:
                    try:
                        await asyncio.wait_for(adapter.close(), 5)
                    except Exception:
                        pass

    async def _run(self) -> None:
        while True:
            adapter, tasks = None, []
            try:
                adapter, instruments = await build_compact_bitstamp()
                tasks = [asyncio.create_task(self._pump(adapter, i)) for i in instruments]
                done, _ = await asyncio.wait(
                    tasks, timeout=METADATA_REFRESH_SECONDS, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()  # Unexpected worker failure rebuilds this source.
                if done:
                    raise RuntimeError("Stablecoin worker exited unexpectedly")
            except Exception:
                self.errors["bitstamp"] = "source_reconnecting"
                await asyncio.sleep(self.poll_seconds)
            finally:
                # Shutdown can cancel this supervisor while a metadata refresh
                # is already cleaning up. Finish cleanup before forwarding it.
                cleanup = asyncio.create_task(self._cleanup(adapter, tasks))
                interrupted = False
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        interrupted = True
                cleanup.result()
                if interrupted:
                    raise asyncio.CancelledError

    async def _cleanup(self, adapter: PublicAdapter | None, tasks: list[asyncio.Task]) -> None:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.latest.clear()
        if adapter:
            try:
                await asyncio.wait_for(adapter.close(), 5)
            except Exception:
                pass  # A failed cleanup must not prevent the next public reconnect.

    async def _pump(self, adapter: PublicAdapter, instrument: Instrument) -> None:
        # Each symbol owns its small Scanner lock, while both share one adapter
        # and the native client rate limiter. A slow USDC request cannot lock or delay USDT.
        scanner = Scanner({"bitstamp": adapter}, max_age_ms=30000, timeout_seconds=10)
        while True:
            try:
                scan = await scanner.run_once((DataRequest(instrument),))
                if scan.observations.books:
                    self.latest[instrument.symbol] = scan.observations.books[0]
                    self.errors.pop(instrument.symbol, None)
                    if len(self.latest) == 2:
                        self.ready.set()
                else:
                    self.latest.pop(instrument.symbol, None)
                    self.errors[instrument.symbol] = (
                        scan.failures[0][1] if scan.failures else "missing_quote"
                    )
            except Exception:
                self.latest.pop(instrument.symbol, None)
                self.errors[instrument.symbol] = "collection_failed"
            await asyncio.sleep(self.poll_seconds)

    async def close(self) -> None:
        if self.fallback_task:
            self.fallback_task.cancel()
            await asyncio.gather(self.fallback_task, return_exceptions=True)
            self.fallback_task = None
        self.fallback_latest.clear()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.latest.clear()
