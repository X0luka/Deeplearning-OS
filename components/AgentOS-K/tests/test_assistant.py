import asyncio
import json

import pytest

from aos.assistant.service import Service
from aos.assistant.store import Store


class Models:
    def resolve(self, *args):
        return {
            "model": "offline",
            "quality_review": False,
            "protocol": "openai",
            "max_output_tokens": 200,
            "tools": True,
            "document_input": "text",
            "capabilities": {"tools": True},
            "limits": {"context_window": 64000},
            "connection": "offline",
        }


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "assistant.db")


def test_idempotency_and_history_preserved(store):
    c = store.create("论文", "paper", [])
    a, fresh = store.enqueue(c["id"], "same-key", "q", {})
    assert fresh
    assert store.enqueue(c["id"], "same-key", "q", {}) == (a, False)
    with pytest.raises(ValueError, match="request_key_reused"):
        store.enqueue(c["id"], "same-key", "different", {})
    store.update(a, state="completed", answer="answer")
    store.import_turn(c["id"], {"question": "old", "answer": "old answer"}, "legacy-id")
    store.import_turn(c["id"], {"question": "old", "answer": "old answer"}, "legacy-id")
    assert len(store.turns(c["id"])) == 2
    restored = Store(store.path)
    assert restored.turn(a)["answer"] == "answer"


def test_interruption_never_reexecutes(store):
    c = store.create("a", "general", [])
    t, _ = store.enqueue(c["id"], "key-12345", "q", {})
    recovered = Store(store.path)
    assert recovered.turn(t)["state"] == "interrupted"


def test_cross_material_evidence_and_out_of_scope(store):
    async def run():
        c = store.create("comparison", "general", [])
        resources = [{"provider": "p", "id": "paper"}, {"provider": "w", "id": "repo"}]
        t, _ = store.enqueue(
            c["id"],
            "cross-key",
            "compare",
            {"resources": resources, "settings": {}, "model_scope": "assistant"},
        )
        calls = []

        async def model(settings, messages, tools, emit):
            calls.append(messages)
            current = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
            assert current["evidence"][0]["coverage"] == "selected_excerpts_only"
            assert current["evidence"][0]["absence_proven"] is False
            if len(calls) == 1:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "a",
                            "function": {
                                "name": "read_material",
                                "arguments": json.dumps(
                                    {"provider": "secret", "resource": "outside"}
                                ),
                            },
                        }
                    ],
                }, {}
            assert any(m["role"] == "tool" and "resource_outside_scope" in m["content"] for m in messages)
            assert "没有可执行工具" in messages[-1]["content"]
            await emit("paper [[E1]], code [[E2]]")
            return {"content": "paper [E1], code 〔E2〕, invented [E99]"}, {"total_tokens": 20}

        service = Service(store, Models(), {}, None, model)

        async def read(resource, *args):
            return [
                {
                    "resource": resource["id"],
                    "block": "b1",
                    "version": "v1",
                    "text": "fact",
                    "title": "source",
                    "location": {},
                    "url": "/source",
                }
            ]

        service.read = read
        await service.run(t)
        result = store.turn(t)
        assert result["state"] == "completed"
        assert len(result["evidence"]) == 2
        assert "来源未核实" in result["answer"]
        assert "[[E1]]" in result["answer"] and "[[E2]]" in result["answer"]
        assert result["snapshot"]["resources"] == resources

    asyncio.run(run())


def test_cancellation_preserves_partial(store):
    async def run():
        c = store.create("c", "general", [])
        t, _ = store.enqueue(
            c["id"],
            "cancel-key",
            "q",
            {"resources": [], "settings": {}, "model_scope": "assistant"},
        )
        ready = asyncio.Event()

        async def model(s, m, tools, emit):
            await emit("partial")
            ready.set()
            await asyncio.sleep(60)

        service = Service(store, Models(), {}, None, model)
        service.start(t)
        await ready.wait()
        await service.close()
        assert store.turn(t)["state"] == "cancelled"
        assert store.turn(t)["answer"] == "partial"

    asyncio.run(run())


