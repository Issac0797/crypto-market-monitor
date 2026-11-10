"""Explicit low-memory market-risk runner for Bot; no execution path.

The legacy database filename is a persisted state compatibility identifier.
"""

import argparse
import asyncio
import json
import logging
import signal
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from crypto_market_monitor.bot_commands import BotCommands, BotTelegramAPI
from crypto_market_monitor.bot_queue import BotNotificationService
from crypto_market_monitor.bot_templates import format_bot
from crypto_market_monitor.market_data.bot_okx_books import BotOkxBooks
from crypto_market_monitor.market_data.funding_stream import FundingStream
from crypto_market_monitor.market_data.models import MarketKind
from crypto_market_monitor.market_data.risk_session import RiskSession
from crypto_market_monitor.market_data.risk_watch import RiskWatch
from crypto_market_monitor.market_data.risk_watch_config import RiskWatchConfig
from crypto_market_monitor.market_data.stablecoin_feed import StablecoinFeed
from crypto_market_monitor.market_data.usde_feed import UsdeFeed
from crypto_market_monitor.notifications import NotificationService
from crypto_market_monitor.operations import atomic_json, instance_lock
from crypto_market_monitor.risk_watch_backup import backup_if_due
from crypto_market_monitor.systemd_notify import notify
from crypto_market_monitor.telegram_transport import TelegramAPI


def now_ms() -> int:
    return time.time_ns() // 1_000_000


