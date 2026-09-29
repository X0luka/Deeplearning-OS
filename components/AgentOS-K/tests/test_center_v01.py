"""Read-only unified projection, snapshot drift and manual task lifecycle."""
import asyncio
import fcntl
import json

import httpx
import pytest
from test_archive import batch

from aos.adapters.sqlite import SQLiteArchive
from aos.application import ProductRuntime
from aos.assistant.store import Store
from aos.center import Center
from aos.domain import ProductError
from aos.host.web import create_app
from aos.manual_sync import ManualSync


def fixture(tmp_path):
    archive = SQLiteArchive(tmp_path / 'archive.db')
    archive.apply('alpha', batch(), None)
    assistant = Store(tmp_path / 'assistant' / 'assistant.db')
    conv = assistant.create('Imported', 'general', [])
    assistant.import_turn(conv['id'], {'question': 'stale', 'answer': 'stale'}, 'alpha:i1:u1')
    native = assistant.create('New chat', 'general', [])
    turn, _ = assistant.enqueue(native['id'], 'native-1', '<script>unsafe</script>', {})
    assistant.update(turn, state='completed', answer='今日 nanoGPT ' + 'x' * 1000)
    return archive, assistant, conv, native, turn


def test_identity_dedup_latest_archive_and_deletion(tmp_path):
    archive, assistant, conv, native, turn = fixture(tmp_path)
    center = Center(archive)
    before = assistant.rows('SELECT * FROM turns')
    assert center.items()['total'] == 2  # Identical text is not an identity.
    old = center.detail('a-1')
    assert old['key'].startswith('t-') and old['answer'] == 'answer'
    assert old['thread'] == conv['id']
    value = batch(2, cursor='c2')
    value['events'][0]['interaction']['answer'] = 'new authoritative answer'
    archive.apply('alpha', value, 'c1')
    assert center.detail(old['key'])['answer'] == 'new authoritative answer'
    assert center.items(q='今日 nanoGPT')['total'] == 1
    assert center.items(q="' OR 1=1 --")['total'] == 0
    assert center.detail('a-999') is None and center.detail('../archive.db') is None
    assert assistant.rows('SELECT * FROM turns') == before
    assistant.delete_conversation(conv['id'])
    assert center.detail('a-1') is None and center.detail(old['key']) is None
    assert center.items()['total'] == 1
    assert archive.detail(1)['answer'] == 'new authoritative answer'
    assistant.delete_conversation(native['id'])
    assert center.detail('t-' + turn) is None and center.items()['total'] == 0


def test_paging_projection_and_read_only_sql(tmp_path):
    archive, assistant, _, native, _ = fixture(tmp_path)
    for n in range(24):
        assistant.import_turn(native['id'], {'question': f'n{n}', 'answer': 'x' * 1000}, f'extra{n}')
    center = Center(archive)
    first, second = center.items(), center.items(page=2)
    assert first['total'] == 26 and len(first['items']) == 20 and len(second['items']) == 6
    assert not {r['key'] for r in first['items']} & {r['key'] for r in second['items']}
    assert all('answer' not in r and 'citations' not in r and len(r['preview']) <= 180
               for r in first['items'])
    import sqlite3
    with center.connection() as (db, attached):
        assert attached
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('DELETE FROM assistant.turns')


def test_snapshot_pending_changed_and_deleted(tmp_path):
    archive, assistant, conv, _, turn = fixture(tmp_path)
    center = Center(archive)
    old = center.detail('a-1')
    center.map_path.parent.mkdir()
    center.map_path.write_text(json.dumps({'generated': 'then', 'topics': [
        {'id': 'x', 'title': 'Topic', 'keys': [old['key']]}],
        'records': {old['key']: {'updated': old['updated'], 'warning': 'ambiguous'}}}))
    graph = center.map()
    assert graph['pending'] == ['t-' + turn] and graph['changed'] == []
    archive.apply('alpha', batch(2, cursor='c2'), 'c1')
    assert center.map()['changed'] == [old['key']]
    assistant.delete_conversation(conv['id'])
    graph = center.map()
    assert graph['topics'][0]['count'] == 0 and not graph['records']


async def test_api_safety_paging_and_manual_auth(tmp_path):
    archive, _, _, _, turn = fixture(tmp_path)
    app = create_app(None, None, archive, registry={})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as c:
        assert (await c.get('/api/v1/data/items?q=nanoGPT')).json()['total'] == 1
        assert (await c.get('/api/v1/data/items?page=0')).status_code == 422
        assert (await c.get('/api/v1/data/items?topic=missing')).status_code == 404
        page = await c.get('/data/item/t-' + turn)
        assert '&lt;script&gt;' in page.text and '<script>unsafe' not in page.text
        assert (await c.post('/api/v1/data/sync')).status_code == 403
        assert (await c.get('/api/v1/data/status')).json()['mode'] == 'manual'
    assert app.state.manual_sync.task is None and not archive.jobs()


async def test_manual_coalescing_failure_cancel_and_idle(tmp_path):
    archive = SQLiteArchive(tmp_path / 'archive.db')
    entered, release = asyncio.Event(), asyncio.Event()
    class Success:
        async def run(self):
            entered.set()
            await release.wait()
            return 3
    class Failure:
        async def run(self):
            raise ProductError('offline')
    sync = ManualSync(archive, {'a': Success(), 'b': Failure()})
    ids = sync.start(['a', 'b'])
    await entered.wait()
    assert sync.start(['a', 'b']) == ids and sync.start(['a']) == ids[:1]
    with pytest.raises(ProductError, match='sync_busy'):
        sync.start(['c'])
    release.set()
    await sync.task
    assert archive.job(ids[0])['imported'] == 3
    assert archive.job(ids[0])['state'] == 'succeeded'
    assert archive.job(ids[1])['state'] == 'failed'
    assert archive.job(ids[1])['error'] == 'offline'
    jobs = archive.jobs()
    await asyncio.sleep(.05)
    assert archive.jobs() == jobs
    entered.clear()
    release.clear()
    ids = sync.start(['a', 'b'])
    await entered.wait()
    await sync.close()
    assert all(archive.job(i)['state'] == 'interrupted' for i in ids)
    with (tmp_path / 'worker.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ProductError, match='archive_worker_busy'):
            sync.start(['a'])
    ids = sync.start(['a'])
    await sync.close()
    assert archive.job(ids[0])['state'] == 'interrupted'
    with (tmp_path / 'worker.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


async def test_runtime_manual_has_no_worker_supervisor():
    class Supervisor:
        async def ensure_worker(self):
            pytest.fail('must not start an archive worker')
        async def close(self):
            pass
    runtime = ProductRuntime(Supervisor(), supervise_worker=False)
    await runtime.start()
    await asyncio.sleep(0)
    assert runtime.worker_task is None and runtime.snapshot()['worker'] == 'manual'
    await runtime.close()
