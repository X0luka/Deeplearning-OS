import hashlib
import json

from conftest import tiny_pdf
from fastapi.testclient import TestClient

from paper_reader.backup import backup, restore
from paper_reader.web import create_app


def test_reading_bound_integrity_and_backup(tmp_path):
    app=create_app(tmp_path/'reader');store=app.state.store
    doc=store.import_pdf(tiny_pdf(tmp_path/'fixture.pdf', [[(50,740,'Reading fixture')]]))
    identity=doc['id'];root=store.root/'reading'/identity;root.mkdir(parents=True)
    bundle=json.dumps({'original':'<p>Original</p>','translated':'<p>译文</p>'}).encode()
    (root/'bundle.json').write_bytes(bundle)
    (root/'image-000.png').write_bytes(b'test image')
    manifest={'document':identity,'bundle_sha256':hashlib.sha256(bundle).hexdigest(),
              'assets':{'image-000.png':hashlib.sha256(b'test image').hexdigest()}}
    (root/'manifest.json').write_text(json.dumps(manifest))
    with TestClient(app) as client:
        result=client.get(f'/api/documents/{identity}/reading')
        assert result.status_code==200 and result.json()['translated']=='<p>译文</p>'
        assert client.get(f'/api/documents/{identity}/reading/assets/image-000.png').content==b'test image'
        assert client.get(f'/api/documents/{identity}/reading/assets/bundle.json').status_code==404
        assert client.get('/api/documents/invalid/reading').status_code==404
        (root/'bundle.json').write_bytes(b'corrupt')
        assert client.get(f'/api/documents/{identity}/reading').status_code==409
        (root/'bundle.json').write_bytes(bundle)
        (root/'image-000.png').write_bytes(b'changed')
        assert client.get(f'/api/documents/{identity}/reading/assets/image-000.png').status_code==409
        (root/'image-000.png').write_bytes(b'test image')
    saved=backup(store.root,tmp_path/'backup')
    restored=restore(saved,tmp_path/'restored')
    assert (restored/'reading'/identity/'bundle.json').read_bytes()==bundle


def test_missing_reading_does_not_call_model(tmp_path):
    app=create_app(tmp_path/'reader');store=app.state.store
    doc=store.import_pdf(tiny_pdf(tmp_path/'fixture.pdf', [[(50,740,'Reading fixture')]]))
    with TestClient(app) as client:
        assert client.get(f"/api/documents/{doc['id']}/reading").json()=={'available':False}