async def serve(
    config: RiskWatchConfig,
    root: Path,
    once: bool,
    telegram: bool,
    *,
    data_notifications: bool = False,
) -> None:
    from crypto_market_monitor.adapters.compact_okx import (
        build_compact_okx,
        refresh_compact_okx_safely,
    )

    data, logs = root / "data", root / "logs"
    data.mkdir(parents=True, exist_ok=True)
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        force=True,
        format="%(asctime)sZ %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(logs / "bot.log", maxBytes=512000, backupCount=2, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    logging.Formatter.converter = time.gmtime
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stopped.set))
    api_type = BotTelegramAPI if config.reliability and config.reliability.commands else TelegramAPI
    api = api_type.from_env(bot="B") if telegram else None
    if api:
        await api.verify()
    args = (data / "bot-b.sqlite3", api.send_html if api else None)
    if config.reliability:
        service = BotNotificationService(
            *args, route=api.route if api else "injected-sender", policy=config.reliability
        )
    else:
        service = NotificationService(
            *args, route=api.route if api else "injected-sender", formatter=format_bot
        )
    watcher = RiskWatch(service.outbox, config, data_notifications=data_notifications)
    adapter = None
    stream = None
    stable_feed = None
    usde_feed = None
    commands = None
    try:
        if api and config.reliability and not once:
            service.start()
            if config.reliability.commands:
                commands = BotCommands(api, watcher, root)
                commands.start()
        derivatives = config.basis is not None or config.funding is not None
        adapter, instruments = await build_compact_okx(config.symbols, derivatives=derivatives)
        if config.stablecoin or config.usde and config.usde.fallback:
            # Keep both cross legs close in source time without changing rule sampling.
            stable_poll = (
                min(config.poll_seconds, config.usde.quote_poll_seconds)
                if config.usde and config.usde.fallback
                else config.poll_seconds
            )
            stable_feed = StablecoinFeed(
                stable_poll,
                usdc_fallback=bool(config.reliability and config.reliability.usdc_fallback),
            )
            stable_feed.start()
        if config.usde:
            usde_feed = UsdeFeed(config.usde)
            usde_feed.start()
        if config.funding:
            stream = FundingStream(
                adapter,
                tuple(i for i in instruments if i.kind == MarketKind.PERPETUAL),
                rest_fallback=True,
                max_age_ms=config.max_age_ms,
            )
            stream.start()
        session = RiskSession(watcher, stream, now_ms())
        if once and usde_feed:
            try:
                await asyncio.wait_for(usde_feed.ready.wait(), 40)
            except TimeoutError:
                pass
        if once and stable_feed:
            try:
                await asyncio.wait_for(stable_feed.ready.wait(), 40)
            except TimeoutError:
                pass  # --once reports the unavailable USD feeds and fails below.
        if once and stream:
            deadline = loop.time() + 110
            while loop.time() < deadline:
                if all(
                    s in stream.latest
                    and 0 <= now_ms() - stream.latest[s].source_ms <= config.max_age_ms // 2
                    for s in (i.symbol for i in stream.instruments)
                ):
                    break
                await asyncio.sleep(0.2)
        scanner = BotOkxBooks(adapter, instruments, max_age_ms=config.max_age_ms)
        logging.info("Bot started; explicit market-risk rules, compact markets, no broker")
        cycles = 0
        next_metadata = loop.time() + 21600
        metadata_failures = 0
        while not stopped.is_set():
            if commands and commands.task.done():
                raise ValueError("Bot polling owner unavailable; restart safely")
            if service.task and service.task.done():
                raise ValueError("Bot notification worker unavailable; restart safely")
            began = loop.time()
            if began >= next_metadata:
                refreshed = await refresh_compact_okx_safely(
                    adapter, config.symbols, instruments, derivatives=derivatives
                )
                metadata_failures = 0 if refreshed else metadata_failures + 1
                if metadata_failures >= 3:
                    raise ValueError("Public metadata refresh exhausted; restart and revalidate")
                next_metadata = loop.time() + (21600 if refreshed else 60)
            scan = await scanner.run_once()
            stamp = now_ms()
            state = session.process(
                scan,
                stamp,
                stable_feed.latest if stable_feed else None,
                usde_feed.latest if usde_feed else None,
                stable_feed.fallback_latest if stable_feed else None,
                stable_feed.errors if stable_feed else None,
            )
            watcher.prune(stamp)
            # One bounded send attempt per poll; no second process or 10 Hz DB worker.
            if api and (not config.reliability or once):
                await service.deliver_once()
            summary = {
                "mode": "MARKET_RISK",
                "bot": "B",
                "updated_ms": now_ms(),
                "failures": scan.failures,
                "live_execution": False,
                "config_sha256": config.fingerprint,
                "data_notifications": data_notifications,
                "metadata_status": "refresh_retrying" if metadata_failures else "fresh",
                **watcher.summary(),
                **state,
                "command_polling": bool(
                    commands and not commands.error and now_ms() - commands.last_success_ms <= 60000
                ),
                "command_status": commands.error if commands else "disabled",
            }
            atomic_json(root / "health.json", summary)
            backup_if_due(service.outbox, root / "backups", now_ms())
            notify("READY=1\nWATCHDOG=1")
            cycles += 1
            if cycles == 1 or cycles % 20 == 0:
                logging.info(
                    "Market cycle completed; valid=%d/%d",
                    len(state["valid_symbols"]),
                    len(instruments),
                )
            if once:
                print(json.dumps(summary, ensure_ascii=False))
                if len(state["valid_symbols"]) != len(instruments) or state["status"] != "sampling":
                    raise ValueError("Public smoke failed")
                break
            try:
                await asyncio.wait_for(
                    stopped.wait(), timeout=max(0.1, config.poll_seconds - (loop.time() - began))
                )
            except TimeoutError:
                pass
    finally:
        if commands:
            await commands.close()
        if usde_feed:
            await usde_feed.close()
        if stable_feed:
            await stable_feed.close()
        if stream:
            await stream.close()
        if adapter:
            await adapter.close()
        await service.close()
        if not once:
            atomic_json(root / "health.json", {"status": "stopped", "updated_ms": now_ms()})
        notify("STOPPING=1")
        logging.info("Bot stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in BTC/ETH price alerts; Bot only")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument(
        "--data-notifications",
        action="store_true",
        help="Also send data outage/recovery/warmup diagnostics; market alerts are always enabled",
    )
    args = parser.parse_args()
    try:
        if not args.state_dir.is_absolute():
            raise ValueError("Absolute state directory required")
        config = RiskWatchConfig.load(args.config)
        with instance_lock(args.state_dir / "service.lock"):
            asyncio.run(
                serve(
                    config,
                    args.state_dir,
                    args.once,
                    args.telegram,
                    data_notifications=args.data_notifications,
                )
            )
    except KeyboardInterrupt:
        pass
    except Exception:
        parser.exit(
            1,
            "Bot unavailable; check config, data freshness, state or network (redacted).\n",
        )


if __name__ == "__main__":
    main()
