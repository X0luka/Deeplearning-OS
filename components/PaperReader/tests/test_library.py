import pytest
from conftest import tiny_pdf
from fastapi.testclient import TestClient

from paper_reader.web import create_app


@pytest.fixture
def library_client(tmp_path):
    app = create_app(tmp_path / 'reader')
    store = app.state.store
    docs = []
    for i in range(2):
        doc = store.import_pdf(tiny_pdf(tmp_path / 'same-name.pdf', [[(50, 700, f'Paper {i}')]]))
        docs.append(doc)
        store.create_session(doc['id'])
    store.create_session(docs[0]['id'])
    with TestClient(app) as client:
        yield client, store, docs, {'x-paper-token': store.meta('csrf')}


def test_pin_group_trash_restore_preserves_documents_sessions_and_notes(library_client):
    client, store, docs, headers = library_client
    identity = docs[0]['id']
    session = next(s for s in store.sessions() if s['document'] == identity)
    note_id = store.save_note(session['id'], 'Preserved note', parse_sha256=docs[0]['parsed']['parse_sha256'])
    raw = (store.root / 'raw' / (identity + '.pdf')).read_bytes()
    state = client.get('/api/state').json()
    assert len(state['library']['items']) == 2
    assert sum(len(f['sessions']) for f in state['library']['items']) == 3
    endpoint = '/api/library/' + identity + '/actions'
    assert client.post(endpoint, json={'action': 'pin'}).status_code == 403
    assert client.post(endpoint, json={'action': 'pin'}, headers=headers).status_code == 200
    assert client.get('/api/state').json()['library']['items'][0]['document'] == identity
    assert client.post(endpoint, json={'action': 'trash'}, headers=headers).status_code == 200
    deleted = client.get('/api/state').json()
    assert all(s['document'] != identity for s in deleted['sessions'])
    assert deleted['library']['trash'][0]['document'] == identity
    assert (store.root / 'raw' / (identity + '.pdf')).read_bytes() == raw
    assert store.notes(session['id'])[0]['id'] == note_id
    assert len(store.sessions()) == 3
    assert client.post(endpoint, json={'action': 'restore'}, headers=headers).status_code == 200
    restored = client.get('/api/state').json()
    assert len(restored['library']['items']) == 2 and not restored['library']['trash']
    assert restored['library']['items'][0]['pinned'] is True
    assert client.post(endpoint, json={'action': 'unpin'}, headers=headers).status_code == 200
    assert not any(f['pinned'] for f in client.get('/api/state').json()['library']['items'])


def test_active_task_blocks_trash_but_pin_and_other_files_remain_available(library_client):
    client, store, docs, headers = library_client
    with store.db() as db:
        db.execute("INSERT INTO reading_jobs(id,source,document,state) VALUES('active','{}',?,'translating')", (docs[0]['id'],))
    url = '/api/library/' + docs[0]['id'] + '/actions'
    response = client.post(url, json={'action': 'trash'}, headers=headers)
    assert response.status_code == 409 and response.json()['detail'] == 'library_document_busy'
    assert not client.get('/api/state').json()['library']['trash']
    assert client.post(url, json={'action': 'pin'}, headers=headers).status_code == 200
    assert client.post('/api/library/' + docs[1]['id'] + '/actions', json={'action': 'trash'}, headers=headers).status_code == 200


def test_trash_persists_and_explicit_new_session_restores(library_client):
    client, store, docs, headers = library_client
    identity = docs[0]['id']
    client.post('/api/library/' + identity + '/actions', json={'action': 'trash'}, headers=headers)
    from paper_reader.library import listing
    from paper_reader.reader_store import ReaderStore
    reopened = ReaderStore(store.root)
    assert listing(reopened)['trash'][0]['document'] == identity
    reopened.create_session(identity)
    assert not listing(reopened)['trash']
    assert client.post('/api/library/missing/actions', json={'action': 'pin'}, headers=headers).status_code == 404
    assert client.post('/api/library/' + identity + '/actions', json={'action': 'purge'}, headers=headers).status_code == 422


def test_rename_persists_and_preserves_identity_records_and_original(library_client):
    client, store, docs, headers = library_client
    identity = docs[0]['id']
    original = (store.root / 'raw' / (identity + '.pdf')).read_bytes()
    sessions = [s for s in store.sessions() if s['document'] == identity]
    note = store.save_note(sessions[0]['id'], 'my note', parse_sha256=docs[0]['parsed']['parse_sha256'])
    endpoint = '/api/library/' + identity + '/actions'
    response = client.post(endpoint, headers=headers, json={'action': 'rename', 'name': '  中文论文（精读）.pdf  '})
    assert response.status_code == 200
    assert client.get('/api/documents/' + identity).json()['name'] == '中文论文（精读）.pdf'
    renamed = [s for s in store.sessions() if s['document'] == identity]
    assert {s['id'] for s in renamed} == {s['id'] for s in sessions}
    assert all(s['title'] == '中文论文（精读）.pdf' for s in renamed)
    assert (store.root / 'raw' / (identity + '.pdf')).read_bytes() == original
    assert store.notes(sessions[0]['id'])[0]['id'] == note
    from paper_reader.reader_store import ReaderStore
    assert ReaderStore(store.root).document(identity)['name'] == '中文论文（精读）.pdf'
    for name in ['', '   ', 'x' * 181, '../other.pdf', 'bad\nname', 'bad\\name']:
        assert client.post(endpoint, headers=headers, json={'action': 'rename', 'name': name}).status_code == 422
    assert store.document(identity)['name'] == '中文论文（精读）.pdf'
