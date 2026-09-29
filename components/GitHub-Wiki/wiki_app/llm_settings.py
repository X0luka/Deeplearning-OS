"""Product-local conversation overrides over the platform configuration contract."""
import json
import re

from fastapi import Request

from . import llm_client


def local_override(store, task, session=''):
    base = json.loads(store.meta('llm:'+task) or '{}')
    return {**base, **json.loads(store.meta('llm:'+task+':'+session) or '{}')} if session else base


def effective(store, task='chat', session='', execution=False):
    return llm_client.resolve(task, local_override(store, task, session), execution)


def install(app, store, tasks):
    def identity(task, session):
        if task not in tasks or not re.fullmatch(r'[a-zA-Z0-9_-]{0,100}', session):
            raise ValueError('invalid_settings_scope')
        return 'llm:'+task+(':'+session if session else '')

    @app.get('/api/llm/settings')
    def read(task: str = 'chat', session: str = ''):
        key = identity(task, session)
        if not llm_client.managed():
            return {'managed':False}
        try:
            data = llm_client.catalog()
            try:
                current = effective(store, task, session)
                error = None
            except ValueError as exc:
                current, error = {}, str(exc)
            return {'managed':True, 'host':llm_client.host_url(), 'connections':data['connections'],
                    'effective':llm_client.public(current), 'error':error,
                    'override':json.loads(store.meta(key) or '{}')}
        except ValueError as exc:
            return {'managed':True, 'host':llm_client.host_url(), 'connections':[],
                    'effective':{}, 'override':{}, 'error':str(exc)}

    @app.post('/api/llm/settings')
    async def save(request: Request):
        if not llm_client.managed():
            raise ValueError('platform_mode_required')
        value = await request.json()
        task, session = value.get('task', 'chat'), value.get('session', '')
        key = identity(task, session)
        override = value.get('override', {})
        if not isinstance(override, dict) or set(override)-{
                'selection','max_output_tokens','thinking','temperature','tools',
                'web_search','document_input'}:
            raise ValueError('invalid_settings')
        merged = {**(local_override(store, task) if session else {}), **override}
        if override:
            llm_client.resolve(task, merged)
        store.set_meta(key, json.dumps(override))
        return read(task, session)
