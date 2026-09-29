import json

import pytest

from wiki_app.store import Store


def test_request_replay_after_finish_and_conflict(tmp_path):
    store=Store(tmp_path)
    args={'version':'v','conversation':'c','question':'q','request_key':'same-request'}
    job=store.enqueue('chat',args)
    store.finish(job,'failed',error='provider_http_400')
    assert store.enqueue('chat',args)==job
    with pytest.raises(ValueError,match='request_key_conflict'):
        store.enqueue('chat',{**args,'question':'changed'})
    store.set_meta('execution:'+job,json.dumps({'model':'fixture','stage':'provider_request'}))
    assert store.job(job)['execution']['stage']=='provider_request'
    assert store.job(job)['error']=='provider_http_400'
