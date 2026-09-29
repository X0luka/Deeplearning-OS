import asyncio
import json

import httpx
import pytest
from test_product import create_version, fixture_fetch, wait_job

from wiki_app import chat
from wiki_app.backup import backup, restore
from wiki_app.engine import Engine
from wiki_app.main import create_app
from wiki_app.store import Store


async def test_chat_retrieval_followup_isolation_restore_and_sse(tmp_path, monkeypatch):
    app = create_app(tmp_path / "live", lambda store: Engine(store, fetcher=fixture_fetch))
    store = app.state.store
    received = []

    async def complete(settings, messages, allow_tools):
        received.append(json.loads(messages[1]["content"]))
        if messages[-1]["role"] != "tool":
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "read1",
                        "type": "function",
                        "function": {
                            "name": "read_repository",
                            "arguments": json.dumps({"path": "src/store.py", "start": 1}),
                        },
                    }
                ],
            }
        proof = json.loads(messages[-1]["content"])["sources"][0]
        return {
            "content": "可以把它理解为记录入口。 [["
            + proof["id"]
            + "]]\n\n**设计建议**：未来可考虑批量处理，这不是现有实现。"
        }

    monkeypatch.setattr(chat, "completion", complete)
    monkeypatch.setattr(chat, "config", lambda _: {"enabled": True})
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"x-wiki-token": app.state.token},
        ) as c,
    ):
        _, version = await create_version(c)
        store.set_meta("model_config", json.dumps({"enabled": True}))
        store.record("other secret", "other answer", [], version, "other", "model")
        store.record("old version secret", "wrong version", [], "other-version", "thread", "model")
        for question in ("解释 startup", "你刚才的类比是什么意思？"):
            response = await c.post(
                f"/api/versions/{version}/chat",
                json={"question": question, "conversation": "thread", "page": "overview"},
            )
            assert response.status_code == 202, response.text
            job = await wait_job(c, response.json()["job_id"])
            assert job["state"] == "succeeded", job
        assert len(received[-1]["history"]) == 1
        assert received[-1]["history"][0]["question"] == "解释 startup"
        assert "secret" not in json.dumps(received[-1], ensure_ascii=False)
        assert received[-1]["current_view"]["page"]
        thread = (await c.get(f"/api/versions/{version}/conversations/thread")).json()
        assert len(thread["items"]) == 2
        assert "<strong>设计建议</strong>" in thread["items"][0]["html"]
        proof = thread["items"][0]["citations"][0]
        assert proof["source"] == "src/store.py" and proof["text_preview"].startswith("def save")
        assert "/versions/" + version + "/source?" in thread["items"][0]["answer"]
        stream = await c.get("/api/chat/jobs/" + job["id"] + "/events")
        assert stream.text.startswith("data: ") and stream.text.endswith("\n\n")
        assert json.loads(stream.text[6:])["state"] == "succeeded"
        assert "thread" in (await c.get(f"/api/versions/{version}/conversations")).text
        assert (
            await c.post(f"/api/versions/{version}/chat", json={"question": "x", "page": "invented"})
        ).status_code == 422
        assert (
            await c.post(f"/api/versions/{version}/chat", json={"question": "x", "entity_id": "fake"})
        ).status_code == 422
        changes = (await c.get("/aos/v1/changes")).json()
        assert any(e["interaction"]["conversation_id"] == "thread" for e in changes["events"])
    backup(store.root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    recovered = Store(tmp_path / "restored")
    assert chat.history(recovered, version, "thread") == chat.history(store, version, "thread")
    assert recovered.meta("epoch") != store.meta("epoch")


def test_source_access_and_citation_validation(tmp_path):
    store = Store(tmp_path)
    with store.db() as c:
        c.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", ("v", "a.py", 1, 2, "safe source"))
        c.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", ("other", "a.py", 1, 2, "other secret"))
    assert not chat.source_chunks(store, "v", path="../../etc/passwd")
    assert chat.source_chunks(store, "v", path="a.py")[0][-1] == "safe source"
    with pytest.raises(ValueError, match="invalid_model_evidence"):
        chat.validate("fake [[E999]]", [], "v")
    assert chat.validate("你好，我们可以先聊整体思路。", [], "v") == ("你好，我们可以先聊整体思路。", [])


async def test_failure_cancel_and_no_automatic_model_retry(tmp_path, monkeypatch):
    app = create_app(tmp_path, lambda store: Engine(store, fetcher=fixture_fetch))
    store = app.state.store
    calls = []

    async def bad(settings, messages, allow_tools):
        calls.append(1)
        return {"content": "捏造 [[E99999]]"}

    monkeypatch.setattr(chat, "completion", bad)
    monkeypatch.setattr(chat, "config", lambda _: {"enabled": True})
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"x-wiki-token": app.state.token},
        ) as c,
    ):
        _, version = await create_version(c)
        store.set_meta("model_config", json.dumps({"enabled": True}))
        payload = {"question": "hello", "conversation": "fail"}
        j = (await c.post(f"/api/versions/{version}/chat", json=payload)).json()["job_id"]
        result = await wait_job(c, j)
        assert result["state"] == "failed" and result["error"] == "invalid_model_evidence"
        assert len(calls) == 1 and not chat.history(store, version, "fail")

        async def slow(*_):
            await asyncio.sleep(30)

        monkeypatch.setattr(chat, "completion", slow)
        j = (await c.post(f"/api/versions/{version}/chat", json=payload)).json()["job_id"]
        await c.post("/api/jobs/" + j + "/cancel")
        assert (await wait_job(c, j))["state"] == "cancelled"
