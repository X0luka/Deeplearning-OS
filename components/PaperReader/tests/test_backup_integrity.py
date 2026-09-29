"""Synthetic backup corruption and non-overwrite acceptance."""
import hashlib
import json
import sqlite3

import pytest

from paper_reader.reader_store import ReaderStore
from paper_reader.backup import backup, restore


def prepared(tmp_path):
    source = tmp_path / "source"
    ReaderStore(source)
    destination = tmp_path / "backup"
    backup(source, destination)
    return source, destination


def test_roundtrip_and_existing_target_preserved(tmp_path):
    source, saved = prepared(tmp_path)
    restored = tmp_path / "restored"
    restore(saved, restored)
    with sqlite3.connect(restored / "paper.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    sentinel = restored / "keep.txt"
    sentinel.write_text("existing user data")
    with pytest.raises(ValueError):
        restore(saved, restored)
    assert sentinel.read_text() == "existing user data"
    assert (source / "paper.db").is_file()


def test_corrupt_file_fails_before_creating_target(tmp_path):
    source, saved = prepared(tmp_path)
    original = (source / "paper.db").read_bytes()
    with (saved / "paper.db").open("ab") as output:
        output.write(b"corruption")
    target = tmp_path / "restored"
    with pytest.raises(ValueError, match="integrity"):
        restore(saved, target)
    assert not target.exists()
    assert (source / "paper.db").read_bytes() == original


def test_invalid_database_even_with_matching_hash_is_rejected(tmp_path):
    _, saved = prepared(tmp_path)
    data = b"not a SQLite database"
    (saved / "paper.db").write_bytes(data)
    manifest = json.loads((saved / "manifest.json").read_text())
    manifest["files"]["paper.db"] = hashlib.sha256(data).hexdigest()
    (saved / "manifest.json").write_text(json.dumps(manifest))
    target = tmp_path / "restored"
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        restore(saved, target)
    assert not target.exists()


def test_manifest_traversal_cannot_write_outside_target(tmp_path):
    _, saved = prepared(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("must stay intact")
    manifest = json.loads((saved / "manifest.json").read_text())
    manifest["files"]["../outside.txt"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    (saved / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore(saved, tmp_path / "restored")
    assert outside.read_text() == "must stay intact"
    assert not (tmp_path / "restored").exists()


def test_backup_symlink_cannot_read_external_database(tmp_path):
    _, saved = prepared(tmp_path)
    original = saved / "paper.db"
    moved = tmp_path / "external.db"
    original.replace(moved)
    original.symlink_to(moved)
    with pytest.raises(ValueError):
        restore(saved, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()

def test_copy_failure_removes_only_new_partial_destination(tmp_path, monkeypatch):
    import shutil
    import os
    source, saved = prepared(tmp_path)
    original = (source / "paper.db").read_bytes()
    artifact = saved / "raw/synthetic.txt"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_text("synthetic attachment")
    metadata = json.loads((saved / "manifest.json").read_text())
    metadata["files"]["raw/synthetic.txt"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (saved / "manifest.json").write_text(json.dumps(metadata))
    copy = shutil.copyfile
    def fail_attachment(src, dst, *args, **kwargs):
        if os.fspath(src).endswith("synthetic.txt"):
            raise OSError("simulated disk write failure")
        return copy(src, dst, *args, **kwargs)
    monkeypatch.setattr(shutil, "copyfile", fail_attachment)
    with pytest.raises((OSError, shutil.Error)):
        restore(saved, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert (source / "paper.db").read_bytes() == original
    assert artifact.read_text() == "synthetic attachment"
