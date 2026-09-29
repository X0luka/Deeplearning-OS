"""Validate the use case against replay and source cursor renewal."""
import pytest
from test_archive import batch

from aos.adapters.sqlite import SQLiteArchive
from aos.application import SyncService
from aos.domain import ProductError


async def test_expired_cursor_reconciles(tmp_path):
    store = SQLiteArchive(tmp_path / "a.db")
    store.apply("rag", batch(3), None)
    class Source:
        async def changes(self, cursor):
            if cursor == "c1":
                raise ProductError("cursor_expired")
            return {**batch(1, epoch="new", cursor="new-c"), "has_more": False}
    assert await SyncService("rag", Source(), store).run() == 1
    assert store.detail(1)["rollback_detected"]


async def test_stalled_cursor_is_error(tmp_path):
    store = SQLiteArchive(tmp_path / "a.db")
    store.apply("rag", batch(), None)
    class Source:
        async def changes(self, cursor):
            return {**batch(), "has_more": True}
    with pytest.raises(ProductError, match="cursor_stalled"):
        await SyncService("rag", Source(), store).run()
