import json
import time

import pytest
from conftest import tiny_pdf
from fastapi.testclient import TestClient

from paper_reader import reading_jobs, reading_sources
from paper_reader.backup import backup, restore
from paper_reader.reading_structure import assemble, prepare, restore_unit
from paper_reader.web import create_app

SOURCE = '''<article><h1>Example paper</h1><p>Run <span class="ltx_font_typewriter">model.eval()</span> before inference.</p>
<div class="ltx_algorithm"><p>for i in range(n):</p><p>  x += 1 # increment</p></div>
<p>Use <math><mi>x</mi><mo>+</mo><mn>1</mn></math> here.</p>
<pre>if x:\n    print("text")</pre><figure><img src="/image.png"><figcaption>Result diagram</figcaption></figure>
<table><tr><td>Speed <b>improves</b>.</td><td>2.3</td></tr></table></article>'''


def test_nested_code_pseudocode_math_and_images_never_sent():
    plan = prepare(SOURCE)
    sent = json.dumps([{k: u[k] for k in ('id', 'text')} for u in plan['units']])
    for protected in ('model.eval', 'range(n)', 'increment', 'print(', '<mi>', '/image.png'):
        assert protected not in sent
    output = assemble(plan, {u['id']: u['text'].replace('before inference', '在推理之前') for u in plan['units']})
    assert 'model.eval()' in output and 'range(n)' in output and '在推理之前' in output
    assert len(plan['protected']) == 5


def test_missing_duplicate_and_crossed_markers_rejected():
    plan = prepare(SOURCE)
    unit = next(u for u in plan['units'] if '⟦P' in u['text'])
    with pytest.raises(ValueError, match='protected_marker_changed'):
        restore_unit(unit, '正文')
    with pytest.raises(ValueError, match='protected_marker_changed'):
        restore_unit(unit, unit['text'] + next(iter(unit['pieces'])))
    unit = {'pieces': {'⟦O0⟧': '<b>', '⟦C0⟧': '</b>', '⟦O2⟧': '<i>', '⟦C2⟧': '</i>'}}
    with pytest.raises(ValueError, match='inline_structure_changed'):
        restore_unit(unit, '⟦O0⟧⟦O2⟧文字⟦C0⟧⟦C2⟧')


def test_untrusted_html_removed_and_translation_escaped():
    plan = prepare('<article><p onclick="steal()">Hello</p><script>evil()</script><iframe src="http://x"></iframe></article>')
    result = assemble(plan, {'u0': '<img src=x onerror=evil()>'})
    assert '<script' not in result and 'onclick=' not in result and '<img src=x' not in result


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://127.0.0.1:7777/', 'http://169.254.169.254/', 'https://user:pass@example.com', 'http://[::1]/'])
def test_private_url_rejected(url):
    with pytest.raises(ValueError):
        reading_sources.public_url(url)


def wait(service, identity):
    for _ in range(100):
        task = service.get(identity)
        if task['state'] in {'completed', 'awaiting_review', 'failed', 'paused'}:
            return task
        time.sleep(.02)
    raise AssertionError('task did not settle')


def test_html_job_dedup_resume_and_backup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(reading_sources, 'public_url', lambda x: x)
    monkeypatch.setattr(reading_sources, 'resolve', lambda url: {'pdf': None, 'html': SOURCE, 'html_url': url, 'url': url, 'name': 'Example'})
    monkeypatch.setattr(reading_sources, 'localize', lambda source, *args: (source, {}))
    monkeypatch.setattr(reading_jobs, 'settings_for_reading', lambda: {'max_output_tokens': 16384})
    def translator(settings, units):
        calls.append(len(units))
        return {'translations': {u['id']: u['text'] for u in units}}
    monkeypatch.setattr(reading_jobs, 'translate', translator)
    app = create_app(tmp_path / 'reader')
    with TestClient(app) as client:
        token = {'x-paper-token': app.state.store.meta('csrf')}
        assert client.post('/api/reading-jobs/url', json={'url': 'https://example.com/paper'}).status_code == 403
        first = client.post('/api/reading-jobs/url', json={'url': 'https://example.com/paper'}, headers=token).json()
        task = wait(app.state.reading_jobs, first['id'])
        assert task['state'] == 'completed', task
        completed_calls = list(calls)
        assert sum(calls) == task['total']
        again = client.post('/api/reading-jobs/url', json={'url': 'https://example.com/paper'}, headers=token).json()
        assert again['id'] == first['id'] and calls == completed_calls
        result = client.get('/api/documents/' + task['document'] + '/reading').json()
        assert result['available'] and result['structure_version']
        assert client.get('/health').json()['busy'] is False
    saved = backup(app.state.store.root, tmp_path / 'backup')
    restored = restore(saved, tmp_path / 'restored')
    assert (restored / 'reading-jobs' / task['id'] / 'bundle.json').is_file()
    assert (restored / 'raw' / (task['document'] + '.html')).is_file()


