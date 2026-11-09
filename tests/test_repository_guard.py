"""The publication guard must inspect actual staged bytes and never print credentials."""

import importlib.util
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def guard():
    path = Path(__file__).resolve().parents[1] / "scripts/check-repository.py"
    spec = importlib.util.spec_from_file_location("repository_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "secret,rule",
    [
        ("123456789:" + "X" * 35, "telegram-token"),
        ("ghp_" + "Y" * 36, "github-token"),
        ("-----BEGIN " + "PRIVATE KEY-----", "private-key"),
        ("https://" + "user:password@example.invalid/repo", "credential-url"),
    ],
)
def test_recognized_credentials_are_reported_without_values(guard, secret, rule):
    findings = guard.inspect("settings.txt", ("header\n" + secret).encode())
    assert f"{rule}:line-2" in findings
    assert secret not in str(findings)


@pytest.mark.parametrize(
    "name", ["telegram.env", "profile.clixml", "DATA/private.txt", "local.db", "secret.key"]
)
def test_private_files_are_rejected_even_when_force_added(guard, name):
    assert "private-or-generated-path" in guard.inspect(name, b"local-only")


def test_staged_secret_cannot_hide_behind_clean_worktree(guard, monkeypatch, tmp_path, capsys):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    path = tmp_path / "config.txt"
    secret = "123456789:" + "X" * 35
    path.write_text(secret, encoding="utf-8")
    subprocess.run(["git", "add", "config.txt"], cwd=tmp_path, check=True, capture_output=True)
    path.write_text("clean working copy", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    staged = list(guard.candidates(True))
    assert staged == [("config.txt", secret.encode())]
    assert guard.inspect(*staged[0]) == ["telegram-token:line-1"]
    assert all(not guard.inspect(name, data) for name, data in guard.candidates(False))
    monkeypatch.setattr("sys.argv", ["check-repository.py", "--staged"])
    with pytest.raises(SystemExit) as caught:
        guard.main()
    assert caught.value.code == 1
    output = capsys.readouterr().out
    assert "telegram-token" in output and secret not in output


def test_source_validation_and_linux_line_endings(guard):
    assert not guard.inspect(".env.example", b"# no real credentials\n")
    assert not guard.inspect("src/module.py", b"from decimal import Decimal\n")
    assert "invalid-source-encoding-or-syntax" in guard.inspect("config.json", b"{")
    assert "invalid-source-encoding-or-syntax" in guard.inspect("module.py", b"def broken(")
    assert "binary-content" in guard.inspect("source.txt", b"binary\0")
    assert "forbidden-runtime-import:line-1" in guard.inspect("module.py", b"import ccxt\n")
    assert "linux-script-requires-lf" in guard.inspect("deploy/run.sh", b"#!/bin/sh\r\n")
