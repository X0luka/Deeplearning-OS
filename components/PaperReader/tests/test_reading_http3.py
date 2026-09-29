import json
import time

import httpx
from conftest import publish_synthetic_content, tiny_pdf
from fastapi.testclient import TestClient

from paper_reader.reading_assistant import ReadingAssistant
from paper_reader.web import create_app


def test_real_http_worker_streams_without_consent_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test")
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        chunks = [
            {"choices": [{"delta": {"content": "这是面向初学者的解释。"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 50}},
        ]
        return httpx.Response(200, text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks))

    monkeypatch.setattr(
        "paper_reader.web.ReadingAssistant",
        lambda store, settings: ReadingAssistant(store, settings, httpx.MockTransport(handler)),
    )
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        headers = {"x-paper-token": app.state.store.meta("csrf")}
        pdf = tiny_pdf(tmp_path / "paper.pdf", [[(50, 700, "A small offline original document.")]])
        publish_synthetic_content(app.state.store, app.state.store.import_pdf(pdf))
        upload = client.post(
            "/api/documents", headers=headers, files={"file": ("paper.pdf", pdf.read_bytes())}
        ).json()
        doc, s = upload["document"], upload["session"]
        body = {
            "session": s["id"],
            "request_key": "new-reading-request",
            "parse_sha256": doc["parsed"]["parse_sha256"],
            "question": {"document": doc["id"], "mode": "fulltext", "question": "请通俗解释"},
        }
        result = client.post("/api/questions", headers=headers, json=body)
        assert result.status_code == 202
        identity = result.json()["id"]
        for _ in range(100):
            job = client.get("/api/questions/" + identity).json()
            if job["state"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert job["state"] == "completed" and "初学者" in job["answer"]["markdown"]
        assert len(calls) == 1
        assert client.post("/api/questions", headers=headers, json=body).json()["created"] is False
        assert (
            app.state.store.changes()["events"][-1]["interaction"]["answer"]
            == "这是面向初学者的解释。"
        )
        assert client.get("/api/sessions/" + s["id"]).json()["model"]["remaining"] == 19


def test_stream_cutoff_retains_partial_result_without_retry(tmp_path, monkeypatch):
    import threading

    import pytest

    from paper_reader.assistant_config import preferences
    from paper_reader.reader_store import ReaderStore
    from paper_reader.types import Question

    store = ReaderStore(tmp_path / "data")
    doc = store.import_pdf(tiny_pdf(tmp_path / "paper.pdf", [[(50, 700, "Offline paper.")]]))
    identity = store.enqueue(Question(document=doc["id"], mode="fulltext", question="Explain"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    attempts = []

    def handler(req):
        attempts.append(1)
        return httpx.Response(
            200,
            text="data: "
            + json.dumps(
                {"choices": [{"delta": {"content": "已收到的解释"}, "finish_reason": "length"}]}
            )
            + "\n\n",
        )

    updates = []
    with pytest.raises(ValueError, match="model_finish_length"):
        ReadingAssistant(store, preferences(store), httpx.MockTransport(handler)).run(
            identity, threading.Event(), lambda stage, text: updates.append(text)
        )
    assert updates[-1] == "已收到的解释" and len(attempts) == 1