@pytest.mark.asyncio
async def test_http_auth_scope_and_idempotency(tmp_path):
    import httpx

    from aos.adapters.sqlite import SQLiteArchive
    from aos.host.web import create_app
    from aos.llm.catalog import model_item
    from aos.llm.store import ModelStore

    models = ModelStore(tmp_path / "llm")
    models.save_connection(
        {
            "name": "offline",
            "protocol": "chat",
            "base_url": "https://offline.invalid/v1",
            "key": "fake-test-key",
        }
    )
    connection = models.read()["connections"][0]["id"]
    models.mutate(lambda s: s["connections"][0]["models"].append({**model_item("test"), "limits": {"context_tokens": 64000, "max_output_tokens": 8192}}))
    models.settings("global", {"selection": {"connection": connection, "model": "test"}})
    app = create_app(None, None, SQLiteArchive(tmp_path / "archive.db"), model_store=models)

    async def offline(s, m, t, emit):
        return {"content": "offline"}, {}

    app.state.assistant.complete = offline
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/api/v1/assistant/conversations")).status_code == 403
        version = await client.get("/assistant/version")
        assert version.status_code == 200 and len(version.json()["revision"]) == 20
        assert version.headers["cache-control"] == "no-store"
        page = await client.get("/assistant")
        assert version.json()["revision"] in page.text
        assert page.headers["cache-control"] == "no-store"
        script = await client.get("/static/assistant.js")
        assert script.headers["cache-control"] == "no-cache"
        headers = {"X-AOS-Token": app.state.write_token}
        assert (
            await client.get(
                "/api/v1/assistant/conversations",
                headers={**headers, "Origin": "https://outside.invalid"},
            )
        ).status_code == 403
        assert (await client.get("/assistant?parent=https://outside.invalid")).status_code == 403
        c = (
            await client.post(
                "/api/v1/assistant/conversations", headers=headers, json={"resources": []}
            )
        ).json()
        bad = await client.patch(
            "/api/v1/assistant/conversations/" + c["id"],
            headers=headers,
            json={"resources": [{"provider": "not-registered", "id": "unknown"}]},
        )
        assert bad.status_code == 409
        url = "/api/v1/assistant/conversations/" + c["id"] + "/turns"
        body = {"question": "hello", "request_key": "unique-key"}
        a = await client.post(url, headers=headers, json=body)
        b = await client.post(url, headers=headers, json=body)
        assert a.status_code == 202 and a.json()["id"] == b.json()["id"]
        await asyncio.gather(*list(app.state.assistant.tasks.values()))
        result = (
            await client.get("/api/v1/assistant/turns/" + a.json()["id"], headers=headers)
        ).json()
        assert result["state"] == "completed"
        assert "fake-test-key" not in json.dumps(result)
        assert result["snapshot"]["settings"]["max_output_tokens"] == 3000

        # New turns freeze server-owned catalog permissions; client cannot add resources.
        paper = {"provider": "p", "id": "paper", "title": "Paper", "section": "paper", "version": "p1"}
        repo = {"provider": "w", "id": "repo", "title": "Repo", "section": "wiki", "version": "sha1"}
        async def catalog():
            return {"items": [paper, repo], "errors": []}
        app.state.assistant.catalog = catalog
        app.state.assistant.start = lambda turn: None
        c2 = (await client.post("/api/v1/assistant/conversations", headers=headers,
                               json={"resources": [{"provider": "p", "id": "paper"}]})).json()
        url2 = "/api/v1/assistant/conversations/" + c2["id"]
        submitted = (await client.post(url2 + "/turns", headers=headers, json={
            "question": "query repo", "request_key": "cross-http-1",
            "accessible_resources": [{"provider": "private", "id": "secret"}]})).json()
        assert submitted["snapshot"]["resources"] == [paper]
        assert submitted["snapshot"]["accessible_resources"] == [paper, repo]
        repo["version"] = "sha2"
        assert app.state.assistant.store.turn(submitted["id"])["snapshot"]["accessible_resources"][1]["version"] == "sha1"
        app.state.assistant.store.update(submitted["id"], state="cancelled")
        assert (await client.patch(url2, headers=headers, json={"scope": "selected"})).status_code == 200
        restricted = (await client.post(url2 + "/turns", headers=headers, json={
            "question": "query repo", "request_key": "cross-http-2"})).json()
        assert restricted["snapshot"]["accessible_resources"] == [paper]


