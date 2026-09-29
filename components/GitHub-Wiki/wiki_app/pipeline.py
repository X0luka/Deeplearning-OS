"""A read-only aggregate over the existing persisted generation-stage links."""
from fastapi import HTTPException

KINDS = {'build', 'architecture', 'knowledge', 'page_diagrams'}
ACTIVE = {'queued', 'running'}


def stages(store, identity):
    row = store.job(identity)
    if not row:
        raise HTTPException(404, 'job_not_found')
    seen = set()
    while row['kind'] in KINDS:
        if row['id'] in seen:
            raise HTTPException(409, 'pipeline_cycle')
        seen.add(row['id'])
        parent = store.one(
            "SELECT id FROM jobs WHERE json_extract(result,'$.next_job_id')=? ORDER BY created LIMIT 1",
            (row['id'],),
        )
        if not parent:
            break
        row = store.job(parent['id'])
    chain, seen = [], set()
    while row:
        if row['id'] in seen:
            raise HTTPException(409, 'pipeline_cycle')
        seen.add(row['id'])
        chain.append(row)
        following = (row.get('result') or {}).get('next_job_id')
        if not following:
            break
        row = store.job(following)
        if not row:
            raise HTTPException(409, 'pipeline_stage_missing')
    return chain


def retryable(row):
    return row['state'] in {'failed', 'cancelled', 'interrupted'} or (
        row['state'] == 'succeeded' and (row.get('result') or {}).get('partial')
    )


def status(store, identity):
    chain = stages(store, identity)
    active = next((s for s in chain if s['state'] in ACTIVE), None)
    last = chain[-1]
    state = active['state'] if active else last['state']
    if not active and state == 'succeeded' and any((s.get('result') or {}).get('partial') for s in chain):
        state = 'partial'
    version = next(((s.get('result') or {}).get('version_id') or s['args'].get('version')
                    for s in reversed(chain)
                    if (s.get('result') or {}).get('version_id') or s['args'].get('version')), None)
    return {'root_id': chain[0]['id'], 'state': state, 'stages': chain,
            'active_id': active['id'] if active else None, 'version_id': version,
            'retryable': not active and any(retryable(s) for s in chain)}


def install(app, store):
    @app.get('/api/jobs/{identity}/pipeline')
    async def get_pipeline(identity: str):
        return status(store, identity)

    @app.post('/api/jobs/{identity}/pipeline/cancel')
    async def cancel_pipeline(identity: str):
        chain = stages(store, identity)
        with store.db() as c:
            for row in chain:
                c.execute("UPDATE jobs SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' "
                          "ELSE state END WHERE id=? AND state IN ('queued','running')", (row['id'],))
        return {'job_id': chain[0]['id']}

    @app.post('/api/jobs/{identity}/pipeline/retry', status_code=202)
    async def retry_pipeline(identity: str):
        chain = stages(store, identity)
        if any(s['state'] in ACTIVE for s in chain):
            raise HTTPException(409, 'pipeline_still_running')
        row = next((s for s in chain if retryable(s)), None)
        if not row:
            raise HTTPException(409, 'job_not_retryable')
        args = dict(row['args'])
        if row['state'] == 'succeeded' and row['kind'] == 'knowledge':
            args['refresh'] = True
        return {'job_id': store.enqueue(row['kind'], args)}


def recent(store, limit=10):
    roots = store.rows(
        "SELECT j.id FROM jobs j WHERE NOT EXISTS (SELECT 1 FROM jobs p "
        "WHERE json_extract(p.result,'$.next_job_id')=j.id) "
        "ORDER BY created DESC LIMIT ?", (limit,))
    items = []
    for root in roots:
        p = status(store, root['id'])
        first = p['stages'][0]
        items.append({'id': p['root_id'], 'kind': 'build' if first['kind'] in KINDS else first['kind'],
                      'state': p['state'], 'error': p['stages'][-1]['error']})
    return items
