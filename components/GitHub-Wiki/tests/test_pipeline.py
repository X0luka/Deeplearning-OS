import asyncio
import re
import subprocess
from pathlib import Path

import httpx

from wiki_app.engine import worker
from wiki_app.main import create_app
from wiki_app.pipeline import recent, status
from wiki_app.store import Store


def chain(store):
    first = store.enqueue('build', {'repo': 'r'})
    second = store.enqueue('knowledge', {'version': 'v'})
    last = store.enqueue('page_diagrams', {'version': 'v'})
    store.finish(first, 'succeeded', {'version_id': 'v', 'next_job_id': second})
    store.finish(second, 'succeeded', {'version_id': 'v', 'partial': True,
                 'failures': [{'topic': {'title': 'core'}, 'failure': {'rule_id': 'QUALITY.UNSUPPORTED'}}],
                 'next_job_id': last})
    return first, second, last


async def test_chain_progress_and_cancel_from_completed_parent(tmp_path):
    app = create_app(tmp_path)
    first, second, last = chain(app.state.store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                               headers={'X-Wiki-Token': app.state.token}) as client:
        p = (await client.get('/api/jobs/'+second+'/pipeline')).json()
        assert p['root_id'] == first and p['active_id'] == last
        assert p['state'] == 'queued' and not p['retryable']
        assert p['stages'][1]['result']['failures'][0]['failure']['rule_id'] == 'QUALITY.UNSUPPORTED'
        assert len(recent(app.state.store)) == 1
        r = await client.post('/api/jobs/'+first+'/pipeline/retry')
        assert r.status_code == 409
        await client.post('/api/jobs/'+first+'/pipeline/cancel')
        assert app.state.store.job(last)['state'] == 'cancelled'
        assert app.state.store.job(second)['state'] == 'succeeded'


async def test_partial_completion_single_retry_and_restart(tmp_path):
    app = create_app(tmp_path)
    first, second, last = chain(app.state.store)
    app.state.store.finish(last, 'succeeded', {'version_id': 'v'})
    p = status(app.state.store, first)
    assert p['state'] == 'partial' and p['retryable'] and p['version_id'] == 'v'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                               headers={'X-Wiki-Token': app.state.token}) as client:
        a = (await client.post('/api/jobs/'+first+'/pipeline/retry')).json()['job_id']
        b = (await client.post('/api/jobs/'+first+'/pipeline/retry')).json()['job_id']
        assert a == b
        assert app.state.store.job(a)['kind'] == 'knowledge'
        assert app.state.store.job(a)['args']['refresh']
    app.state.store.claim()
    app.state.store.recover()
    assert status(Store(tmp_path), a)['state'] == 'interrupted'
    assert app.state.store.job(second)['result']['partial']


async def test_pipeline_writes_keep_auth_and_missing_job_errors(tmp_path):
    app = create_app(tmp_path)
    first, _, _ = chain(app.state.store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as c:
        assert (await c.post('/api/jobs/'+first+'/pipeline/cancel')).status_code == 403
        assert (await c.get('/api/jobs/missing/pipeline')).status_code == 404


async def test_cancel_at_completion_does_not_schedule_next_stage(tmp_path):
    store = Store(tmp_path)
    identity = store.enqueue('build', {'repo': 'r'})
    class FinishingEngine:
        def __init__(self):
            self.store = store
        async def run(self, row):
            with store.db() as c:
                c.execute('UPDATE jobs SET cancel=1 WHERE id=?', (row['id'],))
            return {'version_id': 'v'}
    task = asyncio.create_task(worker(FinishingEngine()))
    try:
        for _ in range(100):
            if store.job(identity)['state'] == 'cancelled':
                break
            await asyncio.sleep(.02)
        assert store.job(identity)['state'] == 'cancelled'
        assert len(store.rows('SELECT id FROM jobs')) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_task_page_script_parses():
    text = Path('wiki_app/templates/job.html').read_text()
    js = re.search(r'<script>(.*?)</script>', text, re.DOTALL).group(1)
    js = js.replace('{{ job.id|tojson }}', '"test-job"')
    js = re.sub(r'{{.*?}}', 'true', js)
    result = subprocess.run(['node', '--check'], input=js, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
