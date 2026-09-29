import sqlite3

import pytest

from aos.backup import snapshot


def test_failed_sqlite_snapshot_does_not_touch_existing_files(tmp_path):
    source = tmp_path / "corrupt.db"
    source.write_bytes(b"invalid SQLite input")
    destination = tmp_path / "snapshot.db"
    with pytest.raises(sqlite3.DatabaseError):
        snapshot(source, destination)
    assert not destination.exists()
    assert source.read_bytes() == b"invalid SQLite input"
    destination.write_bytes(b"existing backup")
    with pytest.raises(FileExistsError):
        snapshot(source, destination)
    assert destination.read_bytes() == b"existing backup"