@pytest.mark.asyncio
async def test_stream_protocol_keeps_content_and_usage():
    import httpx

    from aos.assistant.transport import stream

    events = [
        {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"total_tokens": 12}},
    ]
    payload = "".join("data: " + json.dumps(v) + "\n\n" for v in events) + "data: [DONE]\n\n"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    emitted = []

    async def emit(text):
        emitted.append(text)

    settings = {
        "protocol": "chat",
        "base_url": "https://offline.invalid/v1",
        "model": "test",
        "api_key": "fake",
        "max_output_tokens": 100,
    }
    message, usage = await stream(
        settings, [{"role": "user", "content": "hi"}], [], emit, transport
    )
    assert message["content"] == "hello" and emitted == ["hello"] and usage["total_tokens"] == 12


def test_full_text_pagination_persists_without_total_size_quota(store):
    async def run():
        service = Service(store, Models(), {}, None)
        offsets = []
        async def remote(provider, operation, params):
            offsets.append(params["offset"])
            return {"items": [{"block": str(params["offset"]), "text": "body"}],
                    "next": 64 if params["offset"] == 0 else None}
        service.remote = remote
        resource = {"provider": "fixture", "id": "doc", "version": "v1", "context_mode": "full_text"}
        pages = await service.read(resource)
        assert len(pages) == 2
        assert pages.path.exists()
        assert [r["text"] for r in pages] == ["body", "body"]
        pages.close()
        assert offsets == [0, 64]
        async def large(provider, operation, params):
            offset = params["offset"]
            return {"items": [{"text": "x" * 1_000_000}], "next": offset + 1 if offset < 12 else None}
        service.remote = large
        pages = await service.read(resource)
        assert len(pages) == 13
        assert sum(len(r["text"]) for r in pages) == 13_000_000
        pages.close()
    asyncio.run(run())


def test_whole_document_supplied_for_chinese_summary(store):
    async def run():
        resource = {"provider": "fixture", "id": "doc", "context_mode": "full_text"}
        c = store.create("summary", "paper", [resource])
        t, _ = store.enqueue(c["id"], "whole-summary", "请总结这篇文章", {
            "resources": [resource], "model_scope": "assistant", "settings": {}})
        async def model(settings, messages, tools, emit):
            payload = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
            assert payload["evidence"][0]["coverage"] == "full_extracted_text"
            assert payload["evidence"][0]["evidence"][-1]["text"] == "conclusion"
            return {"content": "summary [[E2]]"}, {}
        service = Service(store, Models(), {}, None, model)
        async def read(r, query="", block=""):
            assert query == "" and block == ""
            return [{"resource": "doc", "block": str(i), "version": "v1", "text": text}
                    for i, text in enumerate(["abstract", "conclusion"])]
        service.read = read
        await service.run(t)
        assert store.turn(t)["state"] == "completed"
    asyncio.run(run())


def test_partition_processes_every_block_and_rejects_invented_ids():
    from aos.assistant.context import fit_materials
    async def run():
        refs = [{"id": "E" + str(i), "text": "正文" * 1200} for i in range(6)]
        initial = [{"resource": {"id": "doc"}, "evidence": refs}]
        settings = {"max_output_tokens": 1024, "limits": {"context_tokens": 20000}}
        seen, usage = set(), []
        async def complete(settings, messages, tools, emit):
            group = json.loads(messages[-1]["content"])["evidence"]
            seen.update(r["id"] for r in group)
            return {"content": " ".join("fact [[" + r["id"] + "]]" for r in group)}, {"total_tokens": 10}
        result = await fit_materials(initial, "总结", settings, complete, usage, lambda: None)
        assert seen == {r["id"] for r in refs}
        assert result[0]["coverage"] == "all_supplied_parts_processed"
        assert len(usage) > 1
        async def bad(*args):
            return {"content": "made up [[E999]]"}, {}
        with pytest.raises(ValueError, match="partition_evidence_invalid"):
            await fit_materials(initial, "总结", settings, bad, [], lambda: None)
    asyncio.run(run())


