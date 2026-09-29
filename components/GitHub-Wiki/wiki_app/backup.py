"""Consistent local backup and verified restore, refusing existing targets."""
import argparse
import fcntl
import hashlib
import json
import shutil
import sqlite3
import warnings
from pathlib import Path

from .store import Store, uid


def inventory(root):
    files = [root / "wiki.db"]
    for name in ("raw", "wiki", "notes"):
        directory = root / name
        if directory.is_symlink():
            raise ValueError("backup_symlink_not_supported")
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_symlink():
                    raise ValueError("backup_symlink_not_supported")
                if path.is_file():
                    files.append(path)
    if any(p.is_symlink() for p in files):
        raise ValueError("backup_symlink_not_supported")
    return files


def database_ok(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup_database_invalid")


def backup(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists() or source == target or source in target.parents:
        raise ValueError("backup_target_must_be_new_and_outside_source")
    if not (source / "wiki.db").is_file():
        raise ValueError("source_database_missing")
    with (source / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inventory(source)
        database_ok(source / "wiki.db")
        target.mkdir(parents=True)
        try:
            with sqlite3.connect((source / "wiki.db").as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(target / "wiki.db") as dst:
                src.backup(dst)
            for name in ("raw", "wiki", "notes"):
                if (source / name).exists():
                    shutil.copytree(source / name, target / name)
            files = {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in inventory(target)}
            (target / "backup.json").write_text(json.dumps({"format": 2, "files": files}, indent=2))
        except BaseException:
            shutil.rmtree(target)
            raise


def verify(source):
    inventory(source)
    metadata = json.loads((source / "backup.json").read_text())
    if metadata.get("format") == 1:
        warnings.warn("legacy_backup_has_no_file_checksums", stacklevel=2)
    elif metadata.get("format") == 2:
        files = metadata.get("files")
        if not isinstance(files, dict) or "wiki.db" not in files:
            raise ValueError("invalid_backup_manifest")
        actual = {str(p.relative_to(source)) for p in inventory(source)}
        if set(files) != actual:
            raise ValueError("backup_file_set_mismatch")
        for name, digest in files.items():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or path.as_posix() != name:
                raise ValueError("invalid_backup_path")
            if hashlib.sha256((source / path).read_bytes()).hexdigest() != digest:
                raise ValueError("backup_integrity_error")
    else:
        raise ValueError("invalid_backup_manifest")
    database_ok(source / "wiki.db")


def restore(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists():
        raise ValueError("backup_target_must_be_new_and_outside_source")
    verify(source)
    backup(source, target)
    try:
        Store(target).set_meta("epoch", uid())
    except BaseException:
        shutil.rmtree(target)
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("operation", choices=["backup", "restore"])
    p.add_argument("source")
    p.add_argument("target")
    a = p.parse_args()
    (backup if a.operation == "backup" else restore)(a.source, a.target)


if __name__ == "__main__":
    main()
