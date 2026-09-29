import hashlib
import io
import json
import zipfile

from conftest import tiny_pdf
from fastapi.testclient import TestClient

from paper_reader import paper_content
from paper_reader.reading_jobs import write_json
from paper_reader.reading_structure import prepare
from paper_reader.web import create_app


def test_exports_are_complete_portable_and_integrity_checked(tmp_path):
    app = create_app(tmp_path / 'reader')
    store = app.state.store
    doc = store.import_pdf(tiny_pdf(tmp_path / 'paper.pdf', [[(50, 740, 'Paper')]]))
    identity = 'a' * 64
    with TestClient(app) as client:
        with store.db() as db:
            db.execute("INSERT INTO reading_jobs(id,source,document,state,done,total) VALUES(?,?,?,'failed',0,1)", (identity, 'pdf:' + doc['id'], doc['id']))
        base = '/api/reading-jobs/' + identity + '/files/'
        assert client.get(base + 'html').status_code == 409
        assert client.get('/api/reading-jobs/' + identity + '/preview').status_code == 409
        assert client.get(base + 'secret').status_code == 404
        task = client.get('/api/documents/' + doc['id'] + '/reading-task').json()['task']
        assert task['id'] == identity
        folder = app.state.reading_jobs.root / identity
        folder.mkdir()
        source = {'document': doc['id'], 'kind': 'pdf'}
        html = '<article><h1>MCP 中文标题</h1><p>完整正文</p><figure><img src="/api/reading-jobs/' + identity + '/assets/image-0000.png"><figcaption>图一</figcaption></figure></article>'
        content, plan = paper_content.build(prepare(html), source)
        write_json(folder / 'content.json', content)
        write_json(folder / 'bundle.json', {'translated': plan['original'], 'css': '', 'source': source})
        image = b'fake-image-content'
        (folder / 'image-0000.png').write_bytes(image)
        manifest = {name + '_sha256': hashlib.sha256((folder / (name + '.json')).read_bytes()).hexdigest() for name in ('content', 'bundle')}
        manifest['assets'] = {'image-0000.png': hashlib.sha256(image).hexdigest()}
        write_json(folder / 'manifest.json', manifest)
        app.state.reading_jobs.update(identity, state='awaiting_review', done=1)
        preview = client.get('/api/reading-jobs/' + identity + '/preview').json()
        assert preview['available'] and preview['draft']
        assert client.get('/api/documents/' + doc['id'] + '/reading').json() == {'available': False}
        response = client.get(base + 'html')
        assert response.status_code == 200
        assert 'attachment;' in response.headers['content-disposition']
        assert 'data:image/png;base64,' in response.text and '待验收' in response.text
        assert '/assets/' not in response.text and '完整正文' in response.text
        response = client.get(base + 'markdown')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.read('images/image-0000.png') == image
            text = archive.read('中文通读.md').decode()
            assert 'images/image-0000.png' in text and '完整正文' in text
        (folder / 'image-0000.png').write_bytes(b'changed')
        assert client.get(base + 'html').status_code == 409
        assert client.get(base + 'markdown').status_code == 409
        (folder / 'image-0000.png').write_bytes(image)
        (folder / 'bundle.json').write_text(json.dumps({'translated': 'corrupted'}))
        assert client.get(base + 'html').status_code == 409
