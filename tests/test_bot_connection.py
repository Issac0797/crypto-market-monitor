import asyncio
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from crypto_market_monitor.bot_cli import configured_api, run
from crypto_market_monitor.telegram_transport import TelegramAPI, TelegramFailure

CHAT_ID_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "telegram-chat-id.py"


def load_chat_id_helper():
    spec = importlib.util.spec_from_file_location("telegram_chat_id", CHAT_ID_HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bot_env():
    return {
        "CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN": "456:synthetic_bot",
        "CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID": "10",
        "CRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS": "10",
    }


def test_single_bot_credentials_are_all_or_nothing():
    env = bot_env()
    b = TelegramAPI.from_env(env, bot="B")
    assert b.route == "telegram:B:bot:456:chat:10"
    env["CRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID"] = ""
    with pytest.raises(ValueError):
        TelegramAPI.from_env(env, bot="B")


def test_legacy_environment_is_used_only_when_new_names_are_absent():
    legacy = {
        "CQL_TELEGRAM_B_TOKEN": "789:synthetic_legacy",
        "CQL_TELEGRAM_B_CHAT_ID": "10",
        "CQL_TELEGRAM_B_ALLOWED_CHAT_IDS": "10",
    }
    api = TelegramAPI.from_env(legacy, bot="B")
    assert api.bot_id == "789"
    with patch.dict("os.environ", legacy, clear=True):
        assert configured_api().bot_id == "789"


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


def test_bot_connection_test_reaches_its_own_sender(tmp_path):
    api = TelegramAPI.from_env(bot_env(), bot="B")
    api.verify = AsyncMock(return_value="fixture_bot")
    api.send = AsyncMock()
    args = SimpleNamespace(db=tmp_path / "b.sqlite3", verify=False, test_message=True, once=False)
    with patch("crypto_market_monitor.bot_cli.configured_api", return_value=api):
        assert asyncio.run(run(args)) == 0
    assert "Bot" in api.send.call_args.args[0]


def test_chat_id_helper_reads_only_unique_numeric_chats():
    helper = load_chat_id_helper()
    assert helper.configured_token({"CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN": "123:synthetic"}) == (
        "123:synthetic"
    )
    updates = [
        {"message": {"chat": {"id": 10, "type": "private", "username": "tester"}}},
        {"message": {"chat": {"id": -10020, "type": "supergroup", "title": "Risk"}}},
        {"message": {"chat": {"id": 10, "type": "private", "username": "tester"}}},
        {"edited_message": {"chat": {"id": 30, "type": "group", "title": "Ops"}}},
    ]
    assert helper.chats_from_updates(updates) == [
        {"chat_id": -10020, "type": "supergroup", "title": "Risk"},
        {"chat_id": 10, "type": "private", "title": "tester"},
        {"chat_id": 30, "type": "group", "title": "Ops"},
    ]
