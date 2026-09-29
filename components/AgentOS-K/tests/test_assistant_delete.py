import pytest
import httpx
from aos.assistant.store import Store
from aos.adapters.sqlite import SQLiteArchive
from aos.host.web import create_app
from aos.llm.store import ModelStore


def test_delete_retains_usage_and_blocks_enqueue(tmp_path):
    s = Store(tmp_path / "assistant.db")
    c = s.create("test", "paper", [{"id": "original"}])
    t, _ = s.enqueue(c["id"], "test-key", "question", {})
    with pytest.raises(ValueError, match="conversation_busy"):
        s.delete_conversation(c["id"])
    s.update(t, state="completed", usage={"total_tokens": 123})
    s.save_evidence(t, [{"id": "E1", "text": "original"}])
    s.delete_conversation(c["id"])
    s.delete_conversation(c["id"])
    assert s.turn(t)["usage"] == {"total_tokens": 123}
    assert s.rows("SELECT * FROM turn_evidence")
    for key in ["test-key", "new-key"]:
        with pytest.raises(ValueError, match="conversation_not_found"):
            s.enqueue(c["id"], key, "question", {})
    with pytest.raises(ValueError, match="conversation_not_found"):
        Store(s.path).conversation(c["id"])


@pytest.mark.asyncio
async def test_delete_api_auth_busy_and_persistence(tmp_path):
    app = create_app(None, None, SQLiteArchive(tmp_path / "archive.db"), model_store=ModelStore(tmp_path / "llm"))
    s = app.state.assistant.store
    c = s.create("first", "paper", [])
    other = s.create("second", "general", [])
    url = "/api/v1/assistant/conversations/" + c["id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        assert (await client.delete(url)).status_code == 403
        h = {"X-AOS-Token": app.state.write_token}
        t, _ = s.enqueue(c["id"], "busy-key", "q", {})
        assert (await client.delete(url, headers=h)).status_code == 409
        s.update(t, state="completed")
        assert (await client.delete(url, headers=h)).status_code == 200
        assert (await client.delete(url, headers=h)).status_code == 200
        items = (await client.get("/api/v1/assistant/conversations", headers=h)).json()["items"]
        assert [x["id"] for x in items] == [other["id"]]
        assert (await client.get(url, headers=h)).status_code != 200
        assert (await client.delete(url + "missing", headers=h)).status_code == 404
