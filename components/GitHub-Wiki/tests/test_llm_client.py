import json

import httpx
import pytest

from wiki_app import llm_client


def settings(protocol='chat'):
    return {'source':'agentos','protocol':protocol,'base_url':'https://provider.test/v1',
            'model':'new-model','api_key':'test-private-key','custom':'用中文',
            'max_output_tokens':700,'thinking':'default','temperature':None,
            'capabilities':{},'tools':False,'document_input':'text','web_search':False}


def test_generic_model_does_not_receive_vendor_parameters():
    url,_headers,body=llm_client.model_request(settings(),[{'role':'system','content':'仅输出JSON'}])
    assert url.endswith('/chat/completions')
    assert body['model']=='new-model'
    assert 'thinking' not in body and 'temperature' not in body and 'tools' not in body
    assert '仅输出JSON' in body['messages'][0]['content'] and '用中文' in body['messages'][0]['content']
    assert 'test-private-key' not in json.dumps(llm_client.public(settings()))


def test_native_tool_history_preserves_parts():
    s=settings('gemini')
    parts=[{'thoughtSignature':'opaque','functionCall':{'name':'read','args':{}}}]
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'q'},
              {'role':'assistant','content':'','_native_parts':parts},
              {'role':'tool','tool_call_id':'read','content':'evidence'}]
    _,_,body=llm_client.model_request(s,messages)
    assert body['contents'][1]['parts']==parts
    assert body['contents'][2]['parts'][0]['functionResponse']['name']=='read'


def test_configuration_unavailable_never_falls_back(monkeypatch):
    monkeypatch.setenv('AOS_LLM_MODE','managed')
    monkeypatch.setenv('AOS_LLM_TOKEN','token')
    monkeypatch.setenv('AOS_LLM_PRODUCT','product')
    monkeypatch.setenv('DEEPSEEK_API_KEY','old-key')
    def fail(*a,**kw):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx.Client,'request',fail)
    with pytest.raises(ValueError,match='platform_configuration_unavailable'):
        llm_client.resolve(execution=True)


def test_completion_formats_and_incomplete_output():
    import asyncio
    async def run():
        for protocol,response in [('chat',{'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]}),
                                  ('gemini',{'candidates':[{'content':{'parts':[{'text':'ok'}]},'finishReason':'STOP'}]})]:
            result=await llm_client.complete(settings(protocol),[{'role':'user','content':'q'}],transport=httpx.MockTransport(lambda r, response=response:httpx.Response(200,json=response)))
            assert result['content']=='ok'
        with pytest.raises(ValueError,match='model_finish_length'):
            await llm_client.complete(settings(),[],transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':'partial'},'finish_reason':'length'}]})))
    asyncio.run(run())


@pytest.mark.parametrize('reason', ['MAX_TOKENS', 'SAFETY', 'RECITATION'])
def test_a_native_finish_reason_survives(reason):
    import asyncio
    async def run():
        with pytest.raises(ValueError, match='model_finish_'+reason.lower()):
            await llm_client.complete(settings('gemini'), [], transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={'candidates':[{'finishReason':reason}]})))
    asyncio.run(run())


def test_a_native_tool_round_trip_keeps_name_id_and_signature():
    import asyncio
    parts=[{'thoughtSignature':'opaque-sig','functionCall':{'name':'read_repository','id':'call-17','args':{'query':'q'}}}]
    response={'candidates':[{'finishReason':'STOP','content':{'parts':parts}}]}
    message=asyncio.run(llm_client.complete(settings('gemini'), [], transport=httpx.MockTransport(
        lambda request: httpx.Response(200,json=response))))
    call=message['tool_calls'][0]
    assert call['id']=='call-17'
    reply={'role':'tool','tool_call_id':call['id'],'name':call['function']['name'],
           '_native_call_id':call['_native_id'],'content':'found'}
    _,_,body=llm_client.model_request(settings('gemini'), [message,reply])
    assert body['contents'][0]['parts']==parts
    assert body['contents'][1]['parts'][0]['functionResponse']=={
        'name':'read_repository','id':'call-17','response':{'result':'found'}}


def test_a_unsupported_thinking_fails_before_http():
    config={**settings('gemini'),'model':'gemini-3-pro-preview','thinking':'off'}
    with pytest.raises(ValueError,match='thinking_level_not_supported'):
        llm_client.model_request(config, [])
    assert llm_client.thinking_config('gemini-2.5-flash','off')=={'thinkingBudget':0}
    assert llm_client.thinking_config('gemini-3-pro-preview','default')=={}