def test_more_than_two_tool_rounds_and_no_progress_stop(store):
    async def run():
        resource = {"provider": "fixture", "id": "doc"}
        c = store.create("research", "general", [resource])
        t, _ = store.enqueue(c["id"], "multi-round", "research", {
            "resources": [resource], "settings": {}, "model_scope": "assistant"})
        rounds = []
        async def complete(settings, messages, tools, emit):
            rounds.append(1)
            if not tools:
                return {"content": "answer [[E1]]"}, {}
            query = str(min(len(rounds), 3))
            return {"content": "", "tool_calls": [{"id": query, "function": {
                "name": "read_material", "arguments": json.dumps({
                    "provider": "fixture", "resource": "doc", "query": query})}}]}, {}
        service = Service(store, Models(), {}, None, complete)
        reads = []
        async def read(resource, query="", block=""):
            reads.append(query)
            return [{"resource": "doc", "version": "v1", "block": query, "text": query}]
        service.read = read
        await service.run(t)
        assert store.turn(t)["state"] == "completed"
        assert len(rounds) == 5
        assert reads == ["research", "1", "2", "3"]
    asyncio.run(run())


@pytest.mark.parametrize("with_material", [False, True])
@pytest.mark.parametrize("legacy_review", [False, True])
def test_first_answer_is_final_without_review(store, with_material, legacy_review):
    class FirstAnswerModels(Models):
        def resolve(self, *args):
            settings = super().resolve(*args)
            settings["quality_review"] = legacy_review
            return settings

    async def run():
        resources = [{"provider": "p", "id": "allowed"}] if with_material else []
        c = store.create("first answer", "general", resources)
        t, _ = store.enqueue(c["id"], "first", "question", {
            "resources": resources, "settings": {}, "model_scope": "assistant"})
        calls = []

        async def model(settings, messages, tools, emit):
            calls.append(messages)
            assert len(calls) == 1, "An extra review must never replace the first answer"
            answer = "first answer [[E1]] [[E99]]" if with_material else "first answer"
            await emit(answer)
            return {"content": answer}, {"total_tokens": 1}

        service = Service(store, FirstAnswerModels(), {}, None, model)

        async def read(resource, *args):
            return [{"resource": "allowed", "block": "b1", "version": "v1",
                     "text": "authorized fact", "title": "source", "location": {}, "url": "/s"}]
        service.read = read
        await service.run(t)
        result = store.turn(t)
        assert result["state"] == "completed"
        assert result["answer"] == ("first answer [[E1]] 〔来源未核实〕" if with_material else "first answer")
        assert len(result["usage"]["calls"]) == 1
        assert result["usage"]["source_review"] is False
    asyncio.run(run())


def test_tool_lookup_keeps_first_answer_without_extra_call(store):
    async def run():
        resource = {"provider": "p", "id": "allowed"}
        c = store.create("tool answer", "general", [resource])
        t, _ = store.enqueue(c["id"], "tool", "question", {
            "resources": [resource], "settings": {}, "model_scope": "assistant"})
        calls = []

        async def model(settings, messages, tools, emit):
            calls.append(messages)
            if len(calls) == 1:
                return {"content": "", "tool_calls": [{"id": "read-2", "function": {
                    "name": "read_material", "arguments": json.dumps({
                        "provider": "p", "resource": "allowed", "block": "b2"})}}]}, {}
            assert len(calls) == 2
            assert "supplemental original fact" in str(messages)
            await emit("first answer [[E2]]")
            return {"content": "first answer [[E2]]"}, {}

        service = Service(store, Models(), {}, None, model)
        async def read(resource, query="", block=""):
            return [{"resource": "allowed", "block": block or "b1", "version": "v1",
                     "text": "supplemental original fact" if block else "initial fact",
                     "title": "source", "location": {}, "url": "/s"}]
        service.read = read
        await service.run(t)
        result = store.turn(t)
        assert result["state"] == "completed"
        assert result["answer"] == "first answer [[E2]]"
        assert len(calls) == 2
        assert result["usage"]["source_review"] is False
    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize("output_limit, fails", [(1024, True), (100000, False)])
