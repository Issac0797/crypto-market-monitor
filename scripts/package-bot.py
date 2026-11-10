"""Create a source-only Bot archive, excluding credentials, caches and local data."""

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/bot.toml")
    args = parser.parse_args()
    candidate = (root / args.config).resolve()
    if candidate.parent != root / "config" or candidate.suffix != ".toml":
        raise ValueError("Reviewed project config required")
    files = sorted(
        [
            *root.glob("src/**/*.py"),
            root / "pyproject.toml",
            root / "README.md",
            root / "LICENSE",
            *root.glob("config/bot*.toml"),
            root / "AGENTS.md",
            root / "ROADMAP.md",
            root / "DEVLOG.md",
            root / "deploy/systemd/crypto-market-monitor.service",
            root / "deploy/install-bot.sh",
            root / "scripts/migrate-bot-config.py",
            root / "scripts/telegram-bot.ps1",
            root / "scripts/telegram-chat-id.py",
            root / "scripts/read_telegram_target.py",
            root / "scripts/deploy-bot-credential.ps1",
            *root.glob("deploy/*bot*.sh"),
            *root.glob("docs/*.md"),
            candidate,
        ]
    )
    files = sorted(set(files))
    checksums = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    fingerprint = hashlib.sha256(json.dumps(checksums, sort_keys=True).encode()).hexdigest()
    release_id = f"bot-{fingerprint[:16]}"
    output = root / "data" / f"{release_id}.tar.gz"
    output.parent.mkdir(exist_ok=True)
    manifest = json.dumps({"source_sha256": fingerprint, "files": checksums}, indent=2).encode()
    with tarfile.open(output, "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        info = tarfile.TarInfo("bot-release.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
    print(
        json.dumps(
            {
                "release_id": release_id,
                "archive": str(output),
                "archive_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
