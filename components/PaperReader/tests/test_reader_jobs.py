import json
import time
from typing import ClassVar

from conftest import publish_synthetic_content, tiny_pdf
from fastapi.testclient import TestClient

from paper_reader.model import Gateway
from paper_reader.web import create_app


class OfflineGateway(Gateway):
    calls: ClassVar[list] = []
    fail = False

    def complete(self, document, messages):
        data = json.loads(messages[1]["content"])
        self.calls.append(data)
        if self.fail:
            raise TimeoutError("synthetic timeout")
        return {
            "status": "answered",
            "claims": [
                {
                    "kind": "paper",
                    "text": "离线替身：只验证流程，不评价阅读质量。",
                    "citations": [
                        {"evidence_id": data["evidence"][0]["id"], "start_line": 1, "end_line": 1}
                    ],
                }
            ],
            "limitations": [],
        }, {"test_only": True}


def wait_job(client, identity):
    for _ in range(100):
        value = client.get("/api/questions/" + identity).json()
        if value["state"] in {"completed", "failed"}:
            return value
        time.sleep(0.01)
    raise AssertionError("asynchronous question did not finish")


def test_async_followup_and_note_snapshot_do_not_call_real_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-only")
    OfflineGateway.calls: ClassVar[list] = []
    OfflineGateway.fail = False
    app = create_app(tmp_path / "web", gateway_factory=OfflineGateway)
    with TestClient(app) as client:
        headers = {"x-paper-token": app.state.store.meta("csrf")}
        content = tiny_pdf(
            tmp_path / "doc.pdf",
            [[(50, 700, "The residual branch is added to the identity branch.")]],
        ).read_bytes()
        publish_synthetic_content(app.state.store, app.state.store.import_pdf(tmp_path / "doc.pdf"))
        upload = client.post(
            "/api/documents", headers=headers, files={"file": ("doc.pdf", content)}
        ).json()
        doc, session = upload["document"], upload["session"]
        assert (
            client.post(
                "/api/assistant/enable",
                headers=headers,
                json={"document": doc["id"], "accept": True},
            ).status_code
            == 200
        )
        body = {
            "session": session["id"],
            "request_key": "offline-question-1",
            "parse_sha256": doc["parsed"]["parse_sha256"],
            "question": {"document": doc["id"], "mode": "fulltext", "question": "第一问"},
        }
        first = client.post("/api/questions", headers=headers, json=body).json()["id"]
        result = wait_job(client, first)
        assert result["state"] == "completed" and result["answer"]["anchors"][0]["line_boxes"]
        replay = client.post("/api/questions", headers=headers, json=body).json()
        assert replay["id"] == first and not replay["created"]
        body["request_key"] = "offline-question-2"
        body["question"].update(question="继续解释它", previous=first)
        second = client.post("/api/questions", headers=headers, json=body).json()["id"]
        assert wait_job(client, second)["state"] == "completed"
        assert OfflineGateway.calls[-1]["previous"]["question"] == "第一问"
        assert len(OfflineGateway.calls) == 2
        assert app.state.store.changes()["events"][-1]["interaction"]["status"] == "completed"


def test_async_timeout_persists_without_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-only")
    OfflineGateway.calls: ClassVar[list] = []
    OfflineGateway.fail = True
    app = create_app(tmp_path / "web", gateway_factory=OfflineGateway)
    try:
        with TestClient(app) as client:
            headers = {"x-paper-token": app.state.store.meta("csrf")}
            content = tiny_pdf(
                tmp_path / "doc.pdf",
                [[(50, 700, "Only a synthetic fixture for timeout persistence.")]],
            ).read_bytes()
            publish_synthetic_content(app.state.store, app.state.store.import_pdf(tmp_path / "doc.pdf"))
            uploaded = client.post(
                "/api/documents", headers=headers, files={"file": ("doc.pdf", content)}
            ).json()
            doc, session = uploaded["document"], uploaded["session"]
            client.post(
                "/api/assistant/enable",
                headers=headers,
                json={"document": doc["id"], "accept": True},
            )
            body = {
                "session": session["id"],
                "request_key": "offline-timeout-1",
                "parse_sha256": doc["parsed"]["parse_sha256"],
                "question": {"document": doc["id"], "mode": "fulltext", "question": "超时测试"},
            }
            identity = client.post("/api/questions", headers=headers, json=body).json()["id"]
            assert wait_job(client, identity)["state"] == "failed"
            client.get("/api/sessions/" + session["id"])
            client.post("/api/questions", headers=headers, json=body)
            assert len(OfflineGateway.calls) == 1
            assert app.state.store.job(identity)["request"]["question"] == "超时测试"
    finally:
        OfflineGateway.fail = False
