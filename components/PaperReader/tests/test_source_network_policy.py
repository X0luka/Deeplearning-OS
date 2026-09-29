import pytest
from paper_reader import llm_client
from paper_reader.reading_sources import source_proxy


def test_managed_source_policy_ignores_ambient_proxy_and_propagates_failure(monkeypatch):
    monkeypatch.setenv("AOS_LLM_MODE", "managed")
    monkeypatch.setenv("PAPER_SOURCE_PROXY", "http://wrong.test:9999")
    monkeypatch.setattr(llm_client, "network", lambda: {"proxy": "http://127.0.0.1:7897"})
    assert source_proxy() == "http://127.0.0.1:7897"
    monkeypatch.setattr(llm_client, "network", lambda: {"proxy": None})
    assert source_proxy() is None
    def unavailable():
        raise ValueError("platform_configuration_unavailable")
    monkeypatch.setattr(llm_client, "network", unavailable)
    with pytest.raises(ValueError, match="platform_configuration_unavailable"):
        source_proxy()
