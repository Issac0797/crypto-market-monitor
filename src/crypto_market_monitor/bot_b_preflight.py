"""Consistent read-only production snapshot and one explicitly authorized Telegram test."""

import argparse
import asyncio
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from crypto_market_monitor.bot_b_commands import BotBTelegramAPI


def snapshot(source: Path, destination: Path) -> None:
    if destination.exists() or destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("New isolated snapshot directory required")
    data = destination / "data"
    data.mkdir(parents=True, mode=0o700)
    src = source / "data/bot-b.sqlite3"
    with closing(sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        with closing(sqlite3.connect(data / "bot-b.sqlite3")) as target:
            db.backup(target)
            if target.execute("pragma integrity_check").fetchone()[0] != "ok":
                raise ValueError("Snapshot integrity failed")
    os.chmod(data / "bot-b.sqlite3", 0o600)


async def telegram_test(state: Path) -> dict:
    # Marker is made durable BEFORE the network call: ambiguous failure cannot cause
    # an automatic duplicate "deployment test". It does not poll or touch production.
    marker = state / "telegram-deployment-test.json"
    if marker.exists():
        return json.loads(marker.read_text())
    api = BotBTelegramAPI.from_env(bot="B")
    await api.verify()
    webhook = await asyncio.to_thread(api.rpc, "getWebhookInfo")
    if webhook.get("url"):
        raise ValueError("Existing webhook; refuse future polling cutover")
    result = {
        "attempted_ms": time.time_ns() // 1000000,
        "confirmed": False,
        "webhook_present": False,
    }
    with marker.open("x", encoding="utf-8") as f:
        json.dump(result, f)
        f.flush()
        os.fsync(f.fileno())
    await api.send_html(
        "🧪 <b>部署测试｜Bot B</b>\n新版预检：Telegram链路验证。\n"
        "此消息不是风险事件；现有生产服务尚未切换。"
    )
    result["confirmed"] = True
    marker.write_text(json.dumps(result), encoding="utf-8")
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source-state", type=Path)
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--telegram-test", action="store_true")
    a = p.parse_args()
    if a.telegram_test:
        print(json.dumps(asyncio.run(telegram_test(a.state_dir))))
    else:
        if a.source_state is None:
            raise ValueError("Snapshot source required")
        snapshot(a.source_state, a.state_dir)
        print("Read-only consistent isolated snapshot completed")


if __name__ == "__main__":
    main()
