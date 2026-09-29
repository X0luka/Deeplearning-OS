import json

import httpx
import pytest
from conftest import tiny_pdf
from fastapi.testclient import TestClient

from paper_reader.backup import backup, restore
from paper_reader.reader_store import ReaderStore
from paper_reader.translations import (
    TranslationStore,
    make_plan,
    protect_formulas,
    translate_segment,
)
from paper_reader.web import create_app

SETTINGS = {"source": "agentos", "connection": "fixture", "version": 1,
            "protocol": "openai", "model": "fixture-model", "max_output_tokens": 512,
            "thinking": "default", "temperature": None, "base_url": "https://fixture.test",
            "api_key": "never-store-this", "configured": True, "capabilities": {},
            "document_input": "text", "web_search": False}


@pytest.fixture
def sample(tmp_path):
    store = ReaderStore(tmp_path / "reader")
    doc = store.import_pdf(tiny_pdf(tmp_path / "sample.pdf", [
        [(50, 740-i*24, f"Line {i}. This is a complete synthetic sentence for translation.")
         for i in range(10)], [(50, 740, "Second page remains available after interruption.")]]))
    return store, doc, store.create_session(doc["id"])


def test_plan_covers_every_character_and_binds_versions(sample):
    _store, doc, _ = sample
    plan = make_plan(doc, SETTINGS)
    parsed = doc["parsed"]
    refs = {}
    for segment in plan["segments"]:
        assert len(segment["source"]) <= 256
        for ref in segment["refs"]:
            refs.setdefault(ref["block"], []).append((ref["start"], ref["end"]))
    for block in parsed["blocks"]:
        cursor = 0
        for start, end in refs[block["id"]]:
            assert start == cursor
            cursor = end
        assert cursor == len(block["text"])
    assert make_plan(doc, {**SETTINGS, "model": "other"})["id"] != plan["id"]
    assert make_plan(doc, {**SETTINGS, "connection": "other"})["id"] != plan["id"]
    changed = {**doc, "parsed": {**parsed, "parse_sha256": "changed"}}
    assert make_plan(changed, SETTINGS)["id"] != plan["id"]
    assert "never-store-this" not in json.dumps(plan)


def test_pause_replay_explicit_retry_and_budget(sample):
    store, doc, _ = sample
    tasks = TranslationStore(store)
    plan = make_plan(doc, SETTINGS)
    token = tasks.preview(plan)["token"]
    assert tasks.start(plan, token, False)
    assert not tasks.start(plan, token, False)
    first = tasks.claim(plan["id"])
    tasks.finish(plan["id"], first["seq"], {"state":"completed", "text":"中文第一段"})
    second = tasks.claim(plan["id"])
    tasks.finish(plan["id"], second["seq"], {"state":"uncertain", "error":"timeout"})
    assert not tasks.start(plan, token, True)  # A replay cannot silently buy a retry.
    preview = tasks.preview(plan)
    assert preview["remaining"] == preview["total"]-1
    assert preview["retry_count"] == 1
    with pytest.raises(ValueError, match="confirmation"):
        tasks.start(plan, preview["token"], False)
    tasks.start(plan, preview["token"], True)
    segment = tasks.claim(plan["id"])
    assert segment["seq"] == second["seq"] and segment["attempts"] == 1
    tasks.stop(plan["id"])
    tasks.finish(plan["id"], segment["seq"], {"state":"completed", "text":"已恢复"})
    assert tasks.claim(plan["id"]) is None
    result = tasks.get(plan["id"])
    assert result["state"] == "paused" and result["completed"] == 2
    assert result["reserved_calls"] == 3
    assert result["reserved_output"] == 1536
    assert result["segments"][0]["attempts"] == 1


def test_restart_retains_completed_and_marks_inflight_unknown(sample):
    store, doc, _ = sample
    tasks = TranslationStore(store)
    plan = make_plan(doc, SETTINGS)
    tasks.start(plan, tasks.preview(plan)["token"], False)
    first = tasks.claim(plan["id"])
    tasks.finish(plan["id"], first["seq"], {"state":"completed", "text":"保留"})
    tasks.claim(plan["id"])
    reopened = TranslationStore(ReaderStore(store.root))
    reopened.recover()
    result = reopened.get(plan["id"])
    assert result["state"] == "paused"
    assert result["segments"][0]["translated"] == "保留"
    assert result["segments"][1]["state"] == "uncertain"
    assert not reopened.busy()


def test_complete_never_reissues_and_budget_cannot_be_exceeded(sample):
    store, doc, _ = sample
    tasks = TranslationStore(store)
    plan = make_plan(doc, SETTINGS)
    tasks.start(plan, tasks.preview(plan)["token"], False)
    while segment := tasks.claim(plan["id"]):
        tasks.finish(plan["id"], segment["seq"], {"state":"completed", "text":"译文"})
    task = tasks.get(plan["id"])
    assert task["reserved_calls"] == len(plan["segments"]) == task["authorized_calls"]
    assert not tasks.start(plan, tasks.preview(plan)["token"], True)
    assert task["state"] == "completed"


