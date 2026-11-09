"""Bounded, injected scan pipeline. No scheduler and no default detector registry."""

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from crypto_market_monitor.market_data.contracts import (
    Capability,
    DataRequest,
    Detector,
    Finding,
    ObservationSet,
    PublicAdapter,
)
from crypto_market_monitor.market_data.models import FundingObservation, OrderBook
from crypto_market_monitor.models import DataError


@dataclass(frozen=True)
class ScanReport:
    observations: ObservationSet
    failures: tuple[tuple[str, str], ...]
    findings: tuple[Finding, ...]
    skipped_detectors: tuple[str, ...]
    enabled_detectors: tuple[str, ...]


class Scanner:
    def __init__(
        self,
        adapters: Mapping[str, PublicAdapter],
        detectors: tuple[Detector, ...] = (),
        *,
        max_age_ms: int = 30000,
        max_skew_ms: int = 10000,
        timeout_seconds: int = 10,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if min(max_age_ms, max_skew_ms, timeout_seconds) <= 0:
            raise ValueError("Positive collection bounds required")
        if len({d.detector_id for d in detectors}) != len(detectors):
            raise ValueError("Unique detector IDs required")
        if any(not d.detector_id or not d.required_keys for d in detectors):
            raise ValueError("Detector identity and data dependencies required")
        if any(key != adapter.venue for key, adapter in adapters.items()):
            raise ValueError("Adapter registry identity mismatch")
        self.adapters = dict(adapters)
        self.detectors = detectors
        self.max_age_ms, self.max_skew_ms = max_age_ms, max_skew_ms
        self.timeout_seconds = timeout_seconds
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self._locks = {key: asyncio.Lock() for key in adapters}

    async def run_once(self, requests: tuple[DataRequest, ...]) -> ScanReport:
        if len({r.key for r in requests}) != len(requests):
            raise ValueError("Duplicate data subscriptions")

        async def fetch(request: DataRequest) -> OrderBook | FundingObservation:
            adapter = self.adapters.get(request.instrument.venue)
            if adapter is None or request.capability not in adapter.capabilities:
                raise DataError("Adapter capability unavailable")
            # One in-flight call per venue; native transport also paces requests.
            async with self._locks[adapter.venue]:
                async with asyncio.timeout(self.timeout_seconds):
                    if request.capability == Capability.ORDER_BOOK:
                        return await adapter.fetch_book(request.instrument)
                    if request.capability == Capability.WATCH_ORDER_BOOK:
                        return await adapter.watch_book(request.instrument)
                    if request.capability == Capability.FUNDING:
                        return await adapter.fetch_funding(request.instrument)
                    if request.capability == Capability.WATCH_FUNDING:
                        return await adapter.watch_funding(request.instrument)
                    raise DataError("Unsupported data request")

        rows = await asyncio.gather(*(fetch(r) for r in requests), return_exceptions=True)
        now = self.clock()
        valid: dict[str, OrderBook | FundingObservation] = {}
        failures = []
        for request, row in zip(requests, rows, strict=True):
            if isinstance(row, BaseException):
                failures.append(
                    (
                        request.key,
                        "timeout" if isinstance(row, TimeoutError) else "source_unavailable",
                    )
                )
                continue
            try:
                expected = (
                    FundingObservation
                    if request.capability in {Capability.FUNDING, Capability.WATCH_FUNDING}
                    else OrderBook
                )
                if not isinstance(row, expected) or row.instrument != request.instrument:
                    raise DataError("Adapter returned mismatched observation")
                row.validate_at(now, self.max_age_ms)
                valid[request.key] = row
            except DataError:
                failures.append((request.key, "invalid_or_stale_data"))
        findings: list[Finding] = []
        skipped = []
        for detector in self.detectors:
            if not detector.required_keys.issubset(valid):
                skipped.append(detector.detector_id)
                continue
            selected = [valid[k] for k in sorted(detector.required_keys)]
            stamps = [v.source_ms for v in selected]
            if max(stamps) - min(stamps) > self.max_skew_ms:
                skipped.append(detector.detector_id)
                continue
            context = ObservationSet(
                now,
                tuple(v for v in selected if isinstance(v, OrderBook)),
                tuple(v for v in selected if isinstance(v, FundingObservation)),
            )
            try:
                produced = detector.evaluate(context)
                if not isinstance(produced, tuple) or any(
                    f.detector_id != detector.detector_id
                    or f.category not in {"opportunity", "anomaly"}
                    or not f.summary
                    or not f.evidence_keys
                    or not set(f.evidence_keys).issubset(detector.required_keys)
                    for f in produced
                ):
                    raise ValueError("Invalid detector findings")
                findings.extend(produced)
            except Exception:
                failures.append((detector.detector_id, "detector_failed"))
        observations = ObservationSet(
            now,
            tuple(v for v in valid.values() if isinstance(v, OrderBook)),
            tuple(v for v in valid.values() if isinstance(v, FundingObservation)),
        )
        return ScanReport(
            observations,
            tuple(failures),
            tuple(findings),
            tuple(skipped),
            tuple(d.detector_id for d in self.detectors),
        )

    async def close(self) -> None:
        await asyncio.gather(*(a.close() for a in self.adapters.values()), return_exceptions=True)
