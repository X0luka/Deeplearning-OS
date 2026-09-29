import asyncio

import httpx
from conftest import tiny_pdf
from fastapi import FastAPI
from test_unified_content import install_content

from paper_reader.assistant_tools import install
from paper_reader.reader_store import ReaderStore


def test_tools_use_canonical_ids_and_require_auth(tmp_path, monkeypatch):
    asyncio.run(check_tools(tmp_path, monkeypatch))


async def check_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("AOS_LLM_TOKEN", "fixture-token")
    store = ReaderStore(tmp_path / "reader")
    doc = store.import_pdf(tiny_pdf(tmp_path / "x.pdf", [[(50, 700, "obsolete extraction")]]))
    _content, _, _ = install_content(store, doc)
    store.create_session(doc["id"])
    app = FastAPI()
    install(app, store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as c:
        assert (await c.get("/aos/assistant/v1/resources")).status_code == 403
        headers = {"Authorization": "Bearer fixture-token"}
        response = await c.get(
            "/aos/assistant/v1/search",
            headers=headers,
            params={"resource_id": doc["id"], "query": "equation"},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert items and "$x^2$" in items[0]["text"]
        assert items[0]["location"]["target"]["revision"] == "a" * 64
        wrong = await c.get(
            "/aos/assistant/v1/search",
            headers=headers,
            params={"resource_id": doc["id"], "version": "different"},
        )
        assert wrong.status_code == 409
        chinese = await c.get(
            "/aos/assistant/v1/search",
            headers=headers,
            params={"resource_id": doc["id"], "query": "解释这篇论文"},
        )
        assert chinese.json()["items"]
        full = await c.get("/aos/assistant/v1/search", headers=headers,
                           params={"resource_id": doc["id"], "full_text": True})
        assert len(full.json()["items"]) == len(_content["blocks"])
        assert full.json()["next"] is None
        beyond = await c.get("/aos/assistant/v1/search", headers=headers,
                             params={"resource_id": doc["id"], "full_text": True, "offset": 1000})
        assert beyond.json()["items"] == []

        exported = (await c.get("/aos/assistant/v1/history", headers=headers)).json()
        assert len(exported["items"]) == 1 and exported["instance"]


def test_ranking_does_not_reward_repeated_table_labels():
    from paper_reader.paper_content import search
    content = {"blocks": [
        {"id": "table", "text": ("Agent result accuracy benchmark score " * 100)},
        {"id": "method", "text": "The agent executes actions inside an isolated sandbox container."},
        {"id": "unrelated", "text": "The dataset includes arithmetic questions."},
    ]}
    assert search(content, "agent sandbox actions", 1) == ["method"]


def test_notes_are_versioned_and_deleted_documents_are_excluded(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setenv("AOS_LLM_TOKEN", "note-test")
        store = ReaderStore(tmp_path / "reader")
        doc = store.import_pdf(tiny_pdf(tmp_path / "note.pdf", [[(50, 700, "fixture")]]))
        session = store.create_session(doc["id"])
        note = store.save_note(session["id"], "A user interpretation", explanation="An excerpt", parse_sha256=store.document(doc["id"])["parsed"]["parse_sha256"])
        app = FastAPI()
        install(app, store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local",
                                    headers={"Authorization": "Bearer note-test"}) as c:
            first = (await c.get("/aos/assistant/v1/resources", params={"limit": 1})).json()
            assert first["next"] == 1
            page = (await c.get("/aos/assistant/v1/resources", params={"offset": 1})).json()
            resource = page["items"][0]
            assert resource["id"] == "note:" + note and resource["kind"] == "note"
            params = {"resource_id": resource["id"], "version": resource["version"]}
            read = await c.get("/aos/assistant/v1/search", params=params)
            assert read.status_code == 200 and read.json()["items"][0]["quality"] == "user_note"
            assert "A user interpretation" in read.json()["items"][0]["text"]
            store.save_note(session["id"], "Changed", identity=note, revision=1, parse_sha256=store.document(doc["id"])["parsed"]["parse_sha256"])
            assert (await c.get("/aos/assistant/v1/search", params=params)).status_code == 409
            with store.db() as db:
                db.execute("INSERT INTO library_state VALUES(?,0,'deleted')", (doc["id"],))
            assert (await c.get("/aos/assistant/v1/resources")).json()["items"] == []
            assert (await c.get("/aos/assistant/v1/search", params=params)).status_code == 404
    asyncio.run(run())


def test_note_source_envelope_preserves_user_body_and_excerpt_separately():
    import json

    from paper_reader.assistant_tools import note_content
    source = {"kind": "html", "text": "An excerpt", "view": "zh", "start_unit": "p1",
              "end_unit": "p1", "start_offset": 0, "end_offset": 10}
    raw = {"body": "My interpretation", "selection": "null", "explanation":
           "reader-note-source/v1\n" + json.dumps({"source": source, "explanation": ""})}
    result = note_content(raw)
    assert result["用户填写的笔记正文"] == "My interpretation"
    assert result["原文摘录（不是用户笔记正文）"] == source
    assert result["保存的解释（不是用户笔记正文）"] == ""
    raw["explanation"] = "reader-note-source/v1\n{broken"
    assert note_content(raw)["保存的解释（不是用户笔记正文）"] == raw["explanation"]


def test_preview_ui_requires_internal_token(monkeypatch):
    from starlette.requests import Request

    from paper_reader.assistant_tools import ui_host

    monkeypatch.delenv("AOS_UNIFIED_ASSISTANT", raising=False)
    monkeypatch.setenv("AOS_ASSISTANT_PREVIEW_HOST", "http://127.0.0.1:8777")
    monkeypatch.setenv("AOS_LLM_TOKEN", "internal-preview")
    def request(token):
        return Request({"type": "http", "headers": [(b"x-aos-preview-token", token.encode())]})
    assert ui_host(request("")) == ""
    assert ui_host(request("wrong")) == ""
    assert ui_host(request("internal-preview")) == "http://127.0.0.1:8777"
