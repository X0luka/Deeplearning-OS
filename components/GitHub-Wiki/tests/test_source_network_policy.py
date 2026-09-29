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
