import io
import json
import zipfile

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from test_reading_pipeline import wait

from paper_reader import reading_jobs, reading_sources
from paper_reader.reading_response import TranslationError, decode_response
from paper_reader.reading_review import translate_group
from paper_reader.reading_structure import prepare
from paper_reader.web import create_app

SOURCE = """<article><h1>Independent fixture</h1><p>Questionable paragraph <math><mi>x</mi></math>.</p>
<p>Ambiguous term.</p><p>Successful paragraph.</p><table><tr><td>Table content</td><td>Final content</td></tr></table></article>"""


def response(content):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}]}


def test_direct_map_exact_only_and_missing_units_preserve_valid():
    units = prepare('<article><p>First sentence</p><p>Second sentence</p></article>')['units']
    assert decode_response(response({'u0': '一', 'u1': {'text': '二'}}), units)['translations'] == {'u0': '一', 'u1': '二'}
    with pytest.raises(TranslationError) as caught:
        decode_response(response({'translations': {'u0': '一'}}), units)
    assert caught.value.valid == {'u0': '一'}
    with pytest.raises(TranslationError):
        decode_response(response({'u0': '一', 'u1': '二', 'unknown': '内容'}), units)


def configure(monkeypatch):
    calls = []
    monkeypatch.setattr(reading_sources, 'resolve', lambda url: {'pdf': None, 'html': SOURCE, 'html_url': url, 'url': url, 'name': 'Independent fixture'})
    monkeypatch.setattr(reading_sources, 'localize', lambda source, *args: (source, {}))
    monkeypatch.setattr(reading_jobs, 'settings_for_reading', lambda: {'max_output_tokens': 16384})
    def translate(settings, units):
        calls.append([u['id'] for u in units])
        values = {u['id']: u['text'].replace('content', '内容').replace('paragraph', '段落') for u in units}
        if 'u2' in values:
            values['u2'] = {'text': '有歧义的术语。', 'review_note': '术语可能有多种解释 <script>unsafe()</script>'}
        if 'u1' in values:
            values['u1'] = '丢掉公式的坏译文'
        return decode_response(response({'translations': values}), units)
    monkeypatch.setattr(reading_jobs, 'translate', translate)
    return calls


def test_local_failure_finishes_with_visible_original_and_exports(tmp_path, monkeypatch):
    calls = configure(monkeypatch)
    app = create_app(tmp_path / 'reader')
    with TestClient(app) as client:
        service = app.state.reading_jobs
        task = service.start('https://example.com/independent-fixture')
        task = wait(service, task['id'])
        assert task['state'] == 'completed', task
        assert task['done'] == task['total'] == 6
        assert task['quality'] == {'total': 6, 'translated': 5, 'original': 1, 'review': 1, 'issues': 2}
        assert calls == [['u0', 'u1', 'u2', 'u3'], ['u1'], ['u1'], ['u4', 'u5']]
        folder = service.root / task['id']
        bundle = json.loads((folder / 'bundle.json').read_text())
        dom = BeautifulSoup(bundle['translated'], 'html.parser')
        assert dom.select_one('[data-reading-unit="u1"] math').get_text() == 'x'
        assert '已保留原文' in dom.select_one('[data-reading-unit="u1"]').get_text()
        assert dom.select_one('[data-reading-unit="u2"] details').get_text().endswith('Ambiguous term.')
        assert not dom.select('script')
        assert len(dom.select('td')) == 2
        html = client.get('/api/reading-jobs/' + task['id'] + '/files/html')
        assert html.status_code == 200 and '2处需核对' in html.text
        assert '1 单元保留原文' in html.text and 'reading-review-note' in html.text
        zipped = client.get('/api/reading-jobs/' + task['id'] + '/files/markdown')
        with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
            md = archive.read('中文通读.md').decode()
            assert '已保留原文' in md and '需核对原文' in md and 'Ambiguous term' in md
            assert len(json.loads(archive.read('核对清单.json'))) == 2
        service.resume(task['id'])
        assert len(calls) == 4
        # A different revision must not silently reuse a fallback as successful Chinese.
        assert json.loads((folder / 'batch-0000.json').read_text())['issues']['u1']['kind'] == 'original'
    with TestClient(create_app(tmp_path / 'reader')) as client:
        assert client.get('/api/reading-jobs/' + task['id']).json()['quality']['original'] == 1
        assert len(calls) == 4


def test_network_failures_never_turn_into_original_fallback():
    unit = {'id': 'u0', 'text': 'Some text', 'pieces': {}}
    partial, issues, attempts = {}, {}, {}
    def unavailable(units):
        raise ValueError('provider_http_401')
    for _ in range(4):
        with pytest.raises(ValueError, match='provider_http_401'):
            translate_group([unit], unavailable, partial, issues, attempts, lambda: None, lambda: None, 100)
    assert partial == {} and attempts['u0'] == 0


def test_pause_check_prevents_retry_and_preserves_first_success():
    units = [{'id': 'u0', 'text': 'First text', 'pieces': {}}, {'id': 'u1', 'text': 'Second text', 'pieces': {}}]
    partial, issues, attempts = {}, {}, {}
    checks = []
    def checkpoint():
        checks.append(True)
        if len(checks) > 1:
            raise ValueError('stopped')
    def incomplete(group):
        raise TranslationError('empty_translation', valid={'u0': '第一段'})
    with pytest.raises(ValueError, match='stopped'):
        translate_group(units, incomplete, partial, issues, attempts, checkpoint, lambda: None, 100)
    assert partial == {'u0': '第一段'} and attempts == {'u0': 1, 'u1': 1}
    calls = []
    def success(group):
        calls.extend(u['id'] for u in group)
        return {'translations': {'u1': '第二段'}}
    translate_group(units, success, partial, issues, attempts, lambda: None, lambda: None, 100)
    assert calls == ['u1'] and issues == {}


def test_oversized_unit_is_visible_fallback_without_sending():
    unit = {'id': 'u0', 'text': 'Long paragraph', 'pieces': {}}
    partial, issues = {}, {}
    def forbidden(group):
        raise AssertionError('oversized unit must not be sent')
    translate_group([unit], forbidden, partial, issues, {}, lambda: None, lambda: None, 3)
    assert partial['u0'] == unit['text']
    assert issues['u0']['code'] == 'paragraph_exceeds_model_request_capacity'
