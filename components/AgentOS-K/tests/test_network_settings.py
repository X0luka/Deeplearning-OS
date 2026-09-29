import httpx
import pytest

from aos.adapters.sqlite import SQLiteArchive
from aos.host.web import create_app
from aos.llm.catalog import model_item
from aos.llm.network import defaults
from aos.llm.store import ModelStore


def test_network_persistence_inheritance_and_connection_priority(tmp_path):
    store = ModelStore(tmp_path / "llm")
    value = defaults()
    value["default"] = {"mode": "proxy", "proxy": "http://127.0.0.1:7897"}
    value["paper"] = {"mode": "direct", "proxy": ""}
    value["wiki"] = {"mode": "proxy", "proxy": "http://127.0.0.1:7898"}
    store.save_network(value)
    restarted = ModelStore(store.root)
    assert restarted.effective_proxy("models") == "http://127.0.0.1:7897"
    assert restarted.effective_proxy("paper") is None
    assert restarted.effective_proxy("wiki") == "http://127.0.0.1:7898"
    assert restarted.effective_proxy("models", "http://127.0.0.1:8001") == "http://127.0.0.1:8001"
    store.save_connection({"name": "Synthetic", "protocol": "chat", "base_url": "https://models.test", "key": "fake"})
    identity = store.read()["connections"][0]["id"]
    store.mutate(lambda s: s["connections"][0]["models"].append(model_item("test")))
    store.settings("global", {"selection": {"connection": identity, "model": "test"}})
    assert store.resolve("assistant", "chat")["proxy"] == "http://127.0.0.1:7897"
    before = store.read()
    value["default"]["proxy"] = "http://secret@proxy.test"
    with pytest.raises(ValueError, match="invalid_proxy_address"):
        store.save_network(value)
    assert store.read() == before


async def test_network_routes_auth_and_product_channel_scope(tmp_path):
    store = ModelStore(tmp_path / "llm")
    app = create_app(None, None, SQLiteArchive(tmp_path / "archive.db"), model_store=store,
                     model_tasks={"paper": {"chat": "chat"}, "wiki": {"chat": "chat"}})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        assert (await c.get("/api/v1/llm/network")).status_code == 403
        headers = {"X-Aos-Token": app.state.write_token}
        value = defaults()
        value["default"] = {"mode": "proxy", "proxy": "http://127.0.0.1:7897"}
        assert (await c.put("/api/v1/llm/network", headers={**headers, "Origin": "https://evil.test"}, json=value)).status_code == 403
        assert (await c.put("/api/v1/llm/network", headers=headers, json=value)).status_code == 200
        auth = {"Authorization": "Bearer " + store.product_token("paper")}
        result = await c.get("/internal/llm/v1/paper/network", headers=auth)
        assert result.json() == {"proxy": "http://127.0.0.1:7897", "source": "default"}
        assert (await c.get("/internal/llm/v1/wiki/network", headers=auth)).status_code == 403
        assert set(result.json()) == {"proxy", "source"}
