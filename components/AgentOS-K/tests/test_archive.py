"""Prove replay, atomic checkpointing, source generations, and view filtering."""
import copy

import pytest

from aos.adapters.sqlite import SQLiteArchive


def batch(revision=1, epoch="e1", cursor="c1", uid="u1", source="product_ui"):
    return {"instance_id": "i1", "export_epoch": epoch, "next_cursor": cursor,
            "events": [{"event_id": revision, "revision": revision, "operation": "upsert",
            "interaction": {"record_uid": uid, "source_interaction_id": 1, "revision": revision,
                            "updated_at": f"2026-01-0{revision}", "source_channel": source,
                            "conversation_id": "thread", "query": "<script>unsafe</script>",
                            "answer": "answer", "status": "succeeded", "citations": []}}]}


def test_duplicate_replay_and_feedback(tmp_path):
    a = SQLiteArchive(tmp_path / "a.db")
    assert a.apply("rag", batch(), None) == 1
    assert a.apply("rag", batch(), "c1") == 0
    assert a.items()["total"] == 1
    update = batch(2, cursor="c2")
    update["events"][0]["interaction"]["feedback"] = 1
    a.apply("rag", update, "c1")
    assert a.detail(1)["revision"] == 2


def test_failure_rolls_back_rows_and_cursor(tmp_path, monkeypatch):
    a = SQLiteArchive(tmp_path / "a.db")
    original = a._event
    def fail(*args):
        original(*args)
        raise RuntimeError("crash before commit")
    monkeypatch.setattr(a, "_event", fail)
    with pytest.raises(RuntimeError):
        a.apply("rag", batch(), None)
    assert a.checkpoint("rag") is None
    assert a.items()["total"] == 0
    monkeypatch.setattr(a, "_event", original)
    a.apply("rag", batch(), None)
    b = SQLiteArchive(a.path)
    assert b.checkpoint("rag") == "c1" and b.items()["total"] == 1
    with pytest.raises(ValueError, match="conflict"):
        b.apply("rag", batch(), None)


def test_restore_epoch_keeps_newer_archive_and_new_uid(tmp_path):
    a = SQLiteArchive(tmp_path / "a.db")
    a.apply("rag", batch(3), None)
    a.apply("rag", batch(1, epoch="restored", cursor="r1"), "c1")
    assert a.detail(1)["revision"] == 3
    assert a.detail(1)["rollback_detected"]
    a.apply("rag", batch(2, epoch="restored", cursor="r2", uid="new"), "r1")
    assert a.items()["total"] == 2


def test_probe_excluded_and_job_coalescing(tmp_path):
    a = SQLiteArchive(tmp_path / "a.db")
    a.apply("rag", batch(source="agentos_probe"), None)
    assert a.items()["total"] == 0
    identity = a.enqueue("rag")
    assert a.enqueue("rag") == identity
    assert a.claim()["id"] == identity
    a.recover()
    assert a.jobs()[0]["state"] == "interrupted"
    assert a.enqueue("rag") != identity


def test_different_instances_do_not_collide(tmp_path):
    a = SQLiteArchive(tmp_path / "a.db")
    a.apply("rag", batch(), None)
    other = copy.deepcopy(batch(cursor="different"))
    other["instance_id"] = "second"
    a.apply("rag", other, "c1")
    assert a.items()["total"] == 2


def test_backup_restores_wal_and_continues_sync(tmp_path):
    from aos.backup import snapshot
    a = SQLiteArchive(tmp_path / "live.db")
    with a.connection() as live:
        a.apply("rag", batch(), None)
        assert live.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        snapshot(a.path, tmp_path / "backup.db")
    snapshot(tmp_path / "backup.db", tmp_path / "restored.db")
    restored = SQLiteArchive(tmp_path / "restored.db")
    assert restored.items() == a.items()
    assert restored.checkpoint("rag") == "c1"
    restored.apply("rag", batch(2, cursor="c2"), "c1")
    assert restored.detail(1)["revision"] == 2
    assert a.detail(1)["revision"] == 1
    with pytest.raises(FileExistsError):
        snapshot(a.path, tmp_path / "restored.db")


def test_older_jobs_remain_addressable(tmp_path):
    a = SQLiteArchive(tmp_path / "archive.db")
    first = a.enqueue("rag")
    a.finish(first, "succeeded")
    for _ in range(35):
        a.finish(a.enqueue("rag"), "succeeded")
    assert a.job(first)["state"] == "succeeded"