async def test_stream_wire_budget_tracks_output_limit(output_limit, fails):
    import httpx

    from aos.assistant.transport import stream

    chunk = json.dumps({"metadata": "x" * 1000, "choices": [{"delta": {"reasoning_content": "x"}}]})
    body = ("data: " + chunk + "\n\n") * 2000
    body += 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    settings = {"protocol": "openai", "base_url": "https://example.test/v1", "model": "offline",
                "api_key": "test", "max_output_tokens": output_limit}

    async def emit(text):
        pass

    if fails:
        with pytest.raises(ValueError, match="model_response_too_large"):
            await stream(settings, [], [], emit, transport=transport)
    else:
        message, _ = await stream(settings, [], [], emit, transport=transport)
        assert message["content"] == "ok"


def test_more_than_32_tools_are_processed_and_originals_persist(store):
    async def run():
        resource = {"provider": "p", "id": "doc"}
        c = store.create("batch", "paper", [resource])
        t, _ = store.enqueue(c["id"], "batch-tools", "q", {
            "resources": [resource], "settings": {}, "model_scope": "assistant"})
        reads, count = [], 0
        async def model(settings, messages, tools, emit):
            nonlocal count
            count += 1
            if count == 1:
                return {"content": "", "tool_calls": [{"id": str(i), "function": {
                    "name": "read_material", "arguments": json.dumps({
                        "provider": "p", "resource": "doc", "block": str(i)})}} for i in range(40)]}, {}
            assert len([m for m in messages if m["role"] == "tool"]) == 40
            return {"content": "answer [[E41]]"}, {}
        service = Service(store, Models(), {}, None, model)
        async def read(resource, query="", block=""):
            reads.append(block)
            return [{"resource": "doc", "block": block, "version": "v1", "text": "source " * 200}]
        service.read = read
        await service.run(t)
        v = store.turn(t)
        assert v["state"] == "completed"
        assert len(reads) == 41
        assert len(v["evidence"][0]["text"]) == 1000
        raw = store.rows("SELECT payload FROM turn_evidence WHERE turn=? AND id='E1'", (t,))[0]
        assert json.loads(raw["payload"])["text"] == "source " * 200
    asyncio.run(run())


def test_token_accounting_is_not_utf8_bytes():
    from aos.assistant.context import byte_size, input_budget, token_count
    text = "仓库架构与源码说明。" * 1000
    assert token_count(text, {"model": "offline"}) < byte_size(text)
    with pytest.raises(ValueError, match="model_context_limit_unknown"):
        input_budget({"max_output_tokens": 1000, "limits": {}})


def test_context_compaction_covers_history_and_rejects_invention():
    from aos.assistant.context import fit_messages, input_budget, token_count
    async def run():
        settings = {"max_output_tokens": 1024, "limits": {"context_tokens": 14000}}
        messages = [{"role": "system", "content": "trusted rules"}] + [
            {"role": "user", "content": "history-marker-" + str(i) + " 正文" * 1400 + " [[E1]]"}
            for i in range(20)
        ]
        seen, usage = [], []
        async def complete(settings, batch, tools, emit):
            payload = json.loads(batch[-1]["content"])
            assert payload["current_question"] == "preserve this exact question"
            seen.extend(payload["records"])
            return {"content": "已读记录与问题" + (" [[E1]]" if "[[E1]]" in batch[-1]["content"] else "")}, {}
        result = await fit_messages(messages, settings, complete, usage, lambda: None,
            question="preserve this exact question", execution_context={"allowed_resources": [{"id": "fixed-version"}]})
        carried = json.loads(result[-1]["content"])
        assert carried["current_question"] == "preserve this exact question"
        assert carried["execution_context"]["allowed_resources"] == [{"id": "fixed-version"}]
        assert token_count(result, settings) < input_budget(settings)
        assert result[0] == messages[0]
        assert all("history-marker-" + str(i) in "".join(seen) for i in range(20))
        assert usage and all(u["stage"] == "context_compaction" for u in usage)
        async def bad(*args):
            return {"content": "invented [[E999]]"}, {}
        with pytest.raises(ValueError, match="context_compaction_invalid"):
            await fit_messages(messages, settings, bad, [], lambda: None)
        async def unchanged(settings, batch, tools, emit):
            return {"content": json.dumps(json.loads(batch[-1]["content"])["records"], ensure_ascii=False) * 2}, {}
        with pytest.raises(ValueError, match="context_compaction_no_progress"):
            await fit_messages(messages, settings, unchanged, [], lambda: None)
    asyncio.run(run())


