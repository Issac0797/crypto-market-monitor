"""List Telegram chat IDs from the Bot's pending updates without printing the token."""

import json
import os
import re
import urllib.request

CANONICAL = "CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN"
LEGACY = "CQL_TELEGRAM_B_TOKEN"


def configured_token(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    token = env.get(CANONICAL) or env.get(LEGACY) or ""
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise ValueError("Telegram Bot token is not configured")
    return token


def chats_from_updates(updates: list[dict]) -> list[dict]:
    found = {}
    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if type(chat_id) is not int or chat_id == 0:
            continue
        found[chat_id] = {
            "chat_id": chat_id,
            "type": chat.get("type", "unknown"),
            "title": chat.get("title") or chat.get("username") or "private chat",
        }
    return sorted(found.values(), key=lambda item: item["chat_id"])


def fetch_chats(token: str) -> list[dict]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getUpdates", method="GET"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    if payload.get("ok") is not True or not isinstance(payload.get("result"), list):
        raise ValueError("Telegram returned an invalid response")
    return chats_from_updates(payload["result"])


def main() -> None:
    try:
        print(json.dumps(fetch_chats(configured_token()), ensure_ascii=False))
    except Exception:
        raise SystemExit(
            "Telegram chat lookup failed; configure the Bot token and send /start first "
            "(details redacted).\n"
        ) from None


if __name__ == "__main__":
    main()
