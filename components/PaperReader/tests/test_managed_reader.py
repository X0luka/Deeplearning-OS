import json
import threading

import httpx
import pytest
from conftest import tiny_pdf

from paper_reader.llm_settings import effective, local_override
from paper_reader.reader_store import ReaderStore
from paper_reader.reading_assistant import ReadingAssistant
from paper_reader.types import Question


@pytest.mark.parametrize('stream',[True,False])
def test_new_managed_model_keeps_answer_budget_and_secret_private(tmp_path,stream):
    store=ReaderStore(tmp_path/'reader')
    doc=store.import_pdf(tiny_pdf(tmp_path/'paper.pdf',[[(50,700,'Example paper evidence.')]]))
    session=store.create_session(doc['id'])
    q=Question(document=doc['id'],mode='fulltext',question='Explain')
    identity,_=store.enqueue_session(q,session['id'],'managed-request')
    settings={'source':'agentos','model':'brand-new-chat','connection':'test','connection_name':'Test',
              'version':4,'protocol':'chat','base_url':'https://provider.test/v1','api_key':'sensitive-test-key',
              'custom':'使用中文','max_output_tokens':1000,'thinking':'default','temperature':None,
              'document_input':'text','web_search':False,'tools':False,'capabilities':{'stream':stream}}
    seen=[]
    def provider(req):
        body=json.loads(req.content);seen.append(body)
        assert body['model']=='brand-new-chat' and 'tools' not in body and 'thinking' not in body
        if stream:
            return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'这是说明。'},'finish_reason':'stop'}]})+'\n\n')
        return httpx.Response(200,json={'choices':[{'message':{'content':'这是说明。'},'finish_reason':'stop'}]})
    ReadingAssistant(store,settings,httpx.MockTransport(provider)).run(identity,threading.Event(),lambda *_:None)
    job=store.job(identity)
    assert job['state']=='completed' and len(seen)==1
    assert 'sensitive-test-key' not in json.dumps(job)
    assert job['context']['settings']['version']==4
    with store.db() as db:
        assert db.execute('SELECT sum(calls) FROM budget').fetchone()[0]==1


def test_conversation_override_inherits_without_key_persistence(tmp_path,monkeypatch):
    store=ReaderStore(tmp_path)
    assert local_override(store,'chat','session')=={}
    store.set_meta('llm:chat',json.dumps({'max_output_tokens':1000}))
    store.set_meta('llm:chat:session',json.dumps({'max_output_tokens':2000}))
    from paper_reader import llm_client
    monkeypatch.setattr(llm_client,'resolve',lambda task,override,execution:{'override':override})
    assert effective(store,session='session')['override']['max_output_tokens']==2000
    store.set_meta('llm:chat:session','{}')
    assert effective(store,session='session')['override']['max_output_tokens']==1000
