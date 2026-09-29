import pytest
from conftest import tiny_pdf
from fastapi.testclient import TestClient

from paper_reader.backup import backup, restore
from paper_reader.reader_store import ReaderStore
from paper_reader.types import Question
from paper_reader.web import create_app


@pytest.fixture
def reader(tmp_path):
    store = ReaderStore(tmp_path / "reader")
    doc = store.import_pdf(
        tiny_pdf(
            tmp_path / "paper.pdf",
            [[(50, 700, "This method uses residual learning for experiments.")]],
        )
    )
    session = store.create_session(doc["id"])
    return store, doc, session


def test_note_edits_do_not_overwrite_evidence(reader):
    store, doc, s = reader
    sha = doc["parsed"]["parse_sha256"]
    note = store.save_note(s["id"], "first", None, "saved explanation", sha)
    store.save_note(s["id"], "my revision", parse_sha256=sha, identity=note, revision=1)
    row = store.notes(s["id"])[0]
    assert row["body"] == "my revision" and row["explanation"] == "saved explanation"
    with pytest.raises(ValueError, match="note_changed_reload"):
        store.save_note(s["id"], "stale", parse_sha256=sha, identity=note, revision=1)


def test_session_position_and_note_reopen(reader):
    store, doc, s = reader
    store.position(s["id"], 1, 1.5)
    store.save_note(s["id"], "persist", parse_sha256=doc["parsed"]["parse_sha256"])
    reopened = ReaderStore(store.root)
    assert reopened.session(s["id"])["zoom"] == 1.5
    assert reopened.notes(s["id"])[0]["body"] == "persist"


def test_enqueue_is_idempotent_and_scoped(reader):
    store, doc, s = reader
    q = Question(document=doc["id"], mode="fulltext", question="what?")
    identity, fresh = store.enqueue_session(q, s["id"], "same-request")
    assert fresh
    assert store.enqueue_session(q, s["id"], "same-request") == (identity, False)
    other = store.create_session(doc["id"])
    with pytest.raises(ValueError, match="request_key_reused"):
        store.enqueue_session(q, other["id"], "same-request")
    q.previous = identity
    store.set_job(identity, "completed", answer={"claims": []})
    with pytest.raises(ValueError, match="invalid_previous_question"):
        store.enqueue_session(q, other["id"], "next-request")


def test_events_paginate_and_preserve_revisions(reader):
    store, doc, s = reader
    note = store.save_note(s["id"], "one", parse_sha256=doc["parsed"]["parse_sha256"])
    store.save_note(
        s["id"], "two", parse_sha256=doc["parsed"]["parse_sha256"], identity=note, revision=1
    )
    first = store.changes(limit=1)
    assert first["has_more"]
    assert first["events"][0]["interaction"]["source_channel"] == "product_ui"
    second = store.changes(first["next_cursor"])
    assert second["events"][0]["interaction"]["answer"] == "two"
    assert first["events"][0]["interaction"]["answer"] == "one"
    assert second["events"][0]["revision"] == 2
    assert not store.changes(second["next_cursor"])["events"]


def test_recovery_is_exported_without_retry(reader):
    store, doc, s = reader
    q = Question(document=doc["id"], mode="fulltext", question="what?")
    identity, _ = store.enqueue_session(q, s["id"], "recover-request")
    store.claim(identity)
    store.recover()
    assert store.job(identity)["state"] == "interrupted"
    assert store.changes()["events"][-1]["interaction"]["status"] == "interrupted"


def test_backup_restore_rotates_epoch_and_keeps_record_identity(reader, tmp_path):
    store, doc, s = reader
    store.save_note(s["id"], "retained", parse_sha256=doc["parsed"]["parse_sha256"])
    original = store.changes()
    backup(store.root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    restored = ReaderStore(tmp_path / "restored")
    assert restored.meta("instance_id") == store.meta("instance_id")
    assert restored.meta("export_epoch") != store.meta("export_epoch")
    assert (
        restored.changes()["events"][0]["interaction"]["record_uid"]
        == original["events"][0]["interaction"]["record_uid"]
    )
    assert restored.notes(s["id"])[0]["body"] == "retained"
    with pytest.raises(ValueError, match="cursor_expired"):
        restored.changes(original["next_cursor"])
    with pytest.raises(ValueError, match="restore_requires_new_directory"):
        restore(tmp_path / "backup", store.root)


def test_corrupt_backup_rejected_before_restore(reader, tmp_path):
    store, _, _ = reader
    dest = backup(store.root, tmp_path / "backup")
    (dest / "paper.db").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="backup_integrity_error"):
        restore(dest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_http_upload_read_and_protected_writes(tmp_path):
    app = create_app(tmp_path / "web")
    content = tiny_pdf(
        tmp_path / "upload.pdf", [[(50, 700, "A real original is retained after upload.")]]
    ).read_bytes()
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/documents", files={"file": ("paper.pdf", content, "application/pdf")}
            ).status_code
            == 403
        )
        headers = {"x-paper-token": app.state.store.meta("csrf")}
        assert (
            client.post(
                "/api/documents",
                headers={**headers, "origin": "https://external.invalid"},
                files={"file": ("paper.pdf", content)},
            ).status_code
            == 403
        )
        result = client.post(
            "/api/documents", headers=headers, files={"file": ("paper.pdf", content)}
        ).json()
        identity = result["document"]["id"]
        assert client.get("/api/documents/" + identity + "/pdf").content == content
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/aos/v1/describe").json()["product_id"] == "paper"
        assert "PaperReader" in client.get("/").text


def test_document_version_and_consent_checked_before_model(reader, monkeypatch):
    store, doc, s = reader
    app = create_app(store.root)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with TestClient(app) as client:
        headers = {"x-paper-token": store.meta("csrf")}
        q = {
            "session": s["id"],
            "request_key": "1234567890abcdef",
            "parse_sha256": "wrong",
            "question": {"document": doc["id"], "mode": "fulltext", "question": "why?"},
        }
        assert (
            client.post("/api/questions", headers=headers, json=q).json()["detail"]
            == "document_version_changed"
        )
        q["parse_sha256"] = doc["parsed"]["parse_sha256"]
        assert (
            client.post("/api/questions", headers=headers, json=q).json()["detail"]
            == "assistant_enable_required"
        )
        assert store.session_history(s["id"]) == []


def test_failed_pdf_original_available(tmp_path):
    app = create_app(tmp_path / "web")
    with TestClient(app) as client:
        data = b"%PDF-1.4\ninvalid body"
        result = client.post(
            "/api/documents",
            headers={"x-paper-token": app.state.store.meta("csrf")},
            files={"file": ("broken.pdf", data)},
        ).json()
        assert result["document"]["state"] == "failed"
        assert client.get("/api/documents/" + result["document"]["id"] + "/pdf").content == data


def test_fit_zoom_range_preserves_page_and_rejects_invalid(reader):
    store, _, session = reader
    for zoom in (.1, .35, 2.5, 3):
        store.position(session["id"], 1, zoom)
        assert store.session(session["id"])["zoom"] == zoom
    for zoom in (0, .09, 3.01, float("nan")):
        with pytest.raises(ValueError, match="invalid_reading_position"):
            store.position(session["id"], 1, zoom)