def test_long_history_preparation_has_linear_tokenizer_work(monkeypatch):
    from aos.assistant import context
    work = 0
    def count(value, settings):
        nonlocal work
        size = len(json.dumps(value, ensure_ascii=False))
        work += size
        return (size + 3) // 4
    monkeypatch.setattr(context, "token_count", count)
    messages = [{"role": "user", "content": str(i) + ":" + "x" * 1000} for i in range(400)]
    settings = {"max_output_tokens": 2048, "limits": {"context_tokens": 64000}}
    async def run():
        async def complete(settings, prompt, tools, emit):
            assert count(prompt, settings) <= context.input_budget(settings)
            return {"content": "source records and unresolved question retained"}, {}
        result = await context.fit_messages(messages, settings, complete, [], lambda: None)
        assert result and count(result, settings) < context.input_budget(settings)
    asyncio.run(run())
    assert work < 12 * len(json.dumps(messages))


def test_cancel_during_long_history_preparation_before_provider(monkeypatch):
    from aos.assistant import context
    monkeypatch.setattr(context, "token_count", lambda value, settings: len(json.dumps(value)) // 4)
    async def run():
        async def complete(*args):
            raise AssertionError("Provider must not be called after preprocessing cancellation")
        messages = [{"role": "user", "content": "x" * 1000} for _ in range(400)]
        settings = {"max_output_tokens": 2048, "limits": {"context_tokens": 64000}}
        task = asyncio.create_task(context.fit_messages(messages, settings, complete, [], lambda: None))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())


@pytest.mark.parametrize("restricted", [False, True])
def test_discover_other_module_then_read_without_rebinding(store, restricted):
    async def run():
        paper = {"provider": "p", "id": "paper", "title": "Article", "version": "v1"}
        repo = {"provider": "w", "id": "repo", "title": "Example repository", "version": "sha", "file_listing": True}
        accessible = [paper] if restricted else [paper, repo]
        c = store.create("article", "paper", [paper])
        identity, _ = store.enqueue(c["id"], "discovery-key", "explain repository", {
            "resources": [paper], "accessible_resources": accessible,
            "settings": {}, "model_scope": "assistant", "scope": "selected" if restricted else "current"})
        reads, rounds = [], []
        async def read(resource, query="", block=""):
            reads.append((resource["id"], block))
            return [{"resource": resource["id"], "version": resource["version"], "block": block or "body",
                     "text": "source fact", "title": "source", "location": {}, "url": "/source"}]
        async def model(settings, messages, tools, emit):
            rounds.append(1)
            n = len(rounds)
            if n == 1:
                assert reads == [("paper", "")]
                name, args = "search_resources", {"query": "Example"}
            elif n == 2:
                result = json.loads(messages[-1]["content"])
                assert [r["id"] for r in result["items"]] == ([] if restricted else ["repo"])
                # Discovery is progress, so source reading remains enabled.
                assert any(t["function"]["name"] == "read_material" for t in tools)
                name, args = "read_material", {"provider": "w", "resource": "repo", "block": "src/main.py:1"}
            else:
                if restricted:
                    assert "resource_outside_scope" in messages[-2]["content"] or any("resource_outside_scope" in m.get("content", "") for m in messages)
                    assert reads == [("paper", "")]
                else:
                    assert reads[-1] == ("repo", "src/main.py:1")
                return {"content": "answer [[E1]]"}, {}
            return {"tool_calls": [{"id": str(n), "function": {"name": name, "arguments": json.dumps(args)}}]}, {}
        service = Service(store, Models(), {}, None, model)
        service.read = read
        await service.run(identity)
        result = store.turn(identity)
        assert result["state"] == "completed", result.get("error")
        assert result["snapshot"]["resources"] == [paper]
        assert store.conversation(c["id"])["resources"] == [paper]
        assert len(result["evidence"]) == (1 if restricted else 2)
    asyncio.run(run())


