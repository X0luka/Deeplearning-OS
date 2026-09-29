"""Offline HTTP acceptance of manually entered synthetic credentials."""
import json

import httpx
import pytest

from aos.adapters.sqlite import SQLiteArchive
from aos.host.web import create_app
from aos.llm.store import ModelStore


async def test_manual_key_lifecycle_and_restart(tmp_path):
    store = ModelStore(tmp_path / "llm")
    app = create_app(None, None, SQLiteArchive(tmp_path / "archive.db"), model_store=store)
    value = {"name": "Synthetic", "protocol": "chat", "base_url": "https://models.test/v1"}
    keys = ["synthetic-first-credential", "synthetic-replacement-credential"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        headers = {"X-Aos-Token": app.state.write_token}
        async def post(path, body):
            response = await client.post("/api/v1/llm/" + path, headers=headers, json=body)
            assert response.status_code == 200
            assert all(key not in response.text for key in keys)
            return response.json()
        public = await post("connections", {**value, "key": keys[0]})
        identity = public["connections"][0]["id"]
        await post(f"connections/{identity}/models", {"id": "synthetic-model"})
        response = await client.put("/api/v1/llm/settings/global", headers=headers, json={
            "selection": {"connection": identity, "model": "synthetic-model"},
            "max_output_tokens": 32, "tools": False, "web_search": False})
        assert response.status_code == 200
        restarted = ModelStore(store.root)
        assert restarted.resolve("assistant", "chat", include_secret=True)["api_key"] == keys[0]
        # Blank key while editing the same endpoint retains the configured credential.
        await post("connections", {**value, "id": identity, "key": ""})
        assert restarted.resolve("assistant", "chat", include_secret=True)["api_key"] == keys[0]
        await post("connections", {**value, "id": identity, "key": keys[1]})
        assert restarted.resolve("assistant", "chat", include_secret=True)["api_key"] == keys[1]
        assert all(keys[0] not in p.read_text() for p in store.private.iterdir())
        await post(f"connections/{identity}/clear-key", {})
        with pytest.raises(ValueError, match="model_key_not_configured"):
            ModelStore(store.root).resolve("assistant", "chat", include_secret=True)
        assert all(not any(k in p.read_text() for k in keys) for p in store.private.iterdir())
        await post("connections", {**value, "id": identity, "key": keys[1]})
        await client.put("/api/v1/llm/settings/global", headers=headers, json={})
        response = await client.delete(f"/api/v1/llm/connections/{identity}", headers=headers)
        assert response.status_code == 200
        assert not list(store.private.iterdir())
        assert all(k not in json.dumps(store.read()) for k in keys)


def test_cleanup_preserves_other_connections_and_service_tokens(tmp_path):
    store = ModelStore(tmp_path / "llm")
    ids = []
    for name in ("one", "two"):
        state = store.save_connection({"name": name, "protocol": "chat",
                                      "base_url": "https://models.test", "key": "fake-" + name})
        ids.append(state["connections"][-1]["id"])
    token = store.product_token("paper")
    store.save_connection({"id": ids[0], "name": "one", "protocol": "chat",
                           "base_url": "https://models.test", "key": "fake-new"})
    store.delete_connection(ids[0])
    assert store.secret(ids[1]) == "fake-two"
    assert store.product_token("paper") == token
    assert {p.name for p in store.private.iterdir()} == {ids[1], "product-paper"}