def test_failure_not_retried_until_explicit_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(reading_sources, 'resolve', lambda url: {'pdf': None, 'html': SOURCE, 'html_url': url, 'url': url, 'name': 'Example'})
    monkeypatch.setattr(reading_sources, 'localize', lambda source, *args: (source, {}))
    monkeypatch.setattr(reading_jobs, 'settings_for_reading', lambda: {'max_output_tokens': 16384})
    calls = []
    def failing(*args):
        calls.append(True)
        raise ValueError('provider_http_503')
    monkeypatch.setattr(reading_jobs, 'translate', failing)
    app = create_app(tmp_path / 'reader')
    with TestClient(app):
        service = app.state.reading_jobs
        task = service.start('https://example.com/paper')
        assert wait(service, task['id'])['state'] == 'failed'
        service.start('https://example.com/paper')
        assert len(calls) == 1
        monkeypatch.setattr(reading_jobs, 'translate', lambda settings, units: {'translations': {u['id']: u['text'] for u in units}})
        service.resume(task['id'])
        assert wait(service, task['id'])['state'] == 'completed'


def test_abstract_page_not_full_html():
    assert not reading_sources.full_html('<main><h1>A paper</h1><p>Abstract</p></main>')


def test_pdf_draft_requires_sample_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(reading_jobs, 'settings_for_reading', lambda: {'max_output_tokens': 16384})
    monkeypatch.setattr(reading_jobs, 'translate', lambda settings, units: {'translations': {u['id']: u['text'] for u in units}})
    def parser(pdf, folder, *args):
        (folder / 'source.html').write_text(SOURCE)
        (folder / 'assets.json').write_text('{}')
    monkeypatch.setattr(reading_jobs.reading_parse, 'parse', parser)
    app = create_app(tmp_path / 'reader')
    store = app.state.store
    document = store.import_pdf(tiny_pdf(tmp_path / 'paper.pdf', [[(50, 740, 'Example paper')]]))
    session = store.create_session(document['id'])
    with TestClient(app) as client:
        task = app.state.reading_jobs.start('pdf:' + document['id'], document['id'], session['id'])
        result = wait(app.state.reading_jobs, task['id'])
        assert result['state'] == 'awaiting_review'
        assert client.get('/api/documents/' + document['id'] + '/reading').json() == {'available': False}
        assert (store.root / 'reading-jobs' / task['id'] / 'bundle.json').is_file()
        draft = client.get('/reading-drafts/' + task['id'])
        assert draft.status_code == 200 and '待验收' in draft.text


def test_arxiv_missing_html_falls_back_to_same_version_pdf(monkeypatch):
    monkeypatch.setattr(reading_sources, 'public_url', lambda x: x)
    calls = []
    def fetch(url, *args):
        calls.append(url)
        if '/html/' in url:
            raise ValueError('source_http_404')
        return b'%PDF-test', url, 'application/pdf'
    monkeypatch.setattr(reading_sources, 'fetch', fetch)
    result = reading_sources.resolve('https://arxiv.org/abs/2407.16741v3')
    assert result['html'] is None and result['pdf'].startswith(b'%PDF-')
    assert calls == ['https://arxiv.org/html/2407.16741v3', 'https://arxiv.org/pdf/2407.16741v3']
