"""Consistent SQLite snapshots to new paths; never overwrite live data."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def snapshot(source: Path, destination: Path) -> None:
    """Use SQLite backup API (including WAL); require an unused destination."""
    source = source.resolve(strict=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    try:
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(destination)) as dst,
        ):
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("snapshot_integrity_failed")
    except BaseException:
        # Only the new file created by this call is removed on failure.
        destination.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description="Back up/restore SQLite to an unused path")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    snapshot(args.source, args.destination)


if __name__ == "__main__":
    main()
