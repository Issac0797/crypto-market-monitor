"""Two bounded, public native WebSocket subscriptions inside the Bot process."""

import asyncio
import logging

from crypto_market_monitor.market_data.contracts import PublicAdapter
from crypto_market_monitor.market_data.models import FundingObservation, Instrument
from crypto_market_monitor.models import DataError


class FundingStream:
    def __init__(
        self,
        adapter: PublicAdapter,
        instruments: tuple[Instrument, ...],
        *,
        rest_fallback: bool = False,
        max_age_ms: int = 30000,
    ) -> None:
        self.adapter, self.instruments = adapter, instruments
        self.latest: dict[str, FundingObservation] = {}
        self.errors: set[str] = set()
        self.tasks: list[asyncio.Task] = []
        self.rest_fallback, self.max_age_ms = rest_fallback, max_age_ms
        self.reasons: dict[str, str] = {}

    def _accept(self, instrument: Instrument, observation: FundingObservation) -> None:
        if observation.instrument != instrument:
            raise DataError("Funding subscription identity mismatch")
        previous = self.latest.get(instrument.symbol)
        if previous and (
            observation.source_ms < previous.source_ms
            or observation.source_ms == previous.source_ms
            and observation.rate != previous.rate
        ):
            raise DataError("Inconsistent funding publication ordering")
        self.latest[instrument.symbol] = observation
        self.errors.discard(instrument.symbol)
        self.reasons.pop(instrument.symbol, None)

    def start(self) -> None:
        self.tasks = [asyncio.create_task(self._pump(i)) for i in self.instruments]

    async def _pump(self, instrument: Instrument) -> None:
        while True:
            try:
                observation = await asyncio.wait_for(self.adapter.watch_funding(instrument), 100)
                self._accept(instrument, observation)
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self.rest_fallback:
                    try:
                        # Explicit Bot fallback to the SAME OKX forecast and
                        # actual schedule; adapter still parses native info.ts.
                        observation = await asyncio.wait_for(
                            self.adapter.fetch_funding(instrument), 5
                        )
                        observation.validate_at(observation.received_ms, self.max_age_ms)
                        self._accept(instrument, observation)
                        await asyncio.sleep(5)
                        continue
                    except Exception:
                        pass
                self.latest.pop(instrument.symbol, None)
                if instrument.symbol not in self.errors:
                    logging.warning("Public funding subscription unavailable; retrying (redacted)")
                self.errors.add(instrument.symbol)
                self.reasons[instrument.symbol] = "funding_stream_or_public_rest_unavailable"
                await asyncio.sleep(5)

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
