import json

import pytest

from paper_reader.model import Gateway


def permit(tmp_path, document):
    path = tmp_path / "permit.json"
    path.write_text(
        json.dumps(
            {
                "authorization": "user_approved",
                "base_url": "https://invalid.example",
                "model": "test",
                "key_env": "PAPER_TEST_KEY",
                "documents": [document],
                "max_calls": 1,
                "max_input_bytes": 10000,
                "max_output_tokens": 1000,
                "output_per_call": 500,
            }
        )
    )
    return path


def test_no_key_no_charge(imported, tmp_path, monkeypatch):
    store, doc, _ = imported
    monkeypatch.delenv("PAPER_TEST_KEY", raising=False)
    gateway = Gateway(store, permit(tmp_path, doc["id"]))
    with pytest.raises(ValueError, match="model_key_not_configured"):
        gateway.complete(doc["id"], [])
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM budget").fetchone()[0] == 0


def test_timeout_consumes_reservation_and_blocks_retry(imported, tmp_path, monkeypatch):
    store, doc, _ = imported
    monkeypatch.setenv("PAPER_TEST_KEY", "synthetic-test-key")
    gateway = Gateway(store, permit(tmp_path, doc["id"]))

    def fail(*args, **kwargs):
        raise TimeoutError("uncertain provider outcome")

    monkeypatch.setattr("httpx.Client.post", fail)
    with pytest.raises(TimeoutError):
        gateway.complete(doc["id"], [])
    with pytest.raises(ValueError, match="model_budget_exhausted"):
        gateway.complete(doc["id"], [])


def test_unauthorized_document_never_sent(imported, tmp_path):
    store, doc, _ = imported
    gateway = Gateway(store, permit(tmp_path, doc["id"]))
    with pytest.raises(ValueError, match="document_not_authorized"):
        gateway.complete("0" * 64, [])


def test_unapproved_permit_rejected(imported, tmp_path):
    store, doc, _ = imported
    path = permit(tmp_path, doc["id"])
    body = json.loads(path.read_text())
    body["authorization"] = "pending"
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="explicit_model_authorization_required"):
        Gateway(store, path)
