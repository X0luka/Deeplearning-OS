"""Paper title discovery uses source metadata without renaming immutable documents."""
import asyncio
import hashlib
import json

import httpx
from fastapi import FastAPI
from conftest import tiny_pdf
from test_unified_content import install_content

from paper_reader import reading_sources
from paper_reader.assistant_tools import install
from paper_reader.reader_store import ReaderStore


def test_declared_title_and_arxiv_heading():
    assert reading_sources.paper_title('<meta name="citation_title" content="Attention  Is All You Need">') == "Attention Is All You Need"
    assert reading_sources.paper_title('<h1 class="title"><span class="descriptor">Title:</span>Attention Is All You Need</h1>') == "Attention Is All You Need"
    assert reading_sources.paper_title("<title>arXiv error page</title>") == ""


def test_arxiv_keeps_confirmed_version_and_number_with_title(monkeypatch):
    monkeypatch.setattr(reading_sources, "public_url", lambda url: url)
    calls = []

    def fetch(url, *args):
        calls.append(url)
        if "/abs/" in url:
            return b'<meta name="citation_title" content="Attention Is All You Need"><p>1706.03762v7</p>', url, "text/html"
        if "/html/" in url:
            raise ValueError("source_http_404")
        return b"%PDF-fixture", url, "application/pdf"

    monkeypatch.setattr(reading_sources, "fetch", fetch)
    result = reading_sources.resolve("https://arxiv.org/abs/1706.03762")
    assert result["title"] == "Attention Is All You Need"
    assert result["name"] == "1706.03762v7.pdf"
    assert result["url"].endswith("1706.03762v7")
    assert len(calls) == 3


def test_catalog_title_aliases_reopen_and_authorization(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setenv("AOS_LLM_TOKEN", "title-fixture")
        store = ReaderStore(tmp_path / "reader")
        doc = store.import_pdf(tiny_pdf(tmp_path / "1706.03762v7.pdf", [[(50, 700, "fixture")]]))
        content, _, folder = install_content(store, doc)
        content["source"]["title"] = "Attention Is All You Need"
        content["source"]["url"] = "https://arxiv.org/pdf/1706.03762v7"
        (folder / "content.json").write_text(json.dumps(content))
        (folder / "manifest.json").write_text(json.dumps({
            "content_sha256": hashlib.sha256((folder / "content.json").read_bytes()).hexdigest()}))
        raw_path = store.root / "raw" / (doc["id"] + ".pdf")
        before = raw_path.read_bytes()
        # Read-time compatibility also works after reopening existing databases.
        reopened = ReaderStore(store.root)
        app = FastAPI()
        install(app, reopened)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
            assert (await client.get("/aos/assistant/v1/resources")).status_code == 403
            response = await client.get("/aos/assistant/v1/resources", headers={"Authorization": "Bearer title-fixture"})
            resource = next(r for r in response.json()["items"] if r["id"] == doc["id"])
            assert resource["title"] == "Attention Is All You Need"
            assert "1706.03762v7.pdf" in resource["aliases"]
            assert "https://arxiv.org/pdf/1706.03762v7" in resource["aliases"]
        with store.db() as db:
            assert db.execute("SELECT name FROM documents WHERE id=?", (doc["id"],)).fetchone()[0] == "1706.03762v7.pdf"
        assert raw_path.read_bytes() == before

    asyncio.run(run())


def test_old_artifact_heading_fallback_keeps_saved_name(tmp_path):
    store = ReaderStore(tmp_path / "reader")
    doc = store.import_pdf(tiny_pdf(tmp_path / "1234.56789.pdf", [[(50, 700, "fixture")]]))
    install_content(store, doc)
    result = store.document(doc["id"])
    assert result["display_title"] == "Unified paper"
    assert result["name"] == "1234.56789.pdf"
