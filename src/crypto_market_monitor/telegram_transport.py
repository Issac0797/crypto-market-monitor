"""Telegram HTTPS transport. Never expose token-bearing errors."""

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping


class TelegramFailure(RuntimeError):
    def __init__(self, retry_after: int = 0, *, error_code: int = 0) -> None:
        super().__init__("Telegram delivery failed (details redacted)")
        self.retry_after = retry_after
        self.error_code = error_code


class TelegramAPI:
    def __init__(
        self, token: str, chat_id: int, allowed_chat_ids: set[int], *, profile: str | None = None
    ) -> None:
        if not token.strip() or type(chat_id) is not int or not chat_id:
            raise ValueError("Telegram token and numeric chat ID required")
        if chat_id not in allowed_chat_ids:
            raise ValueError("Telegram destination is not allowlisted")
        self._token, self._chat_id = token, chat_id
        if profile not in {None, "B"}:
            raise ValueError("Unknown Bot profile")
        self.profile = profile
        self.bot_id = token.split(":", 1)[0]
        if profile and (":" not in token or not self.bot_id.isdecimal()):
            raise ValueError("Invalid Bot credential format (values redacted)")

    @property
    def route(self) -> str:
        if self.profile:
            return f"telegram:{self.profile}:bot:{self.bot_id}:chat:{self._chat_id}"
        return f"telegram:{self._chat_id}"

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, bot: str | None = None
    ) -> "TelegramAPI":
        env = os.environ if env is None else env
        if bot not in {None, "B"}:
            raise ValueError("Unknown Bot profile")
        canonical = "CRYPTO_MARKET_MONITOR_TELEGRAM_"
        # B is a persisted route identifier. Its old variable prefix remains a
        # fallback so an existing private environment can upgrade in place.
        legacy = "CQL_TELEGRAM_B_"
        if any(key.startswith(canonical) for key in env):
            prefix = canonical
        elif bot == "B" and any(key.startswith(legacy) for key in env):
            prefix = legacy
        else:
            prefix = canonical
        try:
            return cls(
                env[prefix + "TOKEN"],
                int(env[prefix + "CHAT_ID"]),
                {int(v.strip()) for v in env[prefix + "ALLOWED_CHAT_IDS"].split(",")},
                profile="B" if bot == "B" else None,
            )
        except (KeyError, ValueError):
            raise ValueError(
                "Invalid Telegram environment configuration (values redacted)"
            ) from None

    def _verify(self) -> str:
        try:
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{self._token}/getMe", method="GET"
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.load(response)
            user = result["result"]
            if (
                not result["ok"]
                or user["is_bot"] is not True
                or str(user["id"]) != self.bot_id
                or not user["username"]
            ):
                raise ValueError("Bot identity mismatch")
            return user["username"]
        except Exception:
            raise TelegramFailure() from None

    async def verify(self) -> str:
        return await asyncio.to_thread(self._verify)

    def _request(self, message: str, *, html: bool = False) -> None:
        retry_after = 0
        error_code = 0
        try:
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                data=urllib.parse.urlencode(
                    {
                        "chat_id": self._chat_id,
                        "text": message.replace(self._token, "[REDACTED]"),
                        **({"parse_mode": "HTML"} if html else {}),
                    }
                ).encode(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    result = json.load(response)
            except urllib.error.HTTPError as exc:
                try:
                    result = json.loads(exc.read(65536))
                finally:
                    exc.close()
            if not result.get("ok"):
                error_code = int(result.get("error_code", 0))
                retry_after = max(0, int(result.get("parameters", {}).get("retry_after", 0)))
                raise ValueError("rejected")
            confirmed = result["result"]
            if confirmed["chat"]["id"] != self._chat_id or not confirmed["message_id"]:
                raise ValueError("unconfirmed destination")
        except Exception:
            raise TelegramFailure(retry_after, error_code=error_code) from None

    async def send(self, message: str) -> None:
        await asyncio.to_thread(self._request, message)

    async def send_html(self, message: str) -> None:
        """Explicit HTML opt-in; send() remains plain text."""
        await asyncio.to_thread(self._request, message, html=True)
