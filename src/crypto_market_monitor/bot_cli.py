"""Explicit Bot status, identity verification, test and outbound delivery."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from crypto_market_monitor.logging_setup import setup_logging
from crypto_market_monitor.notification_events import EventType, NotificationEvent
from crypto_market_monitor.notifications import NotificationService
from crypto_market_monitor.telegram_transport import TelegramAPI

BOT_DATABASES = {"B": Path("data/notifications-market.sqlite3")}


def configured_api(bot: str) -> TelegramAPI:
    if bot != "B":
        raise ValueError("This project owns Bot only")
    api = TelegramAPI.from_env(bot=bot)
    try:
        other = TelegramAPI.from_env(bot="A")
    except ValueError:
        other = None
    if other is not None and api.bot_id == other.bot_id:
        raise ValueError("Bot A and Bot must be distinct Telegram bots")
    return api


def bind_channel(service: NotificationService, channel: str) -> None:
    service.outbox.bind_channel(channel)


async def run(args: argparse.Namespace) -> int:
    api = configured_api(args.bot)
    username = await api.verify()
    if args.verify:
        print(json.dumps({"bot": args.bot, "verified": True, "username": username}))
        return 0
    service = NotificationService(args.db, api.send, route=api.route)
    try:
        bind_channel(service, args.bot)
        if args.test_message:
            identity = f"bot-test:{args.bot}:{uuid4().hex}"
            event = NotificationEvent(
                identity,
                EventType.SYSTEM,
                "bot-cli",
                "connection-test",
                time.time_ns() // 1_000_000,
                "TELEGRAM",
                "CONNECTION_TEST",
                {
                    "bot_channel": args.bot,
                    "note": "Bot 行情风险通道连接测试",
                },
                mode="MONITOR",
            )
            await service.publish(event)
            await service.deliver_once()
            with service.outbox.connect() as db:
                state = db.execute(
                    "SELECT state FROM notification_outbox WHERE event_id=?", (identity,)
                ).fetchone()[0]
            print(json.dumps({"bot": args.bot, "test_delivered": state == "sent"}))
            return 0 if state == "sent" else 1
        if args.once:
            await service.deliver_once()
            return 0
        service.start()
        await asyncio.Event().wait()
    finally:
        await service.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Bot outbound connection tools; no trading")
    # Profile B is a legacy persisted Telegram route identifier.
    parser.add_argument("--bot", choices=("B",), default="B")
    parser.add_argument("--db", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--status", action="store_true", help="Offline configuration status")
    modes.add_argument("--verify", action="store_true", help="Public Bot identity check via getMe")
    modes.add_argument("--test-message", action="store_true")
    modes.add_argument("--once", action="store_true")
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()
    args.db = args.db or BOT_DATABASES[args.bot]
    if args.status:
        try:
            configured_api(args.bot)
            configured = True
        except ValueError:
            configured = False
        print(json.dumps({"bot": args.bot, "configured": configured, "network_started": False}))
        return
    if not args.telegram:
        parser.error("Network actions require explicit --telegram; use --status for offline status")
    setup_logging("INFO")
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        result = 0
    except Exception:
        parser.exit(
            1, "Bot connection unavailable; check local credentials and destination (redacted).\n"
        )
    raise SystemExit(result)


if __name__ == "__main__":
    main()
