"""Independent installation and release boundaries for the extracted monitor."""

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reviewed_config_bytes_match_migration_source():
    manifest = json.loads((ROOT / "docs/migration-manifest.json").read_text())
    configs = [item for item in manifest["files"] if item["source"].startswith("config/")]
    assert len(configs) == 3
    for item in configs:
        assert (
            hashlib.sha256((ROOT / item["destination"]).read_bytes()).hexdigest()
            == item["source_sha256"]
        )


def test_installed_monitor_imports_without_quant_project(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import importlib, importlib.util, pkgutil, crypto_market_monitor as package; "
            "assert importlib.util.find_spec('crypto_quant_lab') is None; "
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
        assert "config/bot-phase2-3.toml" in files
        assert "crypto-market-monitor" in archive.extractfile("pyproject.toml").read().decode()
        assert all("crypto_quant_lab" not in name for name in files)
        assert not any(
            "/prediction/" in name or "/research/" in name or name.startswith("data/")
            for name in files
        )
        for name, digest in files.items():
            body = archive.extractfile(name).read()
            assert hashlib.sha256(body).hexdigest() == digest
            if name.startswith("src/"):
                assert b"crypto_quant_lab" not in body
        config = archive.extractfile("config/bot-phase2-3.toml").read()
        assert config == (ROOT / "config/bot-phase2-3.toml").read_bytes()
