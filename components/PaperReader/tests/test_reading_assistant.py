import json
import threading

import httpx
import pytest
from conftest import tiny_pdf
from fastapi.testclient import TestClient

from paper_reader.assistant_config import ledger, preferences, save_preferences, status
from paper_reader.evidence import build_context
from paper_reader.reader_store import ReaderStore
from paper_reader.reading_assistant import ReadingAssistant, render_answer
from paper_reader.types import Question
from paper_reader.web import create_app


@pytest.fixture
def paper(tmp_path):
    store = ReaderStore(tmp_path / "reader")
    doc = store.import_pdf(
        tiny_pdf(
            tmp_path / "paper.pdf", [[(50, 700, "The learning rate increases during warmup.")]]
        )
    )
    session = store.create_session(doc["id"])
    q = Question(document=doc["id"], mode="fulltext", question="Explain this to a beginner")
    job, _ = store.enqueue_session(q, session["id"], "first-request")
    return store, doc, session, q, job


def sse(events):
    return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))


def test_deepseek_streamed_conversation_without_citations(paper, monkeypatch):
    store, doc, _, _, job = paper
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    seen = []

    def provider(req):
        body = json.loads(req.content)
        seen.append(body)
        assert "response_format" not in body and body["stream"]
        return sse(
            [
                {"choices": [{"delta": {"content": "可以先从直觉理解。"}, "finish_reason": None}]},
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 10},
                },
            ]
        )

    updates = []
    ReadingAssistant(store, preferences(store), httpx.MockTransport(provider)).run(
        job, threading.Event(), lambda stage, text: updates.append(text)
    )
    result = store.job(job)
    assert result["state"] == "completed" and result["answer"]["markdown"] == "可以先从直觉理解。"
    assert status(store, doc["id"])["remaining"] == 19 and len(seen) == 1
    assert result["context"]["coverage"]["all_extracted_text"]


def test_gemini_pdf_search_and_settings_reach_provider(paper, monkeypatch):
    store, _doc, _, _, job = paper
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    settings = save_preferences(
        store,
        {
            "model": "gemini-2.5-flash",
            "max_output_tokens": 4096,
            "thinking": "off",
            "document_input": "pdf",
            "web_search": True,
        },
    )

    def provider(req):
        body = json.loads(req.content)
        assert req.headers["x-goog-api-key"] == "test"
        assert body["generationConfig"]["maxOutputTokens"] == 4096
        assert body["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "application/pdf"
        assert body["tools"] == [{"google_search": {}}]
        return sse(
            [
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "外部背景解释"}]},
                            "finishReason": "STOP",
                            "groundingMetadata": {
                                "groundingChunks": [
                                    {"web": {"uri": "https://example.org/paper", "title": "Source"}}
                                ]
                            },
                        }
                    ],
                    "usageMetadata": {"totalTokenCount": 100},
                }
            ]
        )

    ReadingAssistant(store, settings, httpx.MockTransport(provider)).run(
        job, threading.Event(), lambda *_: None
    )
    result = store.job(job)
    assert result["answer"]["external_sources"][0]["title"] == "Source"
    assert result["context"]["coverage"]["input_mode"] == "pdf"


