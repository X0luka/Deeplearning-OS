import json

from conftest import tiny_pdf
from fastapi.testclient import TestClient

from paper_reader.web import create_app


def test_html_source_is_separate_and_immutable_with_multiple_notes(tmp_path):
    app = create_app(tmp_path / 'data')
    store = app.state.store
    doc = store.import_pdf(tiny_pdf(tmp_path / 'sample.pdf', [[(50, 700, 'Synthetic text.')]]))
    session = store.create_session(doc['id'])
    source = {'kind': 'html', 'text': '选中的中文原文', 'version': 'v1', 'start_unit': 'p1',
              'end_unit': 'p1', 'start_offset': 0, 'end_offset': 8, 'view': 'zh'}
    payload = {'session': session['id'], 'body': '第一种理解', 'selection': None,
               'explanation': 'reader-note-source/v1\n' + json.dumps({'source': source, 'explanation': ''}, ensure_ascii=False),
               'parse_sha256': doc['parsed']['parse_sha256']}
    with TestClient(app) as client:
        headers = {'x-paper-token': store.meta('csrf')}
        first = client.post('/api/notes', json=payload, headers=headers)
        assert first.status_code == 201
        second = client.post('/api/notes', json={**payload, 'body': '第二种理解'}, headers=headers)
        assert second.status_code == 201
        assert first.json()['id'] != second.json()['id']
        edited = client.put('/api/notes/' + first.json()['id'], headers=headers,
                            json={**payload, 'body': '修订理解', 'revision': 1,
                                  'explanation': '不应改写原摘录'})
        assert edited.status_code == 200
        notes = client.get('/api/notes', params={'session': session['id']}).json()['items']
        assert len(notes) == 2
        assert {n['body'] for n in notes} == {'修订理解', '第二种理解'}
        assert all(json.loads(n['explanation'].split('\n', 1)[1])['source'] == source for n in notes)
        invalid = client.post('/api/notes', headers=headers,
                              json={**payload, 'explanation': 'x' * 30001})
        assert invalid.status_code == 422
