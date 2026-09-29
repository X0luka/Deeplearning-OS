"""AgentOS configuration v1 client and portable model transport.

Kept independently installable in each product; no cross-repository imports.
"""
import json
import os
import re
from urllib.parse import quote, urlsplit

import httpx


def managed():
    return os.getenv('AOS_LLM_MODE') == 'managed'


def host_url():
    url = os.getenv('AOS_LLM_URL', 'http://127.0.0.1:7777').rstrip('/')
    p = urlsplit(url)
    if p.scheme != 'http' or p.hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('invalid_configuration_host')
    return url


def request(path, body=None):
    token = os.getenv('AOS_LLM_TOKEN', '')
    product = os.getenv('AOS_LLM_PRODUCT', '')
    if not token or not product:
        raise ValueError('platform_configuration_missing')
    try:
        with httpx.Client(timeout=4, trust_env=False, follow_redirects=False) as client:
            response = client.request('GET' if body is None else 'POST',
                host_url()+'/internal/llm/v1/'+quote(product, safe='')+'/'+path,
                headers={'Authorization': 'Bearer '+token}, json=body)
        data = response.json()
        if response.status_code != 200:
            code = data.get('detail', 'platform_configuration_unavailable')
            raise ValueError(code if isinstance(code, str) and len(code)<90 else 'platform_configuration_unavailable')
        return data
    except (httpx.HTTPError, json.JSONDecodeError):
        raise ValueError('platform_configuration_unavailable') from None


def resolve(task='chat', override=None, execution=False):
    return request('resolve', {'task': task, 'override': override or {}, 'execution': execution})


def network():
    return request('network')


def catalog():
    return request('catalog')


def public(settings):
    return {k:v for k,v in settings.items() if k not in {'api_key', 'proxy', 'base_url'}}


def finish_error(reason):
    return 'model_finish_' + re.sub('[^a-z0-9_]', '', str(reason or 'missing').lower())[:60]


def thinking_config(model, mode):
    if mode in {'default', 'auto'}:
        return {}
    if mode == 'off' and not model.startswith(('gemini-3', 'gemini-2.5-pro')):
        return {'thinkingBudget': 0}
    raise ValueError('thinking_level_not_supported')


def model_request(settings, messages, tools=None, structured=False, stream=False):
    native = settings['protocol'] == 'gemini'
    base, model = settings['base_url'], settings['model']
    custom = settings.get('custom', '')
    messages = [dict(m) for m in messages]
    if custom:
        # User style preferences never replace the product's task contract.
        instruction = '\n用户回答偏好（仅在符合上述任务格式与引用要求时应用）：\n'+custom
        if messages and messages[0]['role'] == 'system':
            messages[0]['content'] += instruction
        else:
            messages.insert(0, {'role':'system', 'content': instruction})
    if native:
        contents, system = [], []
        for m in messages:
            role = m['role']
            if role == 'system':
                system.append({'text':m['content']})
                continue
            if role == 'tool':
                parts = [{'functionResponse': {'name': m.get('name', m['tool_call_id']),
                                               'response': {'result': m['content']},
                                               **({'id': m['_native_call_id']} if m.get('_native_call_id') else {})}}]
            elif m.get('_native_parts'):
                parts = m['_native_parts']
            else:
                parts = [{'text':m.get('content') or ''}]
            contents.append({'role':'model' if role == 'assistant' else 'user', 'parts':parts})
        config = {'maxOutputTokens':settings['max_output_tokens']}
        if settings.get('temperature') is not None:
            config['temperature'] = settings['temperature']
        thinking = thinking_config(model, settings.get('thinking', 'default'))
        if thinking:
            config['thinkingConfig'] = thinking
        if structured and settings['capabilities'].get('json') is True:
            config['responseMimeType'] = 'application/json'
        payload = {'contents':contents, 'systemInstruction':{'parts':system},
                   'generationConfig':config}
        if settings.get('web_search'):
            if tools:
                raise ValueError('search_tools_combination_unsupported')
            payload['tools'] = [{'google_search':{}}]
        elif tools:
            payload['tools'] = [{'functionDeclarations':[t['function'] for t in tools]}]
        method = 'streamGenerateContent?alt=sse' if stream else 'generateContent'
        return base+'/models/'+quote(model, safe='')+':'+method, {'x-goog-api-key':settings['api_key']}, payload
    if settings.get('web_search') or settings.get('document_input') == 'pdf':
        raise ValueError('native_feature_not_adapted')
    payload = {'model':model, 'messages':messages, 'max_tokens':settings['max_output_tokens'],
               'stream':stream}
    if settings.get('temperature') is not None:
        payload['temperature'] = settings['temperature']
    if tools:
        payload['tools'] = tools
    if structured and settings['capabilities'].get('json') is True:
        payload['response_format'] = {'type':'json_object'}
    thinking = settings.get('thinking', 'default')
    if thinking != 'default':
        hostname = urlsplit(base).hostname
        if hostname == 'api.deepseek.com':
            payload['thinking'] = {'type':'disabled' if thinking == 'off' else 'enabled'}
        elif hostname == 'openrouter.ai':
            payload['reasoning'] = {'enabled':thinking != 'off'}
        else:
            raise ValueError('thinking_parameter_not_adapted')
    return base+'/chat/completions', {'Authorization':'Bearer '+settings['api_key']}, payload


async def complete(settings, messages, tools=None, structured=False, transport=None):
    url, headers, payload = model_request(settings, messages, tools, structured)
    async with httpx.AsyncClient(timeout=90, trust_env=False, follow_redirects=False,  # noqa: SIM117 -- bounded stream scope
                                 proxy=settings.get('proxy'), transport=transport) as client:
        async with client.stream('POST', url, headers=headers, json=payload) as response:
            if response.status_code != 200:
                raise ValueError('provider_http_'+str(response.status_code))
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data)>2_000_000:
                    raise ValueError('model_response_too_large')
    try:
        result = json.loads(data)
        if settings['protocol'] == 'gemini':
            candidate = result['candidates'][0]
            if candidate.get('finishReason') != 'STOP':
                raise ValueError(finish_error(candidate.get('finishReason')))
            parts = candidate['content']['parts']
            message = {'role':'assistant', 'content': ''.join(p.get('text','') for p in parts if not p.get('thought')),
                       '_native_parts':parts}
            calls = [p['functionCall'] for p in parts if 'functionCall' in p]
            if calls:
                message['tool_calls'] = [{'id':c.get('id', c['name']), '_native_id':c.get('id'), 'type':'function', 'function':{
                    'name':c['name'], 'arguments':json.dumps(c.get('args',{}))}} for c in calls]
            return message
        choice = result['choices'][0]
        if choice.get('finish_reason') not in {'stop', 'tool_calls'}:
            raise ValueError(finish_error(choice.get('finish_reason')))
        return choice['message']
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ValueError('model_invalid_response') from None
