"""Bot-only read-only queries. Allowlisted destination, durable update IDs, no control RPC.

The bot_b_ meta key and engine:bot-b: command prefix are persisted compatibility identifiers.
"""

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from crypto_market_monitor.notification_events import EventType, NotificationEvent, Priority
from crypto_market_monitor.operations import instance_lock
from crypto_market_monitor.telegram_transport import TelegramAPI, TelegramFailure


class BotTelegramAPI(TelegramAPI):
    def rpc(self, method: str, arguments: dict | None = None) -> object:
        if self.profile != "B" or method not in {"getUpdates", "getWebhookInfo"}:
            raise ValueError("Read-only Bot RPC required")
        error_code = retry_after = 0
        try:
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{self._token}/{method}",
                data=json.dumps(arguments or {}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    data = json.load(response)
            except urllib.error.HTTPError as exc:
                # urlopen raises before JSON parsing on actual 409/429 responses.
                # Keep the HTTP status even when the error body is malformed.
                error_code = exc.code
                try:
                    data = json.loads(exc.read(65536))
                    retry_after = max(0, int(data.get("parameters", {}).get("retry_after", 0)))
                finally:
                    exc.close()
                raise ValueError("Read-only RPC HTTP failure") from None
            if not data.get("ok"):
                error_code = int(data.get("error_code", 0))
                retry_after = max(0, int(data.get("parameters", {}).get("retry_after", 0)))
                raise ValueError("Read-only RPC rejected")
            return data["result"]
        except Exception:
            raise TelegramFailure(retry_after, error_code=error_code) from None


class BotCommands:
    def __init__(self, api: BotTelegramAPI, watcher, root: Path) -> None:
        self.api, self.watcher, self.root = api, watcher, root
        self.task: asyncio.Task | None = None
        self.started_ms = time.time_ns() // 1000000
        self.last_success_ms = 0
        self.error = "starting"
        runtime = Path("/run/crypto-market-monitor")
        self.locks = runtime if runtime.is_dir() else root

    def reply(self, command: str, now_ms: int) -> str:
        w = self.watcher
        if command == "/status":
            try:
                h = json.loads((self.root / "health.json").read_text())
                age = max(0, (now_ms - h["updated_ms"]) // 1000)
                parts = [
                    f"Bot：{h.get('status', 'unknown')}；快照年龄{age}秒",
                    f"现货/基差：{h.get('basis_feed', {})}",
                    f"稳定币：{h.get('stablecoin_feed', {})}",
                    f"USDe：{h.get('usde_feed', {}).get('status', 'unknown')}",
                    f"资金费：{ {k: v['status'] for k, v in h.get('funding_feed', {}).items()} }",
                ]
                return "\n".join(parts)
            except (OSError, ValueError, KeyError):
                return "Bot：健康快照尚不可用；相关状态未知"
        if command == "/risks":
            with w.outbox.connect() as db:
                rows = db.execute(
                    "SELECT symbol,kind,phase,value FROM bot_b_risks "
                    "WHERE active!=0 OR phase IN ('PENDING','DATA_STALE') LIMIT 15"
                ).fetchall()
            return (
                "当前风险/未知状态：\n" + "\n".join(" | ".join(str(v) for v in r) for r in rows)
                if rows
                else "当前没有已记录的持续风险"
            )
        if command == "/history":
            with w.outbox.connect() as db:
                rows = db.execute(
                    "SELECT body FROM bot_b_history WHERE "
                    "json_extract(body,'$.status')!='COMMAND_REPLY' "
                    "ORDER BY ms DESC LIMIT 5"
                ).fetchall()
            from crypto_market_monitor.bot_templates import _time

            events = [NotificationEvent.from_json(r[0]) for r in rows]
            return (
                "近期事件：\n"
                + "\n".join(f"{_time(e.timestamp_ms)} {e.symbol} {e.status}" for e in events)
                if events
                else "暂无历史事件"
            )
        if command == "/settings":
            c = w.config
            return (
                f"价格：{[(r.window_ms // 60000, str(r.threshold * 100) + '%') for r in c.rules]}\n"
                f"基差：{c.basis.threshold * 100 if c.basis else '关闭'}%\n"
                f"预计资金费8h：{c.funding.threshold * 100 if c.funding else '关闭'}%\n"
                f"USDT/USDC：{c.stablecoin.threshold * 100 if c.stablecoin else '关闭'}%\n"
                f"USDe：{c.usde.rule.threshold * 100 if c.usde else '关闭'}%\n"
                f"最多发送尝试：{c.reliability.max_attempts}；"
                f"同事件冷却：{c.reliability.cooldown_ms // 60000}分钟\n"
                "只读查询；不提供远程交易或修改配置"
            )
        return "支持：/status /risks /history /settings（只读）"

    def process(self, updates: list, now_ms: int) -> None:
        with self.watcher.outbox.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM notification_meta WHERE key='bot_b_update_offset'"
            ).fetchone()
            offset = int(row[0]) if row else 0
            for update in sorted(updates, key=lambda u: u.get("update_id", -1)):
                uid = update.get("update_id")
                if type(uid) is not int or uid < offset:
                    continue
                message = update.get("message", {})
                chat = message.get("chat", {}).get("id")
                text = message.get("text", "")
                # Reply only to the configured bound chat. Never log incoming text or IDs.
                fresh = (
                    type(message.get("date")) is int
                    and self.started_ms - 5000 <= message["date"] * 1000 <= now_ms + 1000
                )
                if (
                    chat == self.api._chat_id
                    and isinstance(text, str)
                    and text.startswith("/")
                    and fresh
                ):
                    command = text.split()[0].split("@")[0].lower()
                    # reply() reads only; close this transaction's readers before enqueueing.
                    reply = self.reply(command, now_ms)
                    event = NotificationEvent(
                        f"engine:bot-b:command:{uid}",
                        EventType.SYSTEM,
                        "Bot",
                        "read-only-commands-v1",
                        now_ms,
                        "Bot",
                        "COMMAND_REPLY",
                        {"bot_channel": "B", "expires_ms": now_ms + 60000, "reply": reply},
                        Priority.NORMAL,
                        mode="MONITOR",
                    )
                    self.watcher.enqueue_in(db, event)
                offset = max(offset, uid + 1)
            db.execute(
                "INSERT OR REPLACE INTO notification_meta VALUES ('bot_b_update_offset',?)",
                (str(offset),),
            )
        self.last_success_ms = now_ms
        self.error = ""

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        # systemd provides a common runtime directory, preventing a second poller
        # even if another runner was pointed at a different state directory.
        with instance_lock(self.locks / f"poll-{self.api.bot_id}.lock"):
            info = await asyncio.to_thread(self.api.rpc, "getWebhookInfo")
            if info.get("url"):
                raise ValueError("Existing webhook prevents safe polling; no automatic deletion")
            while True:
                try:
                    offset = int(self.watcher.outbox.get_meta("bot_b_update_offset", "0"))
                    updates = await asyncio.to_thread(
                        self.api.rpc,
                        "getUpdates",
                        {
                            "offset": offset,
                            "timeout": 20,
                            "limit": 20,
                            "allowed_updates": ["message"],
                        },
                    )
                    self.process(updates, time.time_ns() // 1000000)
                except Exception as exc:
                    self.error = "polling_unavailable"
                    if isinstance(exc, TelegramFailure) and exc.error_code == 409:
                        self.error = "conflicting_polling_owner"
                        logging.error("Conflicting Bot polling owner; no further polling attempted")
                        await asyncio.Event().wait()  # fail closed until restart/operator review
                    logging.warning("Bot command polling unavailable (redacted)")
                    delay = (
                        max(5, min(exc.retry_after, 120)) if isinstance(exc, TelegramFailure) else 5
                    )
                    await asyncio.sleep(delay)

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
