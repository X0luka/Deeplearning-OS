import json

import httpx
import pytest

from aos.llm.catalog import preview_connection, model_item
from aos.llm.store import ModelStore


def saved(tmp_path):
    store = ModelStore(tmp_path/'llm')
    value = {'name':'Fixture','protocol':'chat','base_url':'https://provider.test/v1','key':'synthetic-only','selected_model':'one'}
    store.save_connection(value)
    return store, store.public()['connections'][0]['id'], value


@pytest.mark.asyncio
async def test_preview_does_not_save_or_expose_key_and_refuses_endpoint_reuse(tmp_path):
    store, identity, value = saved(tmp_path)
    before = store.read()
    private = {p.name:p.read_bytes() for p in store.private.iterdir()}
    seen = []
    def provider(request):
        seen.append(request)
        assert request.method == 'GET'
        assert request.headers['authorization'] == 'Bearer synthetic-only'
        return httpx.Response(200, json={'data':[{'id':'two','context_length':100000}]})
    draft = {k:v for k,v in value.items() if k not in {'key','selected_model'}}
    draft['id'] = identity
    models = await preview_connection(store, draft, httpx.MockTransport(provider))
    assert models[0]['id'] == 'two' and models[0]['limits']['context_tokens'] == 100000
    assert store.read() == before
    assert {p.name:p.read_bytes() for p in store.private.iterdir()} == private
    assert 'synthetic-only' not in json.dumps(models)
    draft['base_url'] = 'https://another.test/v1'
    with pytest.raises(ValueError, match='key_required_for_new_endpoint'):
        await preview_connection(store, draft, httpx.MockTransport(provider))
    assert len(seen) == 1


def test_edit_keep_replace_clear_and_detach_without_quality_reset(tmp_path):
    store, identity, value = saved(tmp_path)
    store.settings('paper', {'selection':{'connection':identity,'model':'one'},'max_output_tokens':20000})
    store.mutate(lambda s:s['connections'][0]['models'][0]['capabilities'].update(tools=True))
    store.save_connection({**value,'id':identity}, catalog=[model_item('one', {'id':'one'})])
    assert store.public()['connections'][0]['models'][0]['capabilities']['tools'] is True
    original = store.read()['defaults'].copy()
    edit = {**value,'id':identity,'selected_model':'two'}
    edit.pop('key')
    store.save_connection(edit, catalog=[model_item('two')])
    assert store.resolve('assistant','chat', None, True)['api_key'] == 'synthetic-only'
    assert store.read()['defaults']['selection']['model'] == 'two'
    assert store.read()['defaults']['max_output_tokens'] == original['max_output_tokens']
    store.save_connection({**edit,'key':'replacement-synthetic'})
    assert 'synthetic-only' not in [p.read_text() for p in store.private.iterdir()]
    store.save_connection({**edit,'key':'','clear_key':True})
    assert not store.public()['connections'][0]['configured']
    assert all('replacement-synthetic' not in p.read_text() for p in store.private.iterdir())
    store.delete_connection(identity)
    assert store.read()['defaults']['selection'] is None
    assert store.read()['overrides']['paper']['selection'] is None
    assert store.read()['overrides']['paper']['max_output_tokens'] == 20000
    assert not list(store.private.iterdir())


@pytest.mark.asyncio
async def test_configure_auth_and_failed_catalog_leave_saved_state_unchanged(tmp_path, monkeypatch):
    from aos.adapters.sqlite import SQLiteArchive
    from aos.host.web import create_app
    import aos.llm.routes as routes
    store, identity, value = saved(tmp_path)
    app = create_app(None,None,SQLiteArchive(tmp_path/'archive.db'),model_store=store)
    async def catalog(*args):
        return [model_item('two', {'context_length':100000})]
    monkeypatch.setattr(routes,'preview_connection',catalog)
    before = store.read()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://testserver') as client:
        request = {**value,'id':identity,'selected_model':'missing'}
        assert (await client.post('/api/v1/llm/connections/configure',json=request)).status_code == 403
        headers = {'X-Aos-Token':app.state.write_token}
        assert (await client.post('/api/v1/llm/connections/configure',json=request,headers=headers)).status_code == 409
        assert store.read() == before
        request['selected_model'] = 'two'
        result = await client.post('/api/v1/llm/connections/configure',json=request,headers=headers)
        assert result.status_code == 200 and 'synthetic-only' not in result.text
        assert store.read()['defaults']['selection']['model'] == 'two'
        request.update(key='',clear_key=True)
        assert (await client.post('/api/v1/llm/connections/configure',json=request,headers=headers)).status_code == 200
        assert not store.public()['connections'][0]['configured']
