"""Local deployment primitives. No exchange clients or execution capability."""

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def instance_lock(path: Path) -> Iterator[None]:
    """Kernel-owned lock survives stale files and is released on process death."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def check_database(path: Path) -> None:
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")


def backup(root: Path, destination: Path) -> None:
    """Quiescent snapshot of all managed DBs; stop legacy writers separately."""
    root, destination = root.resolve(), destination.resolve()
    if destination.is_relative_to(root):
        raise ValueError("Backup must be outside state directory")
    with instance_lock(root / "service.lock"):
        destination.mkdir(parents=True, exist_ok=False)
        records = {}
        # Only DBs are copied: tokens, DPAPI files and health/locks are excluded.
        for source in sorted((root / "data").glob("*.sqlite3")):
            if source.is_symlink():
                raise ValueError("Symlink database rejected")
            target = destination / source.name
            with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as db:
                with sqlite3.connect(target) as output:
                    db.backup(output)
            check_database(target)
            records[source.name] = digest(target)
        if not records:
            raise ValueError("No managed databases to back up")
        atomic_json(destination / "manifest.json", {"schema": 1, "databases": records})


def restore(snapshot: Path, root: Path) -> None:
    """Validate everything before restoring into an empty data directory."""
    snapshot, root = snapshot.resolve(), root.resolve()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    records = manifest["databases"]
    if manifest["schema"] != 1 or not records:
        raise ValueError("Invalid backup manifest")
    for name, sha in records.items():
        path = snapshot / name
        if Path(name).name != name or not name.endswith(".sqlite3") or path.is_symlink():
            raise ValueError("Invalid database name")
        if digest(path) != sha:
            raise ValueError("Backup digest mismatch")
        check_database(path)
    with instance_lock(root / "service.lock"):
        data = root / "data"
        if data.exists() and any(data.iterdir()):
            raise ValueError("Restore requires empty data directory")
        data.mkdir(parents=True, exist_ok=True)
        for name in records:
            shutil.copyfile(snapshot / name, data / name)
