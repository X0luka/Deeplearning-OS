"""The appearance proxy exposes only a valid public preference and fails closed."""
import asyncio

import httpx

from wiki_app import appearance as module


def test_appearance_proxy_validation_and_offline(monkeypatch):
    values = [{"mode": "dark", "private": "never forwarded"}, {"mode": "light"},
              {"mode": "system"}, {"mode": "bad"}, [], None]

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 1.5, "trust_env": False}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            assert url == "http://127.0.0.1:7777/api/v1/appearance"
            value = values.pop(0)
            if value is None:
                raise httpx.ConnectError("offline")
            return httpx.Response(200, json=value, request=httpx.Request("GET", url))

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    for mode in ("dark", "light", "system"):
        response = asyncio.run(module.appearance())
        assert response.status_code == 200
        assert response.body == ('{"mode":"' + mode + '"}').encode()
    for _ in range(3):
        assert asyncio.run(module.appearance()).status_code == 503
