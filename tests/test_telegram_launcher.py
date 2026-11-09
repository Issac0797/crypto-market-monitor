import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "scripts" / "read_telegram_target.py"


def create_database(path, value):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE notification_meta(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO notification_meta VALUES ('destination',?)", (value,))


@pytest.mark.parametrize(
    "value,expected",
    [
        ("telegram:123456", "123456"),
        ("telegram:-100123456", "-100123456"),
        ("telegram:0", ""),
        ("telegram:someone", ""),
        ("telegram:9223372036854775808", ""),
    ],
)
def test_target_helper_reads_only_valid_bound_identity(tmp_path, value, expected):
    path = tmp_path / "saved target.sqlite3"
    create_database(path, value)
    before = path.read_bytes()
    result = subprocess.run(
        [sys.executable, str(HELPER), "--db", str(path)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0 and result.stdout.strip() == expected
    assert path.read_bytes() == before


def test_missing_target_does_not_create_database_and_corruption_is_failure(tmp_path):
    path = tmp_path / "not-created.sqlite3"
    result = subprocess.run(
        [sys.executable, str(HELPER), "--db", str(path)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0 and not result.stdout and not path.exists()
    path.write_text("not a SQLite file", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(HELPER), "--db", str(path)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 1 and not result.stdout
    assert "Traceback" not in result.stderr and "redacted" in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 interoperability")
def test_actual_windows_powershell_51_preserves_helper_paths_and_python_quotes(tmp_path):
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    db = tmp_path / "saved target 行情.sqlite3"
    create_database(db, "telegram:123456")
    wrapper = tmp_path / "invoke-helper.ps1"
    wrapper.write_text(
        "param([string]$Python,[string]$Helper,[string]$Db)\n"
        "& $Python $Helper --db $Db\nexit $LASTEXITCODE\n",
        encoding="ascii",
    )
    result = subprocess.run(
        [
            str(shell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "-Python",
            sys.executable,
            "-Helper",
            str(HELPER),
            "-Db",
            str(db),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "123456"
