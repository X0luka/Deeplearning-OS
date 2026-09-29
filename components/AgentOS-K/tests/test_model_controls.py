import httpx
import pytest

from aos.adapters.sqlite import SQLiteArchive
from aos.assistant.transport import model_request
from aos.host.web import create_app
from aos.llm.catalog import model_item
from aos.llm.store import ModelStore


@pytest.fixture
def store(tmp_path):
    s = ModelStore(tmp_path / "llm")
    s.save_connection({"name": "Custom", "protocol": "chat", "base_url": "https://api.deepseek.com", "key": "fixture"})
    c = s.read()["connections"][0]["id"]
    s.mutate(lambda v: v["connections"][0]["models"].append(model_item("any-model")))
    s.settings("global", {"selection": {"connection": c, "model": "any-model"}})
    return s


def test_user_controls_persist_and_session_off_wins(store):
    store.settings("assistant:chat", {"tools": True, "thinking": "auto"})
    persisted = ModelStore(store.root)
    active = persisted.resolve("assistant", "chat", include_secret=True)
    assert active["tools"] is True and active["thinking"] == "auto"
    tool = {"type": "function", "function": {"name": "search_resources", "parameters": {"type": "object"}}}
    _, _, payload = model_request(active, [{"role": "user", "content": "Find papers"}], [tool])
    assert payload["tools"] == [tool] and payload["thinking"] == {"type": "enabled"}
    disabled = persisted.resolve("assistant", "chat", {"tools": False, "thinking": "off"}, True)
    _, _, payload = model_request(disabled, [{"role": "user", "content": "Hello"}], [])
    assert "tools" not in payload and payload["thinking"] == {"type": "disabled"}
    assert persisted.resolve("assistant", "chat")["tools"] is True


def test_explicit_incompatibility_does_not_rewrite_user_choice(store):
    store.settings("assistant:chat", {"tools": True, "thinking": "auto"})
    store.mutate(lambda v: v["connections"][0]["models"][0]["capabilities"].update(tools=False))
    with pytest.raises(ValueError, match="unsupported_tools"):
        store.resolve("assistant", "chat")
    assert store.read()["overrides"]["assistant:chat"]["tools"] is True
    store.mutate(lambda v: v["connections"][0]["models"][0]["capabilities"].update(tools=True, thinking=False))
    with pytest.raises(ValueError, match="unsupported_thinking"):
        store.resolve("assistant", "chat")


async def test_assistant_controls_are_editable_in_model_center(store, tmp_path):
    app = create_app(None, None, SQLiteArchive(tmp_path / "archive.db"), model_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        headers = {"X-Aos-Token": app.state.write_token}
        catalog = (await c.get("/api/v1/llm", headers=headers)).json()
        assert catalog["tasks"]["assistant"] == {"chat": "对话"}
        for enabled, thinking in [(True, "auto"), (False, "off")]:
            response = await c.put("/api/v1/llm/settings/assistant:chat", headers=headers,
                                   json={"tools": enabled, "thinking": thinking})
            assert response.status_code == 200
            value = store.resolve("assistant", "chat")
            assert value["tools"] == enabled and value["thinking"] == thinking
        assert (await c.put("/api/v1/llm/settings/assistant:chat", headers=headers, json={})).status_code == 200
        assert store.resolve("assistant", "chat")["thinking"] == "default"
