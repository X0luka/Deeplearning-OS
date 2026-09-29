import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def tiny_pdf(path, pages):
    """Minimal synthetic PDF fixture, no real paper data or third-party asset."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for lines in pages:
        page_id = len(objects) + 1
        kids.append(f"{page_id} 0 R")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>"
            ).encode()
        )
        stream = "\n".join(
            f"BT /F1 10 Tf {x} {y} Td ({text}) Tj ET" for x, y, text in lines
        ).encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    data = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    data += (f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF").encode()
    path.write_bytes(data)
    return path


@pytest.fixture
def imported(tmp_path):
    from paper_reader.store import Store

    path = tiny_pdf(
        tmp_path / "sample.pdf",
        [
            [
                (50, 740, "1 Introduction"),
                (50, 715, "The model uses residual learning for optimization."),
                (50, 700, "This method learns a residual rather than the full mapping."),
                (50, 655, "2 Method"),
                (50, 630, "The output is y = x + F where F denotes the residual function."),
                (50, 590, "3 Experiments"),
                (50, 565, "The evaluation reports 15 units under the stated protocol."),
            ],
            [
                (50, 740, "4 Conclusion"),
                (50, 715, "These results apply only to the tested setting."),
            ],
        ],
    )
    store = Store(tmp_path / "data")
    document = store.import_pdf(path)
    assert document["state"] == "ready"
    return store, document, path


def publish_synthetic_content(store, document):
    """Prepare canonical content for HTTP lifecycle fixtures, without running OCR/models."""
    import hashlib
    import html
    import json

    from paper_reader.paper_content import build
    from paper_reader.reading_structure import prepare
    source={"document":document["id"],"kind":"pdf"}
    blocks=document["parsed"]["blocks"]
    text="<article>"+"".join('<p data-source-ref="'+b['id']+'">'+html.escape(b['text'])+'</p>' for b in blocks)+"</article>"
    provenance={"pages":document["parsed"]["pages"],"items":{b['id']:[{"page":b['page'],"bbox":b['bbox']}] for b in blocks}}
    content,_=build(prepare(text),source,provenance)
    identity=hashlib.sha256((document['id']+':synthetic').encode()).hexdigest()
    folder=store.root/'reading-jobs'/identity;folder.mkdir(parents=True,exist_ok=True)
    (folder/'content.json').write_text(json.dumps(content))
    (folder/'manifest.json').write_text(json.dumps({"document":document['id'],"content_sha256":hashlib.sha256((folder/'content.json').read_bytes()).hexdigest(),"reading_ready":False}))
    pointer=store.root/'reading'/document['id'];pointer.mkdir(parents=True,exist_ok=True)
    (pointer/'current.json').write_text(json.dumps({"job":identity}))
