import pytest
from wiki_app import llm_client
from wiki_app.model_config import source_proxy


def test_managed_source_policy_ignores_ambient_proxy_and_propagates_failure(monkeypatch):
    monkeypatch.setenv("AOS_LLM_MODE", "managed")
    monkeypatch.setenv("WIKI_HTTPS_PROXY", "http://wrong.test:9999")
    monkeypatch.setattr(llm_client, "network", lambda: {"proxy": "http://127.0.0.1:7897"})
    assert source_proxy(None) == "http://127.0.0.1:7897"
    monkeypatch.setattr(llm_client, "network", lambda: {"proxy": None})
    assert source_proxy(None) is None
    def unavailable():
        raise ValueError("platform_configuration_unavailable")
    monkeypatch.setattr(llm_client, "network", unavailable)
    with pytest.raises(ValueError, match="platform_configuration_unavailable"):
        source_proxy(None)


@pytest.mark.parametrize("kind", ["connect", "timeout", "status"])
async def test_update_network_failure_is_actionable_and_preserves_version(tmp_path, monkeypatch, kind):
    import httpx
    from test_architecture import fixture_store
    from wiki_app import main

    store, version = await fixture_store(tmp_path)
    request = httpx.Request("GET", "https://api.github.com/repos/example/repo/commits/HEAD")

    async def unavailable(*args, **kwargs):
        if kind == "status":
            raise httpx.HTTPStatusError("synthetic-sensitive-upstream-detail", request=request,
                                        response=httpx.Response(502, request=request))
        error = httpx.ConnectError if kind == "connect" else httpx.ReadTimeout
        raise error("synthetic-sensitive-upstream-detail", request=request)

    monkeypatch.setattr(main, "check_update", unavailable)
    app = main.create_app(store.root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        response = await client.get("/api/repos/r/updates")
        assert response.status_code == 503
        value = response.json()
        assert value["detail"] == "github_unavailable"
        assert value["failure"]["rule_id"] == "INGEST.NETWORK"
        assert "代理" in value["failure"]["action"]
        assert "synthetic-sensitive" not in response.text
        assert store.repo("r")["current"] == version
        assert (await client.get("/versions/" + version)).status_code == 200

        async def restored(*args, **kwargs):
            return {"commit": "a" * 40, "pinned": False}

        monkeypatch.setattr(main, "check_update", restored)
        response = await client.get("/api/repos/r/updates")
        assert response.status_code == 200 and not response.json()["available"]