def test_resource_discovery_pagination():
    from aos.assistant.service import search_resources
    rows = [{"provider": "w", "id": str(i), "title": "Project", "version": str(i)} for i in range(45)]
    first = search_resources(rows, {"query": "project"})
    second = search_resources(rows, {"offset": first["next"]})
    assert first["next"] == 20 and second["next"] == 40
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    assert search_resources(rows, {"provider": "private"})["items"] == []
    assert search_resources(rows, {"offset": -1})["error"] == "invalid_offset"


def test_live_archive_is_read_only_and_preserves_source_links(tmp_path):
    import sqlite3

    from aos.adapters.sqlite import ReadOnlySQLiteArchive, SQLiteArchive
    original = SQLiteArchive(tmp_path / "archive.db")
    readonly = ReadOnlySQLiteArchive(original.path, "http://127.0.0.1:7777")
    assert readonly.data_page()["items"] == []
    with pytest.raises(sqlite3.OperationalError, match="readonly"), readonly.connection() as db:
        db.execute("CREATE TABLE forbidden(id INTEGER)")
    assert readonly.source_url == "http://127.0.0.1:7777"


def test_remote_uses_live_api_and_auth_without_changing_ui_origin(store, monkeypatch):
    import httpx
    real_client = httpx.AsyncClient
    def handle(request):
        assert str(request.url).startswith("http://live/aos/assistant/v1/resources")
        assert request.headers["authorization"] == "Bearer production-test"
        return httpx.Response(200, json={"items": [], "next": None})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    models = Models()
    models.product_token = lambda provider: "preview-test"
    service = Service(store, models, {"paper": {"url": "http://preview", "api_url": "http://live",
        "section": "paper", "token_provider": lambda provider: "production-test"}}, None)
    assert asyncio.run(service.remote("paper", "resources"))["items"] == []
    assert service.providers["paper"]["url"] == "http://preview"


def test_live_preview_preserves_csrf_and_shares_upstream_data():
    import httpx

    from aos.assistant.preview import create_preview

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"live-data"

    received = []
    async def upstream(request):
        received.append((request.method, str(request.url), dict(request.headers), await request.aread()))
        status = 200 if request.method == "GET" or request.headers.get("x-product-token") == "csrf" else 403
        return httpx.Response(status, stream=Body())

    async def run():
        app = create_preview("http://127.0.0.1:8602", lambda: "internal-preview", httpx.MockTransport(upstream))
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8702") as c:
            assert (await c.get("/api/documents")).text == "live-data"
            assert (await c.post("/api/notes", content=b"body")).status_code == 403
            assert (await c.post("/api/notes", content=b"body", headers={"origin": "http://evil.invalid", "x-product-token": "csrf"})).status_code == 403
            assert len(received) == 2
            assert (await c.post("/api/notes", content=b"body", headers={"origin": "http://127.0.0.1:8702", "x-product-token": "csrf", "x-aos-preview-token": "forged"})).status_code == 200
        method, url, headers, body = received[-1]
        assert method == "POST" and url == "http://127.0.0.1:8602/api/notes" and body == b"body"
        assert headers["host"] == "127.0.0.1:8702"
        assert headers["x-aos-preview-token"] == "internal-preview"
        assert headers["x-product-token"] == "csrf"
    asyncio.run(run())
    with pytest.raises(ValueError, match="loopback"):
        create_preview("https://outside.invalid", lambda: "secret")
