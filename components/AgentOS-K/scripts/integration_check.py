"""Cross-repository offline integration; executed with the RAG virtualenv Python."""
import asyncio
import os
import socket
import shutil
import hashlib
import sqlite3
from uuid import uuid4
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAG = Path(os.environ.get("RAG_REPO", str(ROOT.parent / "RAG-AGENT")))
sys.path.insert(0, str(ROOT))

import httpx
from streamlit.testing.v1 import AppTest

from aos.integrations.rag.http import RagHttpAdapter
from aos.adapters.sqlite import SQLiteArchive
from aos.application import SyncService
from aos.domain import Product
from aos.backup import snapshot
from aos.host.web import create_app


def fixture_pdf(path):
    text = "BT /F1 12 Tf 40 700 Td (The archive retains conversations across restarts.) Tj 0 -20 Td (Each product keeps its own history in durable storage.) Tj 0 -20 Td (The host imports records and feedback without duplicating them.) Tj ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
        "<< /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream"]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{i} 0 obj\n{obj}\nendobj\n".encode())
    pos = len(content)
    content.extend(f"xref\n0 6\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        content.extend(f"{offset:010} 00000 n \n".encode())
    content.extend(f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{pos}\n%%EOF".encode())
    path.write_bytes(content)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def archive_check(url, archive):
    async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=20) as client:
        adapter = RagHttpAdapter(client)
        service = SyncService("rag-agent", adapter, archive)
        imported = await service.run()
        assert imported > 0
        count = archive.items()["total"]
        assert count >= 1
        assert await service.run() == 0
        app = create_app(Product("rag-agent", "RAG", url, url), adapter, archive)
        async with httpx.AsyncClient(base_url="http://testserver",
                                    transport=httpx.ASGITransport(app)) as c:
            assert (await c.get("/")).status_code == 200
            assert (await c.get("/api/v1/history")).json()["total"] == count
        return count


def main():
    with tempfile.TemporaryDirectory(prefix="aos-integration-") as folder:
        work = Path(folder)
        raw = work / "data/raw"
        raw.mkdir(parents=True)
        fixture_pdf(raw / "fixture.pdf")
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        env = {**os.environ, "PYTHONPATH": str(RAG), "RAG_OFFLINE_TEST": "1",
               "DATA_DIR": str(work / "data"), "RAW_DIR": str(raw),
               "DB_PATH": str(work / "data/memory.db"),
               "BM25_INDEX_PATH": str(work / "data/processed/bm25.pkl"),
               "LOG_PATH": str(work / "data/app.log")}
        with (work / "server.log").open("w") as log:
            proc = subprocess.Popen([str(RAG / ".venv/bin/python"), "-m", "uvicorn",
                "tests.offline_server:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=work, env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=url, trust_env=False, timeout=20) as c:
                    for _ in range(150):
                        try:
                            c.get("/health").raise_for_status()
                            break
                        except httpx.HTTPError:
                            if proc.poll() is not None:
                                raise RuntimeError((work / "server.log").read_text()[-5000:])
                            time.sleep(.2)
                    else:
                        raise RuntimeError("fixture startup timed out")
                    result = c.post("/api/ingest", json={"path": str(raw / "fixture.pdf"),
                                                        "source_type": "paper"})
                    assert result.status_code == 200, result.text
                    assert result.json()["chunk_count"] > 0
                    os.environ["API_BASE"] = url
                    at = AppTest.from_file(str(RAG / "ui/app.py"), default_timeout=30).run()
                    assert not at.exception, at.exception
                    at.chat_input[0].set_value("What survives a restart?").run()
                    assert not at.exception, at.exception
                    records = c.get("/api/history").json()["items"]
                    assert records[0]["source_channel"] == "product_ui"
                    assert records[0]["status"] == "succeeded"
                    assert records[0]["citations"]
                    archive = SQLiteArchive(work / "archive.db")
                    count = asyncio.run(archive_check(url, archive))
                    # Host/worker absent while the actual original UI continues to produce history.
                    at.chat_input[0].set_value("Repeat the archival fact.").run()
                    assert not at.exception, at.exception
                    identity = c.get("/api/history").json()["items"][0]["id"]
                    c.post("/api/feedback", json={"interaction_id": identity, "feedback": 1}).raise_for_status()
                    reopened = SQLiteArchive(work / "archive.db")
                    assert asyncio.run(archive_check(url, reopened)) == count + 1
                    assert reopened.items()["items"][0]["feedback"] == 1
                    # Stop source writers before copying the complete local fixture bundle.
                    conversation = at.query_params["conversation"]
                    proc.terminate()
                    proc.wait(timeout=10)
                    restored_root = work / "restore"
                    restored_root.mkdir()
                    shutil.copytree(work / "data", restored_root / "data")
                    shutil.copytree(work / "qdrant-fixture", restored_root / "qdrant-fixture")
                    snapshot(work / "archive.db", restored_root / "archive.db")
                    original_hash = hashlib.sha256((raw / "fixture.pdf").read_bytes()).digest()
                    assert hashlib.sha256((restored_root / "data/raw/fixture.pdf").read_bytes()).digest() == original_hash
                    with sqlite3.connect(restored_root / "data/memory.db") as db:
                        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                        db.execute("UPDATE aos_meta SET value=? WHERE key='export_epoch'", (str(uuid4()),))
                    env.update(DATA_DIR=str(restored_root / "data"),
                               RAW_DIR=str(restored_root / "data/raw"),
                               DB_PATH=str(restored_root / "data/memory.db"),
                               BM25_INDEX_PATH=str(restored_root / "data/processed/bm25.pkl"),
                               LOG_PATH=str(restored_root / "data/app.log"))
                    proc = subprocess.Popen([str(RAG / ".venv/bin/python"), "-m", "uvicorn",
                        "tests.offline_server:app", "--host", "127.0.0.1", "--port", str(port)],
                        cwd=restored_root, env=env, stdout=log, stderr=log)
                    for _ in range(150):
                        try:
                            c.get("/health").raise_for_status()
                            break
                        except httpx.HTTPError:
                            if proc.poll() is not None:
                                raise RuntimeError((work / "server.log").read_text()[-5000:])
                            time.sleep(.2)
                    else:
                        raise RuntimeError("restore startup timed out")
                    fresh = AppTest.from_file(str(RAG / "ui/app.py"), default_timeout=30)
                    fresh.query_params["conversation"] = conversation
                    fresh.run()
                    assert not fresh.exception, fresh.exception
                    assert len(fresh.session_state["messages"]) == 4
                    fresh.chat_input[0].set_value("What does the restored archive retain?").run()
                    assert not fresh.exception, fresh.exception
                    restored_archive = SQLiteArchive(restored_root / "archive.db")
                    assert asyncio.run(archive_check(url, restored_archive)) == count + 2
                    print("PASS: stopped-source bundle restore + PDF hash + fresh UI thread + epoch reconciliation")
                    print("PASS: real PDF parser + local Qdrant fixture + original Streamlit widgets")
                    print("PASS: actual HTTP API + SQLite + adapter + offline catch-up + replay + feedback")
                    print("NOTE: paid providers are fixtures; no real-model quality claim")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


if __name__ == "__main__":
    main()
