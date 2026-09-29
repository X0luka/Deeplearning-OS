"""Local backups preserve reader identity; restores use a fresh export epoch and no permits."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

from .reader_store import ReaderStore
from .reading_jobs import artifact_lock
from .runner import worker_lock
from .translations import TranslationStore


def backup(root, destination):
    store = ReaderStore(root)
    with artifact_lock(store):
        return _backup(store, destination)


def _backup(store, destination):
    dest = Path(destination).resolve()
    if dest.exists() or dest.is_relative_to(store.root):
        raise ValueError("backup_requires_new_external_directory")
    with store.db() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='reading_jobs'").fetchone()
        if exists and db.execute("SELECT 1 FROM reading_jobs WHERE state IN ('queued','fetching','parsing','translating','validating') LIMIT 1").fetchone():
            raise ValueError("reading_processing_busy")
    dest.mkdir(parents=True)
    with worker_lock(store):
        with store.db() as source, sqlite3.connect(dest / "paper.db") as target:
            source.backup(target)
        with sqlite3.connect(dest / "paper.db") as db:
            ids = [r[0] for r in db.execute("SELECT id FROM documents")]
        (dest / "raw").mkdir()
        for identity in ids:
            path = store.root / "raw" / (identity + ".pdf")
            if not path.exists():
                path = store.root / "raw" / (identity + ".html")
            if hashlib.sha256(path.read_bytes()).hexdigest() != identity:
                raise ValueError("original_integrity_error")
            shutil.copyfile(path, dest / "raw" / path.name)
            reading = store.root / "reading" / identity
            if reading.is_dir():
                if reading.is_symlink() or any(p.is_symlink() for p in reading.rglob("*")):
                    raise ValueError("reading_backup_symlink")
                shutil.copytree(reading, dest / "reading" / identity)

    jobs = store.root / "reading-jobs"
    if jobs.exists():
        if jobs.is_symlink() or any(p.is_symlink() for p in jobs.rglob("*")):
            raise ValueError("reading_backup_symlink")
        shutil.copytree(jobs, dest / "reading-jobs")
    manifest = {
        str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in dest.rglob("*")
        if p.is_file()
    }
    (dest / "manifest.json").write_text(json.dumps({"version": 1, "files": manifest}, indent=2))
    return dest


def restore(source, destination):
    source = Path(source).resolve()
    dest = Path(destination).resolve()
    if dest.exists():
        raise ValueError("restore_requires_new_directory")
    manifest = json.loads((source / "manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict) or "paper.db" not in manifest["files"]:
        raise ValueError("invalid_backup")
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
            raise ValueError("invalid_backup_path")
        lexical = source / relative
        if lexical.is_symlink() or any(parent.is_symlink() for parent in lexical.parents if parent != source and parent.is_relative_to(source)):
            raise ValueError("invalid_backup_path")
        path = lexical.resolve()
        if not path.is_relative_to(source) or Path(name).is_absolute():
            raise ValueError("invalid_backup_path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("backup_integrity_error")
    with sqlite3.connect((source / "paper.db").as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup_database_invalid")
    dest.mkdir(parents=True)
    try:
        return _restore_verified(source, dest, manifest)
    except BaseException:
        shutil.rmtree(dest)
        raise


def _restore_verified(source, dest, manifest):
    for name in manifest["files"]:
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    store = ReaderStore(dest)
    with store.db() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup_database_invalid")
        db.execute("UPDATE reader_meta SET value=? WHERE key='export_epoch'", (uuid.uuid4().hex,))
        db.execute("UPDATE reader_meta SET value=? WHERE key='csrf'", (uuid.uuid4().hex,))
    with worker_lock(store):
        store.recover()
        TranslationStore(store).recover()
        with store.db() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs WHERE state='queued'")]
        for identity in ids:
            store.set_job(identity, "interrupted", error="restored_without_retry")
    return dest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print((backup if args.action == "backup" else restore)(args.source, args.destination))


if __name__ == "__main__":
    main()
