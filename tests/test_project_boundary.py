"""Independent installation and release boundaries for the monitor."""

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FROZEN_CONFIGS = {
    "config/bot.toml": "d2e95e8bd0a1115f43132bbfbaa644fb8c793b05cefb1bce229418f3ead683db",
    "config/bot-phase1.toml": ("320b1919f54698b5ee7796c8c56d7280228421cf54d56e121034d444119a51b0"),
    "config/bot-phase2-3.toml": (
        "9cee084e6c47e92108667a800d7ea77680da0b8c6e53ae59f7e61c9e83b9cee3"
    ),
    "config/bot-phase4.toml": ("ba3936b06aea11a66a6b33029fa6394ac833163ba782b552a895607b677b9298"),
}


def test_reviewed_config_bytes_are_frozen():
    for name, expected in FROZEN_CONFIGS.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected


def test_installed_monitor_imports_without_extra_paths(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import importlib, pkgutil, crypto_market_monitor as package; "
            "[importlib.import_module(item.name) "
            "for item in pkgutil.walk_packages(package.__path__, package.__name__ + '.')]; "
            "print('standalone imports passed')",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "standalone imports passed" in result.stdout


def test_release_contains_independent_runtime_and_exact_reviewed_config():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/package-bot.py"),
            "--config",
            "config/bot-phase2-3.toml",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    path = Path(output["archive"])
    assert path.resolve().is_relative_to(ROOT / "data")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == output["archive_sha256"]
    with tarfile.open(path) as archive:
        manifest = json.load(archive.extractfile("bot-release.json"))
        files = manifest["files"]
        assert archive.extractfile("LICENSE").read() == (ROOT / "LICENSE").read_bytes()
        assert "src/crypto_market_monitor/risk_watch_cli.py" in files
        assert "src/crypto_market_monitor/systemd_notify.py" in files
        assert "deploy/systemd/crypto-market-monitor.service" in files
        assert "config/bot-phase2-3.toml" in files
        assert "crypto-market-monitor" in archive.extractfile("pyproject.toml").read().decode()
        assert not any(
            "/prediction/" in name or "/research/" in name or name.startswith("data/")
            for name in files
        )
        for name, digest in files.items():
            body = archive.extractfile(name).read()
            assert hashlib.sha256(body).hexdigest() == digest
        config = archive.extractfile("config/bot-phase2-3.toml").read()
        assert config == (ROOT / "config/bot-phase2-3.toml").read_bytes()
