"""Bounded metadata discovery only. Never generates text to probe models."""
import datetime
from urllib.parse import quote, urlsplit

import httpx


def model_item(identity, raw=None, protocol='chat'):
    raw = raw or {}
    params = raw.get('supported_parameters', [])
    methods = raw.get('supportedGenerationMethods', [])
    inputs = raw.get('architecture', {}).get('input_modalities', [])
    cap = {'text': None, 'tools': None, 'pdf': None, 'search': None, 'stream': None,
           'thinking': None, 'json': None, 'temperature': None}
    if params:
        cap.update(tools='tools' in params, json='response_format' in params,
                   thinking='reasoning' in params or 'reasoning_effort' in params,
                   temperature='temperature' in params)
    if methods:
        cap['text'] = 'generateContent' in methods
    if inputs and 'text' not in inputs:
        cap['text'] = False
    return {'id': identity, 'name': raw.get('displayName') or raw.get('name') or identity,
            'capabilities': cap, 'source': 'catalog' if raw else 'manual',
            'availability': 'not_verified',
            'limits': {'context_tokens': raw.get('context_length'),
                       'max_input_tokens': raw.get('inputTokenLimit'),
                       'max_output_tokens': raw.get('outputTokenLimit') or
                           (raw.get('top_provider') or {}).get('max_completion_tokens'),
                       'source': 'provider_catalog'}}


async def discover(store, identity, transport=None):
    conn = next((c for c in store.read()['connections'] if c['id'] == identity), None)
    if not conn:
        raise ValueError('connection_not_found')
    key = store.secret(conn.get('credential_ref', identity))
    if not key:
        raise ValueError('model_key_not_configured')
    native = conn['protocol'] == 'gemini'
    headers = {'x-goog-api-key': key} if native else {'Authorization': 'Bearer '+key}
    items, page = {}, None
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False,
                                     proxy=store.effective_proxy('models', conn['proxy']), transport=transport) as client:
            for _ in range(30):
                url = conn['base_url']+'/models'
                if native:
                    url += '?pageSize=1000'+('&pageToken='+quote(page, safe='') if page else '')
                elif page:
                    url += '?after='+quote(page, safe='')
                async with client.stream('GET', url, headers=headers) as response:
                    if response.status_code != 200:
                        raise ValueError('catalog_http_'+str(response.status_code))
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 8_000_000:
                            raise ValueError('catalog_too_large')
                import json
                payload = json.loads(data)
                rows = payload.get('models' if native else 'data')
                if not isinstance(rows, list):
                    raise ValueError('catalog_invalid_response')  # noqa: TRY004 -- public error code
                for raw in rows:
                    name = raw.get('name' if native else 'id', '')
                    if native:
                        name = name.removeprefix('models/')
                    if isinstance(name, str) and 0 < len(name) <= 250:
                        items[name] = model_item(name, raw, conn['protocol'])
                nxt = payload.get('nextPageToken') if native else (
                    payload.get('last_id') if payload.get('has_more') else None)
                if not nxt:
                    break
                if nxt == page:
                    raise ValueError('catalog_pagination_invalid')
                page = nxt
            else:
                raise ValueError('catalog_page_limit')
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, httpx.TimeoutException):
            code = 'catalog_timeout'
        elif isinstance(exc, httpx.ProxyError):
            code = 'catalog_proxy_error'
        elif isinstance(exc, httpx.ConnectError):
            code = 'catalog_connect_error'
        else:
            code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('catalog_') else 'catalog_unavailable'
        store.mutate(lambda s: next(c for c in s['connections'] if c['id'] == identity).update(error=code))
        raise ValueError(code) from None
    def save(state):
        current = next(c for c in state['connections'] if c['id'] == identity)
        if current['base_url'] != conn['base_url'] or current['protocol'] != conn['protocol']:
            raise ValueError('connection_changed_during_refresh')
        for old in current['models']:
            if old['source'] in {'manual', 'override', 'migration'}:
                items[old['id']] = old
        current.update(models=list(items.values()), error=None,
                       refreshed_at=datetime.datetime.now(datetime.UTC).isoformat())
    return store.mutate(save)


async def preview_connection(store, value, transport=None):
    """Query draft credentials without saving them or changing the current connection."""
    from .store import address, proxy_address
    protocol = value.get('protocol', 'chat')
    if protocol not in {'chat', 'gemini'}:
        raise ValueError('invalid_protocol')
    base = address(value.get('base_url', ''))
    proxy = value.get('proxy') or None
    if proxy:
        proxy = proxy_address(proxy)
    key = value.get('key')
    if key is None:
        old = next((c for c in store.read()['connections'] if c['id'] == value.get('id')), None)
        if not old:
            raise ValueError('model_key_not_configured')
        if old['base_url'] != base or old['protocol'] != protocol:
            raise ValueError('key_required_for_new_endpoint')
        key = store.secret(old.get('credential_ref', old['id']))
    if not isinstance(key, str) or not key.strip() or len(key)>8192 or any(c in key for c in '\r\n'):
        raise ValueError('model_key_not_configured')
    connection = {'id':'draft', 'base_url':base, 'protocol':protocol, 'proxy':proxy,
                  'models':[], 'error':None, 'refreshed_at':None}
    class Draft:
        def read(self): return {'connections':[connection]}
        def secret(self, identity): return key
        def effective_proxy(self, channel, connection_proxy):
            return store.effective_proxy(channel, connection_proxy)
        def mutate(self, operation):
            state = self.read()
            operation(state)
            return state
    result = await discover(Draft(), 'draft', transport)
    return result['connections'][0]['models']


def model_limits(connection, model):
    """Verified provider facts, scoped to the endpoint; never infer from a model name alone."""
    limits = dict(model.get('limits') or {})
    for key in ('context_tokens', 'max_input_tokens', 'max_output_tokens'):
        value = limits.get(key)
        limits[key] = value if type(value) is int and value > 0 else None
    if (urlsplit(connection['base_url']).hostname == 'api.deepseek.com'
            and connection['protocol'] == 'chat'
            and model['id'] in {'deepseek-flash', 'deepseek-v4-flash',
                                'deepseek-v4-flash-vision-exp', 'deepseek-v4-pro'}):
        limits.update(context_tokens=1048576, max_output_tokens=393216,
                      serving_model=('DeepSeek-V4-Pro-0813' if model['id'] == 'deepseek-v4-pro'
                                     else 'DeepSeek-V4.1-Flash'),
                      source='https://api-docs.deepseek.com/quick_start/pricing/',
                      output_source='https://api-docs.deepseek.com/api/create-chat-completion/',
                      verified_at='2026-09-24')
    return limits
