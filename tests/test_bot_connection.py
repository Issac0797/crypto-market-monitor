import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from crypto_market_monitor.bot_cli import configured_api, run
from crypto_market_monitor.telegram_transport import TelegramAPI, TelegramFailure


def bot_env():
    return {
        "CQL_TELEGRAM_TOKEN": "123:synthetic_a",
        "CQL_TELEGRAM_CHAT_ID": "10",
        "CQL_TELEGRAM_ALLOWED_CHAT_IDS": "10",
        "CQL_TELEGRAM_B_TOKEN": "456:synthetic_b",
        "CQL_TELEGRAM_B_CHAT_ID": "10",
        "CQL_TELEGRAM_B_ALLOWED_CHAT_IDS": "10",
    }


def test_old_bot_credentials_reused_by_a_only_and_no_partial_fallback():
    env = bot_env()
    a = TelegramAPI.from_env(env, bot="A")
    b = TelegramAPI.from_env(env, bot="B")
    assert a.route == "telegram:A:bot:123:chat:10"
    assert b.route == "telegram:B:bot:456:chat:10"
    env["CQL_TELEGRAM_A_CHAT_ID"] = "10"
    with pytest.raises(ValueError):
        TelegramAPI.from_env(env, bot="A")
    with pytest.raises(ValueError):
        TelegramAPI.from_env(
            {k: v for k, v in env.items() if not k.startswith("CQL_TELEGRAM_B_")}, bot="B"
        )


def test_a_and_b_must_be_distinct_bots():
    env = bot_env()
    env["CQL_TELEGRAM_B_TOKEN"] = env["CQL_TELEGRAM_TOKEN"]
    with patch.dict("os.environ", env, clear=True), pytest.raises(ValueError, match="distinct"):
        configured_api("B")


def test_verify_bot_identity_and_redacted_errors():
    api = TelegramAPI.from_env(bot_env(), bot="B")
    data = io.BytesIO(
        json.dumps(
            {"ok": True, "result": {"id": 456, "is_bot": True, "username": "fixture_bot"}}
        ).encode()
    )
    with patch("urllib.request.urlopen", return_value=data):
        assert asyncio.run(api.verify()) == "fixture_bot"
    with patch("urllib.request.urlopen", side_effect=RuntimeError("456:synthetic_b")):
        with pytest.raises(TelegramFailure) as error:
            asyncio.run(api.verify())
        assert "synthetic_b" not in str(error.value)


def test_bot_b_connection_test_reaches_its_own_sender(tmp_path):
    api = TelegramAPI.from_env(bot_env(), bot="B")
    api.verify = AsyncMock(return_value="fixture_bot")
    api.send = AsyncMock()
    args = SimpleNamespace(
        bot="B", db=tmp_path / "b.sqlite3", verify=False, test_message=True, once=False
    )
    with patch("crypto_market_monitor.bot_cli.configured_api", return_value=api):
        assert asyncio.run(run(args)) == 0
    assert "Bot B" in api.send.call_args.args[0]
