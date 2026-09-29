import hashlib
import json

import pytest
from conftest import tiny_pdf

from paper_reader.evidence import build_context
from paper_reader.paper_content import build, load, markdown
from paper_reader.reader_store import ReaderStore
from paper_reader.reading_assistant import render_answer
from paper_reader.reading_structure import assemble, prepare
from paper_reader.types import Question


def install_content(store, doc):
    source = {'document': doc['id'], 'kind': 'html', 'html_url': 'https://example.org/paper'}
    html = '<article><h1>Unified paper</h1><p>Correct equation <math alttext="x^2"><mi>x</mi><mn>2</mn></math> and evidence.</p><pre>return x ** 2</pre></article>'
    content, plan = build(prepare(html), source)
    translated = assemble(plan, {u['id']: u['text'] for u in plan['units']})
    folder = store.root / 'reading-jobs' / ('a' * 64)
    folder.mkdir(parents=True)
    (folder / 'content.json').write_text(json.dumps(content))
    (folder / 'manifest.json').write_text(json.dumps({'content_sha256': hashlib.sha256((folder / 'content.json').read_bytes()).hexdigest()}))
    pointer = store.root / 'reading' / doc['id']
    pointer.mkdir(parents=True)
    (pointer / 'current.json').write_text(json.dumps({'job': 'a' * 64}))
    return content, translated, folder


def test_reading_question_markdown_share_formula_and_code(tmp_path):
    store = ReaderStore(tmp_path / 'reader')
    doc = store.import_pdf(tiny_pdf(tmp_path / 'paper.pdf', [[(50, 700, 'WRONG OLD EXTRACTION')]]))
    content, translated, _ = install_content(store, doc)
    context = build_context(store, Question(document=doc['id'], mode='fulltext', question='Explain equation'))
    text = '\n'.join(b['text'] for b in context['evidence'])
    assert 'WRONG OLD EXTRACTION' not in text
    assert '$x^2$' in text and 'return x ** 2' in text
    assert '$x^2$' in markdown(content, translated)
    assert context['parse_sha256'] == content['parse_sha256']
    assert all('data-content-id="' + b['id'] + '"' in translated for b in content['blocks'])
    answer = render_answer('据论文[[b2:1-1]]', context)
    assert answer['anchors'][0]['target'] == {'kind': 'html', 'block': 'b2', 'revision': 'a' * 64}
    assert answer['anchors'][0]['page'] is None  # Never fabricate a PDF page for native HTML.


def test_content_integrity_failure_blocks_questions(tmp_path):
    store = ReaderStore(tmp_path / 'reader')
    doc = store.import_pdf(tiny_pdf(tmp_path / 'paper.pdf', [[(50, 700, 'Original')]]))
    _, _, folder = install_content(store, doc)
    with (folder / 'content.json').open('a') as f:
        f.write(' ')
    with pytest.raises(ValueError, match='reading_integrity_error'):
        load(store, doc['id'])


def test_prefix_compatibility_and_no_false_checked_status(tmp_path):
    store = ReaderStore(tmp_path / 'reader')
    doc = store.import_pdf(tiny_pdf(tmp_path / 'paper.pdf', [[(50, 700, 'Original')]]))
    install_content(store, doc)
    context = build_context(store, Question(document=doc['id'], mode='fulltext', question='Explain'))
    assert len(render_answer('[[证据id:b2:1-1]]', context)['anchors']) == 1
    assert render_answer('[[b2:1-99]]', context)['citation_integrity'] == 'partial'
    assert render_answer('[[unknown:1-1]]', context)['anchors'] == []
    assert render_answer('普通交流', context)['citation_integrity'] == 'not_cited'


def test_old_question_references_repaired_without_database_rewrite(tmp_path):
    store = ReaderStore(tmp_path / 'reader')
    doc = store.import_pdf(tiny_pdf(tmp_path / 'paper.pdf', [[(50, 700, 'Original evidence')]]))
    q = Question(document=doc['id'], mode='fulltext', question='Explain')
    context = build_context(store, q)
    ref = context['evidence'][0]['id']
    identity = store.enqueue(q)
    raw = '[[证据id:' + ref + ':1-1]]'
    store.set_job(identity, 'completed', context=context, answer={'markdown': raw})
    assert len(store.job(identity)['answer']['anchors']) == 1
    with store.db() as db:
        saved = json.loads(db.execute('SELECT answer FROM jobs WHERE id=?', (identity,)).fetchone()[0])
    assert saved['markdown'] == raw


def test_new_import_does_not_run_old_text_parser(tmp_path, monkeypatch):
    import paper_reader.store as storage
    def forbidden(*args):
        raise AssertionError('legacy parser must not run')
    monkeypatch.setattr(storage, 'parse_pdf', forbidden)
    store = ReaderStore(tmp_path / 'reader')
    pdf = tiny_pdf(tmp_path / 'paper.pdf', [[(50, 700, 'New input')]])
    doc = store.register_pdf(pdf)
    assert doc['state'] == 'registered' and doc['parsed']['blocks'] == []
    assert (store.root / 'raw' / (doc['id'] + '.pdf')).read_bytes() == pdf.read_bytes()


def test_translation_cache_changes_when_protected_formula_changes():
    from paper_reader.reading_jobs import translation_key
    a={"text":"Equation ⟦P0⟧", "pieces":{"⟦P0⟧":"<math>x</math>"}}
    b={"text":"Equation ⟦P0⟧", "pieces":{"⟦P0⟧":"<math>y</math>"}}
    assert translation_key(a) != translation_key(b)


def test_native_pdf_provenance_is_preserved_in_citation():
    source={"document":"d"*64,"kind":"pdf"}
    plan=prepare('<article><p data-source-ref="#/texts/1">Original PDF text.</p></article>')
    provenance={"pages":[{"page":3,"width":600,"height":800,"issues":[]}],
                "items":{"#/texts/1":[{"page":3,"bbox":[10,20,300,40]}]}}
    content,_=build(plan,source,provenance)
    block=dict(content["blocks"][0],document=source["document"])
    answer=render_answer('[[b1:1-1]]',{"evidence":[block],"parse_sha256":content["parse_sha256"],"coverage":{}})
    assert answer['anchors'][0]['target']['kind']=='pdf'
    assert answer['anchors'][0]['page']==3
    assert answer['anchors'][0]['bbox']==[10,20,300,40]


def test_native_table_caption_is_in_translation_plan():
    plan=prepare('<article><table><caption>Evaluation benchmarks.</caption><tr><td>Software tasks</td></tr></table></article>')
    assert any(u['text']=='Evaluation benchmarks.' for u in plan['units'])
    assert any(u['text']=='Software tasks' for u in plan['units'])


def test_http_never_sends_unmigrated_legacy_text(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from paper_reader.web import create_app
    monkeypatch.setenv("DEEPSEEK_API_KEY","offline-only")
    monkeypatch.delenv("AOS_LLM_MODE",raising=False)
    app=create_app(tmp_path/'reader')
    store=app.state.store
    doc=store.import_pdf(tiny_pdf(tmp_path/'paper.pdf',[[(50,700,'Legacy extracted text')]]))
    session=store.create_session(doc['id'])
    with TestClient(app) as client:
        response=client.post('/api/questions',headers={"x-paper-token":store.meta('csrf')},json={
            "session":session['id'],"request_key":"unmigrated-question",
            "parse_sha256":doc['parsed']['parse_sha256'],
            "question":{"document":doc['id'],"mode":"fulltext","question":"Explain"}})
        assert response.status_code==409
        assert response.json()['detail']=='paper_content_requires_processing'
        assert store.session_history(session['id'])==[]
