import httpx
from test_product import create_version, fixture_fetch

from wiki_app.engine import Engine
from wiki_app.main import create_app


async def test_assistant_source_scope_and_exact_block(tmp_path, monkeypatch):
    monkeypatch.setenv("AOS_LLM_TOKEN", "offline-tools-token")
    app = create_app(tmp_path / "data", lambda store: Engine(store, fetcher=fixture_fetch))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"x-wiki-token": app.state.token},
        ) as client,
    ):
        _, version = await create_version(client)
        path = "/aos/assistant/v1/search"
        assert (await client.get(path, params={"resource_id": version})).status_code == 403
        headers = {"Authorization": "Bearer offline-tools-token"}
        response = await client.get(
            path, headers=headers, params={"resource_id": version, "block_id": "src/store.py:1"}
        )
        assert response.status_code == 200
        evidence = response.json()["items"][0]
        assert evidence["resource"] == version
        assert evidence["title"] == "src/store.py"
        assert "def save" in evidence["text"]
        assert "end=" in evidence["url"]
        named = await client.get(
            path, headers=headers, params={"resource_id": version, "query": "请读取 src/store.py 并说明作用"}
        )
        assert any(i["title"] == "src/store.py" for i in named.json()["items"])
        listing = await client.get("/aos/assistant/v1/tree", headers=headers,
            params={"resource_id": version, "prefix": "src/", "limit": 1})
        assert listing.status_code == 200
        import json
        files = json.loads(listing.json()["items"][0]["text"])
        assert len(files["files"]) == 1 and files["files"][0]["path"].startswith("src/")
        assert (await client.get("/aos/assistant/v1/tree", headers=headers,
            params={"resource_id": version, "version": "wrong"})).status_code == 409
        assert (await client.get("/aos/assistant/v1/tree", headers=headers,
            params={"resource_id": version, "prefix": "../"})).status_code == 400
        assert (await client.get("/aos/assistant/v1/tree",
            params={"resource_id": version})).status_code == 403
        plain = await client.get(path, headers=headers,
            params={"resource_id": version, "block_id": "src/store.py"})
        assert plain.json()["items"][0]["title"] == "src/store.py"
        contextual = await client.get(path, headers=headers, params={"resource_id": version,
            "query": "解释当前页面", "view": '{"page":"overview"}'})
        assert contextual.status_code == 200
        assert any(i["block"] == "view-context" for i in contextual.json()["items"])

        assert (
            await client.get(path, headers=headers, params={"resource_id": version, "version": "changed"})
        ).status_code == 409
        assert (
            await client.get(
                path, headers=headers, params={"resource_id": version, "block_id": "src/store.py:bad"}
            )
        ).status_code == 400


def test_preview_ui_requires_internal_token(monkeypatch):
    from starlette.requests import Request

    from wiki_app.assistant_tools import ui_host

    monkeypatch.delenv("AOS_UNIFIED_ASSISTANT", raising=False)
    monkeypatch.setenv("AOS_ASSISTANT_PREVIEW_HOST", "http://127.0.0.1:8777")
    monkeypatch.setenv("AOS_LLM_TOKEN", "internal-preview")
    def request(token):
        return Request({"type": "http", "headers": [(b"x-aos-preview-token", token.encode())]})
    assert ui_host(request("")) == ""
    assert ui_host(request("wrong")) == ""
    assert ui_host(request("internal-preview")) == "http://127.0.0.1:8777"
