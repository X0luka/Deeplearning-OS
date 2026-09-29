import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from paper_reader import reading_jobs, reading_sources
from paper_reader.reading_response import TranslationError, decode_response, heading_prefixes
from paper_reader.reading_structure import assemble, prepare
from paper_reader.web import create_app


def reply(values):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'translations': values})}}]}


def settled(service, identity):
    for _ in range(150):
        task = service.get(identity)
        if task['state'] in {'failed', 'completed'}:
            return task
        time.sleep(.02)
    raise AssertionError('job did not finish')


def test_heading_prefix_is_structural_not_body_formula():
    plan = prepare('<article><h2><span class="ltx_tag ltx_tag_section">6. </span>Discussion</h2>'
                   '<p><span class="ltx_tag ltx_tag_section">7. </span>Body reference</p>'
                   '<h3>Nested <span class="ltx_tag ltx_tag_subsection">6.1.</span> title</h3>'
                   '<h3><span class="ltx_tag ltx_tag_subsection">6.2. </span>Use <math>x</math></h3></article>')
    prefixes = heading_prefixes(plan)
    assert prefixes == {'u0': '⟦P0⟧', 'u3': '⟦P0⟧'}
    with pytest.raises(TranslationError) as caught:
        decode_response(reply({'u0': '讨论', 'u1': '正文', 'u2': '嵌套标题', 'u3': '使用'}), plan['units'], prefixes)
    assert caught.value.valid == {'u0': '⟦P0⟧讨论'}
    assert len(caught.value.details['units']) == 3
    assert '6. </span>讨论' in assemble({**plan, 'units': plan['units'][:1]}, caught.value.valid)


@pytest.mark.parametrize('content,code', [
    ('{"translations":{"u0":"正文"}},"u1":"extra"}', 'translation_invalid_json'),
    ('{"translations":{"u0":"a","u0":"b"}}', 'translation_duplicate_key'),
    ('{"translations":[]}', 'translation_invalid_schema'),
    ('{"translations":{"wrong":"a"}}', 'paragraph_coverage_changed'),
])
def test_malformed_responses_are_explicit(content, code):
    with pytest.raises(TranslationError, match=code):
        decode_response({'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]},
                        [{'id': 'u0', 'text': 'Body', 'pieces': {}}])


def test_heading_empty_duplicate_and_formula_remain_invalid():
    plan = prepare('<article><h2><span class="ltx_tag ltx_tag_section">6. </span>Discussion</h2></article>')
    for value in ['', '   ', '⟦P0⟧⟦P0⟧讨论']:
        with pytest.raises(TranslationError):
            decode_response(reply({'u0': value}), plan['units'], heading_prefixes(plan))


def test_http_request_omits_heading_number_and_receipt_has_no_key(tmp_path, monkeypatch):
    plan = prepare('<article><h2><span class="ltx_tag ltx_tag_section">6. </span>Discussion</h2></article>')
    seen = []
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, headers, json):
            seen.append(json)
            class Response:
                status_code = 200
                def json(self):
                    return reply({'u0': '讨论'})
            return Response()
    monkeypatch.setattr(reading_jobs.httpx, 'Client', Client)
    path = tmp_path / 'receipt.json'
    result = reading_jobs.translate({'protocol': 'openai', 'base_url': 'https://example.invalid',
        'model': 'fixture', 'api_key': 'SECRET_FIXTURE', 'max_output_tokens': 16384,
        'capabilities': {'json': True}, '_heading_prefixes': heading_prefixes(plan), '_receipt_path': str(path)}, plan['units'])
    assert json.loads(seen[0]['messages'][1]['content'])[0]['text'] == 'Discussion'
    assert seen[0]['response_format'] == {'type': 'json_object'}
    assert result['translations']['u0'] == '⟦P0⟧讨论'
    assert 'SECRET_FIXTURE' not in path.read_text()


def setup_source(monkeypatch):
    source = '<article><p>First paragraph</p><p>Second paragraph</p></article>'
    monkeypatch.setattr(reading_sources, 'resolve', lambda url: {'pdf': None, 'html': source, 'html_url': url, 'url': url, 'name': 'Fixture'})
    monkeypatch.setattr(reading_sources, 'localize', lambda source, *args: (source, {}))
    monkeypatch.setattr(reading_jobs, 'settings_for_reading', lambda: {'max_output_tokens': 16384})


