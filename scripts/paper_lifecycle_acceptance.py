"""Real public PDF lifecycle; never enables a model or runs paid translation."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/PaperReader"))
os.environ["PAPER_SOURCE_PROXY"] = "http://127.0.0.1:7897"
from fastapi.testclient import TestClient
from paper_reader.reading_sources import fetch
from paper_reader.web import create_app
from paper_reader.reader_store import ReaderStore
from paper_reader.store import Store
from paper_reader.backup import backup, restore

fixtures = ROOT / ".local/fixtures"
fixtures.mkdir(exist_ok=True)
pdf = fixtures / "arxiv-1706.03762.pdf"
if not pdf.exists():
    data, _, _ = fetch("https://arxiv.org/pdf/1706.03762")
    assert data.startswith(b"%PDF-")
    pdf.write_bytes(data)
data = pdf.read_bytes()
with tempfile.TemporaryDirectory(dir=ROOT / ".local/tmp", prefix="paper-lifecycle-") as tmp:
    base = Path(tmp)
    app = create_app(base / "reader")
    with TestClient(app) as client:
        token = app.state.store.meta("csrf")
        response = client.post("/api/documents", headers={"x-paper-token": token},
                               files={"file": ("public-paper.pdf", data, "application/pdf")})
        assert response.status_code == 201
        value = response.json()
        identity, session = value["document"]["id"], value["session"]["id"]
        pages = len(value["document"]["parsed"]["pages"])
        assert pages > 1
        assert client.get("/api/documents/"+identity+"/pdf").content == data
        app.state.store.position(session, 2, 1.5)
        app.state.store.save_note(session, "Synthetic acceptance note; no model used.",
                                 parse_sha256=value["document"]["parsed"]["parse_sha256"])
    reopened = ReaderStore(base / "reader")
    assert reopened.session(session)["page"] == 2
    assert reopened.notes(session)[0]["body"].startswith("Synthetic acceptance")
    backup(base / "reader", base / "backup")
    restore(base / "backup", base / "restored")
    restored = ReaderStore(base / "restored")
    assert restored.session(session)["page"] == 2
    assert (restored.root / "raw" / (identity+".pdf")).read_bytes() == data
    # Separate base-parser check; never mislabel this as the Docling reading pipeline.
    basic = Store(base / "basic")
    document = basic.import_pdf(pdf)
    assert document["state"] == "ready"
    found = basic.search(document["id"], "attention")
    assert found
    report = {"status": "passed", "public_pdf_sha256": hashlib.sha256(data).hexdigest(),
              "bytes": len(data), "pages": pages, "http_upload_original_retrieval": True,
              "position_notes_reopen": True, "backup_restore": True,
              "basic_parser_blocks": len(document["parsed"]["blocks"]),
              "basic_search_hits": len(found), "docling_pipeline_tested": False, "model_calls": 0}
(ROOT / ".local/preparation/paper-lifecycle-acceptance.json").write_text(json.dumps(report,indent=2))
print(json.dumps(report))
