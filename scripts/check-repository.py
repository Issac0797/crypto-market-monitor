"""Offline source-only Git guard. Findings contain paths/rules, never matched secrets.

Default: tracked and nonignored untracked working files. --staged: exact index blobs,
including staged content that differs from the working copy. No network or hooks installed.
"""

import argparse
import ast
import json
import re
import subprocess
import tomllib
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1_000_000
PRIVATE_DIRS = {
    ".venv",
    ".ssh",
    "data",
    "logs",
    "state",
    "backups",
    "releases",
    "wheelhouse",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".test-tmp",
    "build",
    "dist",
}
PRIVATE_SUFFIXES = {
    ".clixml",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".db",
    ".log",
    ".parquet",
    ".arrow",
    ".zip",
    ".gz",
    ".tar",
    ".whl",
    ".pyc",
    ".pyo",
}
PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "telegram-token": re.compile(rb"(?<![\w-])\d{6,12}:[A-Za-z0-9_-]{30,}(?![\w-])"),
    "github-token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "aws-access-key": re.compile(rb"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    "credential-url": re.compile(rb"(?:https?|ssh)://[^\s/:@]+:[^\s/@]+@"),
}


def git(*args: str, payload: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, input=payload, capture_output=True, check=True, timeout=30
    ).stdout


def candidates(staged: bool) -> Iterable[tuple[str, bytes]]:
    if not staged:
        names = set(
            git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
        )
        for raw in sorted(names - {b""}):
            name = raw.decode("utf-8")
            path = ROOT / name
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise ValueError("Symlink or path outside repository")
            # A tracked deletion need not be present until git add -u is run.
            if path.exists():
                yield name, path.read_bytes()
        return
    entries = []
    for entry in git("ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, identity, stage = metadata.split()
        if mode not in {b"100644", b"100755"} or stage != b"0":
            raise ValueError("Only regular, conflict-free source files may be staged")
        entries.append((name.decode("utf-8"), identity))
    blobs = git("cat-file", "--batch", payload=b"".join(sha + b"\n" for _, sha in entries))
    cursor = 0
    for name, _ in entries:
        end = blobs.index(b"\n", cursor)
        _, kind, size = blobs[cursor:end].split()
        if kind != b"blob":
            raise ValueError("Expected Git blob")
        cursor = end + 1
        length = int(size)
        yield name, blobs[cursor : cursor + length]
        cursor += length + 1


def inspect(name: str, data: bytes) -> list[str]:
    path = PurePosixPath(name)
    lower = path.name.lower()
    findings = []
    if (
        {part.lower() for part in path.parts} & PRIVATE_DIRS
        or path.suffix.lower() in PRIVATE_SUFFIXES
        or ".sqlite" in lower
        or lower.startswith((".env", "credentials", "secrets."))
        and lower != ".env.example"
        or lower.endswith(".env")
        or lower in {"id_rsa", "id_ed25519", "secrets.toml"}
    ):
        findings.append("private-or-generated-path")
    if len(data) > MAX_BYTES:
        findings.append("oversized-source-file")
    if b"\0" in data:
        findings.append("binary-content")
        return findings
    for rule, pattern in PATTERNS.items():
        for match in pattern.finditer(data):
            findings.append(f"{rule}:line-{data[: match.start()].count(bytes([10])) + 1}")
    try:
        text = data.decode("utf-8-sig")
        if path.suffix == ".py":
            tree = ast.parse(text, filename=name)
            for node in ast.walk(tree):
                modules = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                if any(m.split(".")[0] in {"ccxt", "openai", "anthropic"} for m in modules):
                    findings.append(f"forbidden-runtime-import:line-{node.lineno}")
        elif path.suffix == ".json":
            json.loads(text)
        elif path.suffix == ".toml":
            tomllib.loads(text)
    except (UnicodeError, SyntaxError, ValueError):
        findings.append("invalid-source-encoding-or-syntax")
    if path.suffix in {".sh", ".service", ".timer"} or name == "deploy/cql-backup":
        if b"\r" in data:
            findings.append("linux-script-requires-lf")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Check exact Git index bytes")
    args = parser.parse_args()
    count = failed = 0
    try:
        for name, data in candidates(args.staged):
            count += 1
            for rule in inspect(name, data):
                failed += 1
                print(f"{name}: {rule}")
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(1, "Repository inspection failed (details redacted).\n")
    if not count:
        parser.exit(1, "No files inspected; stage source files first.\n")
    mode = "index" if args.staged else "worktree"
    print(f"Repository guard: {count} files, {failed} findings; mode={mode}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
