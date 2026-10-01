import copy
import json

import httpx
import pytest

from aos.assistant.links import present_turn


@pytest.mark.parametrize("old", [
    "http://127.0.0.1:18989/?document=doc#page=3",
    "http://localhost:18989/?document=doc#page=3",
    "http://[::1]:18989/?document=doc#page=3",
])
def test_saved_local_citations_follow_current_provider_without_mutating_history(old):
    turn = {"answer": "Original answer [[E1]]", "snapshot": {"historical_url": old},
            "evidence": [{"id": "E1", "provider": "paper", "url": old,
                          "location": {"document": "doc", "page": 3}}]}
    original = copy.deepcopy(turn)
    shown = present_turn(turn, {"paper": {"url": "http://127.0.0.1:19879"}})
    assert shown["evidence"][0]["url"] == "http://127.0.0.1:19879/?document=doc#page=3"
    assert shown["answer"] == original["answer"]
    assert shown["snapshot"] == original["snapshot"]
    assert turn == original


@pytest.mark.parametrize("url,provider,target", [
    ("https://arxiv.org/abs/1706.03762", "paper", "http://127.0.0.1:19879"),
    ("http://127.0.0.1.example.invalid:18989/", "paper", "http://127.0.0.1:19879"),
    ("http://127.0.0.1:18989/", "unregistered", "http://127.0.0.1:19879"),
    ("/assistant?conversation=old", "paper", "http://127.0.0.1:19879"),
    ("javascript:alert(1)", "paper", "http://127.0.0.1:19879"),
    ("http://user:synthetic@127.0.0.1:18989/", "paper", "http://127.0.0.1:19879"),
    ("http://[invalid/", "paper", "http://127.0.0.1:19879"),
    ("http://127.0.0.1:18989/", "paper", "https://outside.invalid"),
])
def test_external_unknown_relative_or_malformed_links_are_not_retargeted(url, provider, target):
    original = {"evidence": [{"provider": provider, "url": url}]}
    assert present_turn(original, {"paper": {"url": target}}) == original


@pytest.mark.asyncio
async def test_restored_history_routes_relocate_citations_and_preserve_database(tmp_path):
    from aos.adapters.sqlite import SQLiteArchive
    from aos.host.web import create_app
    from aos.llm.store import ModelStore

    providers = {"wiki": {"url": "http://127.0.0.1:19878", "name": "Wiki", "section": "wiki"}}
    app = create_app(None, None, SQLiteArchive(tmp_path / "archive.db"),
                     model_store=ModelStore(tmp_path / "llm"), assistant_providers=providers)
    store = app.state.assistant.store
    c = store.create("Restored conversation", "wiki", [])
    identity, _ = store.enqueue(c["id"], "restored-request", "Original question", {"resources": []})
    old_url = "http://127.0.0.1:18988/versions/sha/source?path=src%2Fsample.py&start=1#L1"
    store.update(identity, state="completed", answer="Original answer [[E1]]",
                 evidence=[{"id": "E1", "provider": "wiki", "url": old_url}])
    before = store.turn(identity)
    headers = {"X-AOS-Token": app.state.write_token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        direct = (await client.get("/api/v1/assistant/turns/" + identity, headers=headers)).json()
        conversation = (await client.get("/api/v1/assistant/conversations/" + c["id"], headers=headers)).json()
        replay = await client.post("/api/v1/assistant/conversations/" + c["id"] + "/turns",
                                  headers=headers, json={"question": "Original question", "request_key": "restored-request"})
        assert replay.status_code == 202
        events = await client.get("/api/v1/assistant/turns/" + identity + "/events", headers=headers)
        event = json.loads(events.text.removeprefix("data: ").strip())
    for shown in (direct, conversation["turns"][0], replay.json(), event):
        assert shown["evidence"][0]["url"] == old_url.replace(":18988", ":19878")
        assert shown["answer"] == before["answer"] and shown["snapshot"] == before["snapshot"]
    assert store.turn(identity) == before
    assert not app.state.assistant.tasks  # Reopening and idempotent replay never invoke a model.
