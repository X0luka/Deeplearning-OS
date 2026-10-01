import json

import httpx
import pytest

from aos.adapters.sqlite import SQLiteArchive
from aos.host.web import create_app
from aos.llm.catalog import discover, model_item
from aos.llm.store import ModelStore


@pytest.fixture
def models(tmp_path):
    store = ModelStore(tmp_path/'llm')
    store.save_connection({'name':'Test','protocol':'chat','base_url':'https://models.test/v1','key':'private-key'})
    c = store.read()['connections'][0]['id']
    store.mutate(lambda s:s['connections'][0]['models'].append(model_item('unlisted-new-model')))
    store.settings('global', {'selection':{'connection':c,'model':'unlisted-new-model'},'custom':'中文'})
    return store,c


def test_inheritance_snapshot_and_secrets(models):
    store,c = models
    store.settings('alpha',{'max_output_tokens':4000})
    store.settings('alpha:chat',{'max_output_tokens':5000})
    snapshot = store.resolve('alpha','chat',{'max_output_tokens':6000},True)
    assert snapshot['api_key']=='private-key' and snapshot['max_output_tokens']==6000
    assert 'private-key' not in json.dumps(store.public())
    assert 'api_key' not in store.resolve('alpha','chat')
    store.settings('alpha:chat',{})
    assert store.resolve('alpha','chat')['max_output_tokens']==4000
    assert snapshot['max_output_tokens']==6000
    assert (store.private/c).stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError,match='connection_in_use'):
        store.delete_connection(c)


def test_unknown_optional_capabilities_and_disabled_connection(models):
    store,_c=models
    assert store.resolve('alpha','chat')['thinking']=='default'
    assert store.resolve('alpha','chat',{'tools':True})['tools'] is True
    store.mutate(lambda s:s['connections'][0]['models'][0]['capabilities'].update(tools=False))
    with pytest.raises(ValueError,match='unsupported_tools'):
        store.resolve('alpha','chat',{'tools':True})
    store.mutate(lambda s:s['connections'][0].update(enabled=False))
    with pytest.raises(ValueError,match='connection_disabled'):
        store.resolve('alpha','chat')


async def test_paginated_catalog_and_failure_retains_snapshot(models):
    store,c=models
    seen=[]
    def upstream(req):
        seen.append(req)
        assert req.method=='GET' and req.headers['authorization']=='Bearer private-key'
        return httpx.Response(200,json={'data':[{'id':'a' if len(seen)==1 else 'b'}],
                                       'has_more':len(seen)==1,'last_id':'a'})
    await discover(store,c,httpx.MockTransport(upstream))
    assert len(seen)==2 and seen[1].url.params['after']=='a'
    old=store.read()['connections'][0]['models']
    with pytest.raises(ValueError,match='catalog_http_401'):
        await discover(store,c,httpx.MockTransport(lambda r:httpx.Response(401,text='private-key')))
    assert store.read()['connections'][0]['models']==old
    assert 'private-key' not in json.dumps(store.public())


async def test_ui_write_auth_product_isolation_and_no_secret_to_browser(models,tmp_path):
    store,_c=models
    app=create_app(None,None,SQLiteArchive(tmp_path/'archive.db'),model_store=store,
                   model_tasks={'alpha':{'chat':'对话'},'beta':{'chat':'对话'}})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://testserver') as client:
        assert (await client.get('/settings/models')).status_code==200
        assert (await client.get('/api/v1/llm')).status_code==403
        headers={'X-Aos-Token':app.state.write_token}
        public=await client.get('/api/v1/llm',headers=headers)
        assert public.status_code==200 and 'private-key' not in public.text
        assert (await client.put('/api/v1/llm/settings/global',json={},headers={**headers,'Origin':'https://evil.test'})).status_code==403
        auth={'Authorization':'Bearer '+store.product_token('alpha')}
        assert (await client.post('/internal/llm/v1/beta/resolve',headers=auth,json={'execution':True})).status_code==403
        r=await client.post('/internal/llm/v1/alpha/resolve',headers=auth,json={'execution':True,'task':'chat'})
        assert r.json()['api_key']=='private-key' and r.headers['cache-control']=='no-store'
        assert (await client.post('/internal/llm/v1/alpha/resolve',headers=auth,json={'task':'shell'})).status_code==403
        assert (await client.post('/internal/llm/v1/alpha/resolve',headers=auth,json={'override':{'custom':'overwrite'}})).status_code==403


def test_url_credentials_and_settings_validation(models):
    store,_=models
    for url in ['http://public.test/v1','https://key@host.test/v1','https://host.test/v1?key=secret']:
        with pytest.raises(ValueError):
            store.save_connection({'name':'bad','protocol':'chat','base_url':url})
    for value in [{'max_output_tokens':True},{'temperature':float('nan')},{'tools':'false'}]:
        with pytest.raises(ValueError):
            store.settings('global',value)


def test_verified_limits_are_endpoint_scoped_and_enforced(tmp_path):
    from aos.llm.catalog import model_limits
    store = ModelStore(tmp_path/'limits')
    store.save_connection({'name':'DeepSeek','protocol':'chat',
                           'base_url':'https://api.deepseek.com','key':'test'})
    c=store.read()['connections'][0]['id']
    store.mutate(lambda s:s['connections'][0]['models'].append(model_item('deepseek-v4-flash')))
    selected={'connection':c,'model':'deepseek-v4-flash'}
    before=store.read()
    limits=store.public()['connections'][0]['models'][0]['limits']
    assert limits['context_tokens']==1048576
    assert limits['max_output_tokens']==393216
    assert limits['serving_model']=='DeepSeek-V4.1-Flash'
    assert store.read()==before  # Existing configuration is enriched read-only.
    store.settings('global',{'selection':selected,'max_output_tokens':393216})
    assert store.resolve('paper','chat')['max_output_tokens']==393216
    with pytest.raises(ValueError,match='model_output_limit_exceeded'):
        store.settings('paper:chat',{'max_output_tokens':393217})
    with pytest.raises(ValueError,match='model_output_limit_exceeded'):
        store.resolve('paper','chat',{'max_output_tokens':393217})
    third_party={'base_url':'https://proxy.example/v1','protocol':'chat'}
    assert model_limits(third_party,model_item('deepseek-v4-flash'))['context_tokens'] is None
    assert model_limits(third_party,model_item('deepseek-v4-flash'))['max_output_tokens'] is None


def test_discovered_limits_and_unknown_are_not_guessed():
    from aos.llm.catalog import model_limits
    conn={'base_url':'https://models.test/v1','protocol':'chat'}
    model=model_item('custom',{'context_length':123456,'top_provider':{'max_completion_tokens':48000}})
    assert model_limits(conn,model)['max_output_tokens']==48000
    assert model_limits(conn,model)['context_tokens']==123456
    model=model_item('other',{'context_length':'invalid'})
    assert model_limits(conn,model)['context_tokens'] is None


def test_new_quality_budget_preserves_saved_user_choices(tmp_path):
    directory = tmp_path / 'llm'
    store = ModelStore(directory)
    assert store.read()['defaults']['max_output_tokens'] == 16384
    # A saved older budget, including task overrides, is a deliberate user choice.
    store.settings('global', {'max_output_tokens': 3000, 'thinking': 'off'})
    store.settings('paper:reading', {'max_output_tokens': 8192})
    reopened = ModelStore(directory)
    assert reopened.read()['defaults']['max_output_tokens'] == 3000
    assert reopened.read()['defaults']['thinking'] == 'off'
    assert reopened.read()['overrides']['paper:reading']['max_output_tokens'] == 8192
