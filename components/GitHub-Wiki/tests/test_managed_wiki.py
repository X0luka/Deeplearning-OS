import pytest

from wiki_app import chat, engine, llm_client
from wiki_app.architecture import knowledge, semantic


def settings():
    return {'source':'agentos','enabled':True,'configured':True,'key_env':'','api_key':'test',
            'base_url':'https://example.test','model':'new-model','tools':False}


async def test_all_model_entrypoints_use_platform_transport_and_keep_validation(monkeypatch):
    seen=[]
    async def fake(config,messages,tools=None,structured=False):
        seen.append((config['model'],structured,tools))
        return {'content':'{"claims":[{"text":"bad","evidence":["E99"]}]}'}
    monkeypatch.setattr(llm_client,'complete',fake)
    await chat.completion(settings(),[{'role':'user','content':'hello'}],True)
    with pytest.raises(ValueError,match='invalid_model_evidence'):
        await engine.model_answer(settings(),'topic',[])
    await semantic.request(settings(),{})
    await knowledge.request(settings(),'task',{})
    assert len(seen)==4 and seen[0]==('new-model',False,None)
    assert all(x[1] for x in seen[1:])


async def test_malformed_structured_output_never_becomes_success(monkeypatch):
    async def bad(*args,**kwargs):
        return {'content':'not valid JSON'}
    monkeypatch.setattr(llm_client,'complete',bad)
    with pytest.raises(ValueError):
        await engine.model_answer(settings(),'topic',[])
    with pytest.raises(ValueError):
        await semantic.request(settings(),{})
    with pytest.raises(ValueError):
        await knowledge.request(settings(),'task',{})


async def test_workbench_link_uses_current_instance(tmp_path, monkeypatch):
    import httpx
    from wiki_app.main import create_app
    monkeypatch.setenv("AOS_LLM_URL", "http://127.0.0.1:18777")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)),
                                base_url="http://testserver") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert 'href="http://127.0.0.1:18777"' in response.text
    assert 'href="http://127.0.0.1:7777"' not in response.text
