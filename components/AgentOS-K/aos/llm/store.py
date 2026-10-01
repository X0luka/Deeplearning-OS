"""Versioned configuration and private credentials, never prompt or answer storage."""
import copy
import json
import os
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from .catalog import model_limits


def address(value):
    p = urlsplit(value)
    if (p.scheme not in {'https', 'http'} or not p.hostname or p.username or p.password
            or p.query or p.fragment or (p.scheme == 'http' and p.hostname not in
                                        {'127.0.0.1', 'localhost', '::1'})):
        raise ValueError('invalid_api_address')
    return value.rstrip('/')


def proxy_address(value):
    """Explicit HTTP(S) proxies may live on the Windows host's private address.

    API endpoint rules remain unchanged. Embedded credentials and unsupported
    schemes are rejected rather than silently forwarded or ignored.
    """
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ValueError('invalid_proxy_address')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme in {'http', 'https'} and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment
                 and parsed.path in {'', '/'} and port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('invalid_proxy_address')
    return value.rstrip('/')


DEFAULTS = {'selection': None, 'custom': '', 'max_output_tokens': 32768,
            'thinking': 'default', 'temperature': None, 'tools': False,
            'web_search': False, 'document_input': 'text'}


def validate_settings(value, partial=False):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('invalid_settings')
    result = dict(value) if partial else {**DEFAULTS, **value}
    for k, v in result.items():
        if k == 'selection' and v is not None:
            if (not isinstance(v, dict) or set(v) != {'connection', 'model'}
                    or any(not isinstance(x, str) or not 1 <= len(x) <= 250 for x in v.values())):
                raise ValueError('invalid_model_selection')
        elif k == 'custom' and (not isinstance(v, str) or len(v) > 12000):
            raise ValueError('custom_too_long')
        elif k == 'max_output_tokens' and (type(v) is not int or v < 1):
            raise ValueError('invalid_output_limit')
        elif k == 'temperature' and v is not None:
            if type(v) not in {float, int} or not 0 <= v <= 2:
                raise ValueError('invalid_temperature')
        elif k == 'thinking' and v not in {'default', 'off', 'auto'}:
            raise ValueError('invalid_thinking')
        elif k in {'tools', 'web_search'} and type(v) is not bool:
            raise ValueError('invalid_toggle')
        elif k == 'document_input' and v not in {'text', 'pdf'}:
            raise ValueError('invalid_document_input')
    return result