def test_provider_complete_truncated_and_formula_integrity():
    original = "The equation is $y=x+F(x)$ and the result is 15."
    protected, formulas, prefix = protect_formulas(original)
    assert formulas == ["$y=x+F(x)$"]
    requests = []
    def response(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices":[{"message":{"content":"公式为 "+prefix+"0_END，结果为15。"},"finish_reason":"stop"}],"usage":{"total_tokens":20}})
    result = translate_segment(SETTINGS, original, httpx.MockTransport(response))
    assert result["state"] == "completed" and "$y=x+F(x)$" in result["text"]
    assert requests[0]["max_tokens"] == 512 and not requests[0].get("tools")
    assert requests[0]["messages"][1]["content"] == protected
    for finish, content in [("length", "部分译文"), ("stop", ""), ("stop", "公式丢失")]:
        transport = httpx.MockTransport(lambda r, content=content, finish=finish: httpx.Response(200, json={"choices":[{
            "message":{"content":content},"finish_reason":finish}]}))
        result = translate_segment(SETTINGS, original, transport)
        assert result["state"] == "incomplete"


def test_timeout_is_unknown_and_native_transport():
    def timeout(request):
        raise httpx.ReadTimeout("private information must not be stored")
    result = translate_segment(SETTINGS, "text", httpx.MockTransport(timeout))
    assert result == {"state":"uncertain", "error":"request_result_unknown"}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"candidates":[{
        "content":{"parts":[{"text":"秘密推理", "thought":True},{"text":"中文译文"}]},
        "finishReason":"STOP"}]}))
    result = translate_segment({**SETTINGS, "protocol":"gemini"}, "text", transport)
    assert result["text"] == "中文译文" and result["state"] == "completed"


def test_backup_restores_translations_and_originals(sample, tmp_path):
    store, doc, _ = sample
    tasks = TranslationStore(store)
    plan = make_plan(doc, SETTINGS)
    tasks.start(plan, tasks.preview(plan)["token"], False)
    segment = tasks.claim(plan["id"])
    tasks.finish(plan["id"], segment["seq"], {"state":"completed", "text":"备份译文"})
    tasks.claim(plan["id"])
    saved = backup(store.root, tmp_path / "backup")
    destination = restore(saved, tmp_path / "restored")
    restored = TranslationStore(ReaderStore(destination)).get(plan["id"])
    assert restored["segments"][0]["translated"] == "备份译文"
    assert restored["segments"][1]["state"] == "uncertain"
    assert (destination / "raw" / (doc["id"]+".pdf")).read_bytes() == (store.root / "raw" / (doc["id"]+".pdf")).read_bytes()


def test_retired_translation_endpoint_keeps_history_readable(sample):
    store, doc, session = sample
    tasks = TranslationStore(store)
    plan = make_plan(doc, SETTINGS)
    tasks.start(plan, tasks.preview(plan)["token"], False)
    segment = tasks.claim(plan["id"])
    tasks.finish(plan["id"], segment["seq"], {"state":"completed", "text":"历史译文"})
    tasks.stop(plan["id"])
    app = create_app(store.root)
    with TestClient(app) as client:
        headers={"x-paper-token":store.meta("csrf")}
        preview=client.get('/api/translations/preview',params={"session":session["id"]}).json()
        assert preview["plan"] is None
        body={"session":session["id"],"token":"0"*64}
        assert client.post('/api/translations',json=body).status_code==403
        assert client.post('/api/translations',json=body,headers=headers).status_code==410
        assert '历史译文' in client.get('/api/translations/'+plan['id']+'/export').text
        assert client.get('/translations').status_code==200


def test_a_reference_pages_available_without_model(tmp_path, monkeypatch):
    monkeypatch.delenv('AOS_LLM_MODE',raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    app=create_app(tmp_path/'reference')
    store=app.state.store
    doc=store.import_pdf(tiny_pdf(tmp_path/'source.pdf',[
        [(50,740,'First synthetic page')],[]]))
    session=store.create_session(doc['id'])
    with TestClient(app) as client:
        result=client.get('/api/translations/preview',params={'session':session['id']}).json()
        assert result['plan'] is None
        assert [p['page'] for p in result['pages']]==[1,2]
        assert client.get('/api/documents/'+doc['id']+'/pdf').content.startswith(b'%PDF')
        assert not result['tasks']


def test_a_translation_parameter_failure_precedes_network():
    calls=[]
    config={**SETTINGS,'protocol':'gemini','model':'gemini-3-pro-preview','thinking':'off'}
    result=translate_segment(config,'synthetic',httpx.MockTransport(lambda r:calls.append(r)))
    assert result=={'state':'failed','error':'thinking_level_not_supported'}
    assert calls==[]


def test_a_translation_cutoff_keeps_text_and_reason():
    config={**SETTINGS,'protocol':'gemini'}
    result=translate_segment(config,'synthetic',httpx.MockTransport(lambda r:httpx.Response(200,json={
        'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':'已收到的译文'}]}}]})))
    assert result['state']=='incomplete'
    assert result['text']=='已收到的译文'
    assert result['error']=='model_finish_max_tokens'