def test_tool_round_reads_original_and_uses_same_budget(paper, monkeypatch):
    store, doc, _, _, job = paper
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    calls = []
    block = doc["parsed"]["blocks"][0]["id"]

    def provider(req):
        body = json.loads(req.content)
        calls.append(body)
        if len(calls) == 1:
            return sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "tool1",
                                            "function": {
                                                "name": "read_paper",
                                                "arguments": '{"page":1}',
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    }
                ]
            )
        assert body["messages"][-1]["role"] == "tool"
        return sse(
            [
                {
                    "choices": [
                        {
                            "delta": {"content": f"预热期间上升 [[{block}:1-1]]"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ]
        )

    ReadingAssistant(store, preferences(store), httpx.MockTransport(provider)).run(
        job, threading.Event(), lambda *_: None
    )
    result = store.job(job)
    assert len(calls) == 2 and result["answer"]["anchors"][0]["page"] == 1
    assert result["context"]["tool_reads"][0]["page"] == 1
    assert status(store, doc["id"])["remaining"] == 18


def test_one_bad_reference_preserves_valid_reference_and_content(paper):
    store, doc, _, q, _ = paper
    block = doc["parsed"]["blocks"][0]["id"]
    result = render_answer(
        f"Useful explanation [[{block}:1-1]] wrong [[invented:1-1]]", build_context(store, q)
    )
    assert len(result["anchors"]) == 1 and result["limitations"]
    assert "Useful explanation" in result["markdown"] and "引用待核实" in result["markdown"]


def test_budget_migrates_old_identity_and_switch_does_not_reset(paper):
    import hashlib

    store, doc, _, _, _ = paper
    permit = {"model": "old", "authorization": "user_approved"}
    directory = store.root / "permits"
    directory.mkdir()
    (directory / (doc["id"] + ".json")).write_text(json.dumps(permit))
    old = hashlib.sha256(json.dumps(permit, sort_keys=True).encode()).hexdigest()
    with store.db() as db:
        db.execute("INSERT INTO budget VALUES(?,2,100,6000)", (old,))
    assert ledger(store, doc["id"]) == old
    save_preferences(store, {"model": "gemini-2.5-flash"})
    assert status(store, doc["id"])["remaining"] == 18
    assert status(store, doc["id"])["reserved_output_tokens"] == 6000


def test_note_visible_and_editable_from_second_session(paper):
    store, doc, s, _, _ = paper
    sha = doc["parsed"]["parse_sha256"]
    note = store.save_note(s["id"], "original", explanation="snapshot", parse_sha256=sha)
    second = store.create_session(doc["id"])
    assert store.notes(second["id"])[0]["id"] == note
    store.save_note(second["id"], "changed", identity=note, revision=1, parse_sha256=sha)
    assert store.notes(s["id"])[0]["body"] == "changed"
    assert store.notes(s["id"])[0]["explanation"] == "snapshot"


def test_invalid_capabilities_rejected_and_settings_persist(paper):
    store, *_ = paper
    with pytest.raises(ValueError, match="unsupported_web_search"):
        save_preferences(store, {"web_search": True})
    with pytest.raises(ValueError, match="unsupported_pdf_input"):
        save_preferences(store, {"document_input": "pdf"})
    with pytest.raises(ValueError, match="unsupported_thinking"):
        save_preferences(store, {"model": "gemini-2.5-pro", "thinking": "off"})
    save_preferences(store, {"max_output_tokens": 5000})
    assert preferences(ReaderStore(store.root))["max_output_tokens"] == 5000


def test_no_retry_after_provider_error_and_budget_reserved(paper, monkeypatch):
    store, doc, _, _, job = paper
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    requests = []

    def provider(req):
        requests.append(req)
        return httpx.Response(429)

    with pytest.raises(ValueError, match="provider_http_429"):
        ReadingAssistant(store, preferences(store), httpx.MockTransport(provider)).run(
            job, threading.Event(), lambda *_: None
        )
    assert len(requests) == 1 and status(store, doc["id"])["remaining"] == 19


def test_cancel_before_request_no_charge(paper, monkeypatch):
    store, doc, _, _, job = paper
    event = threading.Event()
    event.set()
    with pytest.raises(ValueError, match="user_cancelled"):
        ReadingAssistant(
            store,
            preferences(store),
            httpx.MockTransport(lambda req: pytest.fail("unexpected network")),
        ).run(job, event, lambda *_: None)
    assert status(store, doc["id"])["remaining"] == 20


def test_http_configuration_requires_csrf_and_no_consent_button(paper):
    store, *_ = paper
    with TestClient(create_app(store.root)) as client:
        assert client.put("/api/settings", json={}).status_code == 403
        assert len(client.get("/api/models").json()["items"]) == 3
        page = client.get("/").text
        assert "quick-note" in page and "model-select" in page and "同意上述" not in page
