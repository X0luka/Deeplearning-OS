import json

import httpx
import pytest

from aos.llm.catalog import discover
from aos.llm.store import ModelStore


def test_empty_store_never_uses_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'must-not-be-used')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-be-used')
    store = ModelStore(tmp_path)
    with pytest.raises(ValueError, match='model_not_configured'):
        store.resolve('paper', 'chat', include_secret=True)
    assert not store.public()['connections']


async def test_custom_name_model_and_persisted_connection(tmp_path):
    store = ModelStore(tmp_path)
    store.save_connection({'name': '用户自己的名称', 'protocol': 'chat',
                           'base_url': 'https://custom.example/prefix/v1/', 'key': 'fixture-key'})
    identity = store.public()['connections'][0]['id']
    calls = []

    def provider(request):
        calls.append(request)
        assert str(request.url) == 'https://custom.example/prefix/v1/models'
        assert request.headers['authorization'] == 'Bearer fixture-key'
        return httpx.Response(200, json={'data': [{'id': 'arbitrary-model-42'}]})

    await discover(store, identity, httpx.MockTransport(provider))
    store.settings('global', {'selection': {'connection': identity, 'model': 'arbitrary-model-42'}})
    fresh = ModelStore(tmp_path)
    for product, task in [('paper', 'chat'), ('paper', 'reading'), ('github-wiki', 'structured'), ('assistant', 'chat')]:
        resolved = fresh.resolve(product, task, include_secret=True)
        assert resolved['api_key'] == 'fixture-key'
        assert resolved['model'] == 'arbitrary-model-42'
        assert resolved['connection_name'] == '用户自己的名称'
    assert [r.method for r in calls] == ['GET']
    assert 'fixture-key' not in json.dumps(fresh.public())


def test_new_connection_requires_user_key_but_edit_can_retain_it(tmp_path):
    store = ModelStore(tmp_path)
    value = {'name': '自定义', 'protocol': 'chat', 'base_url': 'https://service.example/v1'}
    with pytest.raises(ValueError, match='key_required'):
        store.save_connection(value)
    store.save_connection({**value, 'key': 'fixture'})
    conn = store.read()['connections'][0]
    store.save_connection({**value, 'id': conn['id'], 'name': '改名不改变厂商'})
    assert store.public()['connections'][0]['configured']


@pytest.mark.parametrize('exception,code', [(httpx.ConnectTimeout, 'catalog_timeout'),
                                          (httpx.ProxyError, 'catalog_proxy_error'),
                                          (httpx.ConnectError, 'catalog_connect_error')])
async def test_network_errors_are_actionable_and_sanitized(tmp_path, exception, code):
    store = ModelStore(tmp_path)
    store.save_connection({'name': '自定义', 'protocol': 'chat',
                           'base_url': 'https://test.example/v1', 'key': 'fixture-key'})
    identity = store.public()['connections'][0]['id']

    def fail(request):
        raise exception('unsafe detail fixture-key', request=request)

    with pytest.raises(ValueError, match=code):
        await discover(store, identity, httpx.MockTransport(fail))
    public = store.public()
    assert public['connections'][0]['error'] == code
    assert 'fixture-key' not in json.dumps(public)
