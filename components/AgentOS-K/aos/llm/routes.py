"""Separate browser management and authenticated product configuration routes."""
import secrets

from fastapi import HTTPException, Request

from .catalog import discover, model_item, preview_connection
from .store import validate_settings


def install(app, store, tasks, templates, check_write, products, network_channels=None):
    app.state.models = store
    setting_tasks = {**tasks, 'assistant': {'chat': '对话'}}

    def scope_ok(scope):
        if scope != 'global' and scope not in setting_tasks and scope not in {
                p+':'+t for p, ts in setting_tasks.items() for t in ts}:
            raise HTTPException(404, 'unknown_scope')

    async def body(request):
        raw = await request.body()
        if len(raw) > 64000:
            raise HTTPException(413, 'request_too_large')
        import json
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()  # noqa: TRY004 -- invalid JSON payload
            return result
        except ValueError:
            raise HTTPException(422, 'invalid_json') from None

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        from fastapi.responses import JSONResponse
        code = str(exc)
        return JSONResponse({'detail': code if code.replace('_', '').isalnum() and len(code)<90
                             else 'invalid_configuration'}, status_code=409)

    @app.get('/settings/models')
    async def page(request: Request):
        return templates.TemplateResponse(request=request, name='models.html',
                                          context={'products': products})

    @app.get('/api/v1/llm')
    async def state(request: Request):
        check_write(request)
        return {**store.public(), 'tasks': setting_tasks,
                'products': {**{p.id: p.name for p in products}, 'assistant': '统一助手'}}

    @app.get('/api/v1/llm/network')
    async def network_state(request: Request):
        check_write(request)
        return store.network()

    @app.put('/api/v1/llm/network')
    async def network_save(request: Request):
        check_write(request)
        return store.save_network(await body(request))

    @app.post('/api/v1/llm/connections')
    async def connection(request: Request):
        check_write(request)
        return store.save_connection(await body(request))

    @app.post('/api/v1/llm/connections/preview')
    async def preview(request: Request):
        check_write(request)
        return {'models': await preview_connection(store, await body(request))}

    @app.post('/api/v1/llm/connections/configure')
    async def configure(request: Request):
        check_write(request)
        value = await body(request)
        manual = value.pop('manual_model', False)
        if type(manual) is not bool:
            raise ValueError('invalid_connection')
        catalog = None
        if not value.get('clear_key'):
            if not value.get('selected_model'):
                raise ValueError('model_not_configured')
            if not manual:
                draft = dict(value)
                if not draft.get('key'):
                    draft.pop('key', None)
                catalog = await preview_connection(store, draft)
                if not any(m['id'] == value['selected_model'] for m in catalog):
                    raise ValueError('model_not_in_catalog')
        return store.save_connection(value, catalog=catalog)

    @app.delete('/api/v1/llm/connections/{identity}')
    async def remove(identity: str, request: Request):
        check_write(request)
        store.secret(identity)
        return store.delete_connection(identity)

    @app.post('/api/v1/llm/connections/{identity}/refresh')
    async def refresh(identity: str, request: Request):
        check_write(request)
        return await discover(store, identity)

    @app.post('/api/v1/llm/connections/{identity}/clear-key')
    async def clear_key(identity: str, request: Request):
        check_write(request)
        if not any(c['id'] == identity for c in store.read()['connections']):
            raise HTTPException(404, 'connection_not_found')
        return store.clear_key(identity)

    @app.post('/api/v1/llm/connections/{identity}/models')
    async def manual(identity: str, request: Request):
        check_write(request)
        value = await body(request)
        name = value.get('id', '')
        if not isinstance(name, str) or not 1 <= len(name) <= 250 or any(ord(c)<32 for c in name):
            raise ValueError('invalid_model_id')
        def change(state):
            conn = next((c for c in state['connections'] if c['id'] == identity), None)
            if not conn:
                raise ValueError('connection_not_found')
            old = next((m for m in conn['models'] if m['id'] == name), model_item(name))
            caps = value.get('capabilities', {})
            if not isinstance(caps, dict) or set(caps)-set(old['capabilities']):
                raise ValueError('invalid_capability')
            if any(v is not None and type(v) is not bool for v in caps.values()):
                raise ValueError('invalid_capability')
            old['capabilities'].update(caps)
            old['source'] = 'override' if caps else old['source']
            conn['models'] = [m for m in conn['models'] if m['id'] != name]+[old]
        return store.mutate(change)

    @app.put('/api/v1/llm/settings/{scope}')
    async def settings(scope: str, request: Request):
        check_write(request)
        scope_ok(scope)
        return store.settings(scope, await body(request))

    def product_auth(product, request):
        if product not in tasks:
            raise HTTPException(404, 'unknown_product')
        expected = store.product_token(product)
        if not secrets.compare_digest(request.headers.get('authorization', ''), 'Bearer '+expected):
            raise HTTPException(403, 'product_token_required')

    @app.get('/internal/llm/v1/{product}/network')
    async def product_network(product: str, request: Request):
        product_auth(product, request)
        channel = (network_channels or {}).get(product, product)
        effective = store.network()['effective']
        if channel not in effective:
            raise HTTPException(404, 'unknown_network_channel')
        return effective[channel]

    @app.get('/internal/llm/v1/{product}/catalog')
    async def catalog(product: str, request: Request):
        product_auth(product, request)
        state = store.public()
        # Every configured connection is available to an explicitly enrolled product.
        # A product cannot modify credentials, defaults, or another product's binding.
        return {'version': state['version'], 'connections': state['connections'],
                'defaults': state['defaults'], 'overrides': {
                    k:v for k,v in state['overrides'].items() if k==product or k.startswith(product+':')}}

    @app.post('/internal/llm/v1/{product}/resolve')
    async def resolve(product: str, request: Request):
        product_auth(product, request)
        value = await body(request)
        task = value.get('task', 'chat')
        if task not in tasks[product]:
            raise HTTPException(403, 'unknown_task')
        override = validate_settings(value.get('override', {}), True)
        if 'custom' in override:
            raise HTTPException(403, 'custom_is_global')
        return store.resolve(product, task, override, bool(value.get('execution', False)))