class ModelStore:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root/'config.db'
        self.private = self.root/'private'
        self.private.mkdir(exist_ok=True, mode=0o700)
        self.private.chmod(0o700)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, body TEXT)')
            db.execute('INSERT OR IGNORE INTO config VALUES(1,?)', (json.dumps({
                'version': 1, 'connections': [], 'defaults': DEFAULTS, 'overrides': {}}),))
        self.path.chmod(0o600)

    def db(self):
        return sqlite3.connect(self.path, timeout=10)

    def read(self):
        with self.db() as db:
            return json.loads(db.execute('SELECT body FROM config WHERE id=1').fetchone()[0])

    def mutate(self, operation):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT body FROM config WHERE id=1').fetchone()[0])
            operation(state)
            state['version'] += 1
            db.execute('UPDATE config SET body=? WHERE id=1', (json.dumps(state),))
        return self.public()

    def network(self):
        from .network import defaults, effective
        state = self.read()
        return {"settings": state.get("network", defaults()),
                "effective": {name: effective(state, name) for name in ("models", "paper", "wiki")}}

    def save_network(self, value):
        from .network import validate
        parsed = validate(value)
        self.mutate(lambda state: state.update(network=parsed))
        return self.network()

    def effective_proxy(self, channel, connection_proxy=None):
        from .network import effective
        return effective(self.read(), channel, connection_proxy)["proxy"]

    def secret(self, identity):
        if not identity or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in identity):
            raise ValueError('invalid_identity')
        path = self.private/identity
        return path.read_text() if path.exists() else ''

    def write_secret(self, identity, value):
        self.secret(identity)
        path = self.private/identity
        temp = self.private/(identity+'-'+secrets.token_hex(6))
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(value)
        temp.replace(path)

    def product_token(self, identity):
        name = 'product-'+identity
        value = self.secret(name)
        if not value:
            value = secrets.token_urlsafe(32)
            self.write_secret(name, value)
        return value

    def public(self):
        state = self.read()
        for c in state['connections']:
            c['configured'] = bool(self.secret(c.get('credential_ref', c['id'])))
            c.pop('credential_ref', None)
            for model in c['models']:
                model['limits'] = model_limits(c, model)
        return state

    def save_connection(self, value):
        allowed = {'id', 'name', 'protocol', 'base_url', 'key', 'enabled', 'proxy'}
        if not isinstance(value, dict) or set(value)-allowed:
            raise ValueError('invalid_connection')
        identity = value.get('id') or secrets.token_hex(12)
        self.secret(identity)
        if not isinstance(value.get('name'), str) or not 1 <= len(value['name']) <= 100:
            raise ValueError('invalid_connection_name')
        if value.get('protocol') not in {'chat', 'gemini'}:
            raise ValueError('invalid_protocol')
        base = address(value.get('base_url', ''))
        proxy = value.get('proxy') or None
        if proxy:
            proxy = proxy_address(proxy)
        key = value.get('key', '')
        if not isinstance(key, str) or len(key) > 8192 or any(c in key for c in '\r\n'):
            raise ValueError('invalid_key')
        if not value.get('id') and not key.strip():
            raise ValueError('key_required')
        def change(state):
            old = next((c for c in state['connections'] if c['id'] == identity), None)
            if value.get('id') and old is None:
                raise ValueError('connection_not_found')
            if old and old['base_url'] != base and not key:
                raise ValueError('key_required_for_new_endpoint')
            item = {'id': identity, 'name': value['name'], 'protocol': value['protocol'],
                    'base_url': base, 'proxy': proxy, 'enabled': bool(value.get('enabled', True)),
                    'models': [], 'refreshed_at': None, 'error': None}
            if old and old['base_url'] == base and old['protocol'] == item['protocol']:
                item.update({k: old[k] for k in ['models', 'refreshed_at', 'error']})
            item['credential_ref'] = old.get('credential_ref', identity) if old else identity
            if key:
                item['credential_ref'] = identity+'-'+secrets.token_hex(8) if old else identity
                self.write_secret(item['credential_ref'], key)
            state['connections'] = [c for c in state['connections'] if c['id'] != identity]+[item]
        result = self.mutate(change)
        self.cleanup_credentials(identity)
        return result

    def cleanup_credentials(self, identity):
        """Remove only retired files for this connection under the config write lock."""
        self.secret(identity)  # Validate before constructing a filename prefix.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT body FROM config WHERE id=1').fetchone()[0])
            active = {c.get('credential_ref', c['id']) for c in state['connections']}
            for path in self.private.iterdir():
                suffix = path.name.removeprefix(identity+'-')
                owned = path.name == identity or (
                    path.name.startswith(identity+'-') and len(suffix) in {12, 16}
                    and all(c in '0123456789abcdef' for c in suffix))
                if owned and path.name not in active and path.is_file():
                    path.unlink()

    def clear_key(self, identity):
        self.secret(identity)
        def clear(state):
            conn = next((c for c in state['connections'] if c['id'] == identity), None)
            if conn is None:
                raise ValueError('connection_not_found')
            ref = identity+'-'+secrets.token_hex(8)
            self.write_secret(ref, '')
            conn['credential_ref'] = ref
        result = self.mutate(clear)
        self.cleanup_credentials(identity)
        return result

    def delete_connection(self, identity):
        def change(state):
            values = [state['defaults'], *state['overrides'].values()]
            if any((v.get('selection') or {}).get('connection') == identity for v in values):
                raise ValueError('connection_in_use')
            state['connections'] = [c for c in state['connections'] if c['id'] != identity]
        result = self.mutate(change)
        self.cleanup_credentials(identity)
        return result

    def settings(self, scope, value):
        parsed = validate_settings(value, scope != 'global')
        if scope != 'global' and 'custom' in parsed:
            raise ValueError('custom_is_global')
        def change(state):
            selection = parsed.get('selection')
            if selection:
                c = next((c for c in state['connections'] if c['id'] == selection['connection']), None)
                if not c or not any(m['id'] == selection['model'] for m in c['models']):
                    raise ValueError('model_not_in_catalog')
            effective = parsed if scope == 'global' else {
                **state['defaults'],
                **(state['overrides'].get(scope.split(':')[0], {}) if ':' in scope else {}),
                **parsed}
            selected = effective.get('selection')
            if selected:
                conn = next((c for c in state['connections']
                             if c['id'] == selected['connection']), None)
                model = next((m for m in conn['models'] if m['id'] == selected['model']), None) if conn else None
                maximum = model_limits(conn, model).get('max_output_tokens') if model else None
                if maximum and effective['max_output_tokens'] > maximum:
                    raise ValueError('model_output_limit_exceeded')
            if scope == 'global':
                state['defaults'] = parsed
            elif parsed:
                state['overrides'][scope] = parsed
            else:
                state['overrides'].pop(scope, None)
        return self.mutate(change)

    def resolve(self, product, task, override=None, include_secret=False):
        state = self.read()
        settings = {**state['defaults'], **state['overrides'].get(product, {}),
                    **state['overrides'].get(product+':'+task, {}),
                    **validate_settings(override or {}, True)}
        selected = settings.pop('selection')
        if not selected:
            raise ValueError('model_not_configured')
        conn = next((c for c in state['connections'] if c['id'] == selected['connection']), None)
        if not conn or not conn['enabled']:
            raise ValueError('connection_disabled')
        model = next((m for m in conn['models'] if m['id'] == selected['model']), None)
        if not model:
            raise ValueError('model_not_in_catalog')
        limits = model_limits(conn, model)
        if (limits.get('max_output_tokens') is not None
                and settings['max_output_tokens'] > limits['max_output_tokens']):
            raise ValueError('model_output_limit_exceeded')
        cap = model['capabilities']
        if cap.get('text') is False:
            raise ValueError('model_not_text_generation')
        for setting, capability in [('web_search', 'search'), ('tools', 'tools')]:
            if settings[setting] and (cap.get(capability) is False if setting == 'tools' else cap.get(capability) is not True):
                raise ValueError('unsupported_'+setting)
        if settings['document_input'] == 'pdf' and cap.get('pdf') is not True:
            raise ValueError('unsupported_pdf_input')
        if settings['thinking'] != 'default' and cap.get('thinking') is False:
            raise ValueError('unsupported_thinking')
        if settings['temperature'] is not None and cap.get('temperature') is False:
            raise ValueError('unsupported_temperature')
        key = self.secret(conn.get('credential_ref', conn['id']))
        result = {**settings, 'version': state['version'], 'connection': conn['id'],
                  'connection_name': conn['name'], 'model': model['id'],
                  'protocol': conn['protocol'], 'base_url': conn['base_url'],
                  'proxy': self.effective_proxy('models', conn['proxy']), 'capabilities': copy.deepcopy(cap),
                  'configured': bool(key), 'source': 'agentos', 'limits': limits}
        if include_secret:
            if not key:
                raise ValueError('model_key_not_configured')
            result['api_key'] = key
        return result