def test_partial_units_repair_locally_and_restart_does_not_repeat(tmp_path, monkeypatch):
    setup_source(monkeypatch)
    calls = []
    def translator(settings, units):
        calls.append([u['id'] for u in units])
        if len(calls) == 1:
            raise TranslationError('protected_marker_changed', valid={'u0': '第一段'},
                                   details={'units': [{'unit': 'u1', 'code': 'protected_marker_changed'}]})
        return {'translations': {u['id']: '第二段' for u in units}}
    monkeypatch.setattr(reading_jobs, 'translate', translator)
    with TestClient(create_app(tmp_path / 'reader')) as client:
        service = client.app.state.reading_jobs
        task = service.start('https://example.com/paper')
        result = settled(service, task['id'])
        assert result['done'] == 2 and result['state'] == 'completed'
        assert result['quality']['issues'] == 0
        service.start('https://example.com/paper')
        assert len(calls) == 2
    with TestClient(create_app(tmp_path / 'reader')) as client:
        service = client.app.state.reading_jobs
        assert len(calls) == 2
        service.resume(task['id'])
        assert settled(service, task['id'])['state'] == 'completed'
    assert calls == [['u0', 'u1'], ['u1']]


def test_legacy_reply_reused_and_digest_conflict_blocks(tmp_path, monkeypatch):
    setup_source(monkeypatch)
    def offline(settings, units):
        raise ValueError('provider_http_503')
    monkeypatch.setattr(reading_jobs, 'translate', offline)
    with TestClient(create_app(tmp_path / 'reader')) as client:
        service = client.app.state.reading_jobs
        task = service.start('https://example.com/paper')
        assert settled(service, task['id'])['state'] == 'failed'
        folder = service.root / task['id']
        plan = json.loads((folder / 'plan.json').read_text())
        assert (folder / 'batch-0000.started').read_text() == hashlib.sha256(json.dumps(plan['units'], sort_keys=True).encode()).hexdigest()
        (folder / 'attempt-0000-legacy.json').write_text(json.dumps({'status': 200, **reply({'u0': '第一段', 'u1': '第二段'})}))
        (folder / 'batch-0000.started').write_text('wrong-source')
        service.resume(task['id'])
        assert settled(service, task['id'])['error'] == 'translation_plan_changed'
        (folder / 'batch-0000.started').write_text(hashlib.sha256(json.dumps(plan['units'], sort_keys=True).encode()).hexdigest())
        service.resume(task['id'])
        assert settled(service, task['id'])['state'] == 'completed'


def test_lossless_exact_id_wrapper_relocation():
    units = [{'id': 'u0', 'text': 'First', 'pieces': {}}, {'id': 'u1', 'text': 'Second', 'pieces': {}}]
    raw = {'translations': {'u0': '第一段'}, 'u1': {'text': '第二段'}}
    data = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(raw)}}]}
    assert decode_response(data, units)['translations'] == {'u0': '第一段', 'u1': '第二段'}
    raw['u0'] = 'conflicting duplicate location'
    data['choices'][0]['message']['content'] = json.dumps(raw)
    with pytest.raises(TranslationError, match='paragraph_coverage_changed'):
        decode_response(data, units)


def test_cosmetic_word_hyphen_wrapper_is_optional_but_styles_and_math_are_not():
    from paper_reader.reading_structure import restore_unit
    plan = prepare('<article><p>Real<span class="ltx_text" id="word">-</span>time protection.</p></article>')
    assert restore_unit(plan['units'][0], '实时保护。') == '实时保护。'
    for source in ['Real<span class="ltx_text ltx_font_bold">-</span>time',
                   'Real<span class="ltx_text" style="color:red" title="meaning">-</span>time',
                   'Value <span class="ltx_text">-</span>1',
                   'Use <math><mi>x</mi></math>']:
        unit = prepare('<article><p>' + source + '</p></article>')['units'][0]
        with pytest.raises(ValueError, match='protected_marker_changed'):
            restore_unit(unit, '正文')
    with pytest.raises(ValueError, match='protected_marker_changed'):
        restore_unit(plan['units'][0], '实时⟦O0⟧保护')
