"""Opt-in DeepSeek node acceptance; BM25-only retrieval, no embedding/rerank calls.

Run with RAG's virtualenv, --allow-deepseek, and an interactive WSL environment.
Writes evidence under var/acceptance; never runs as part of default pytest.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RAG = ROOT.parent / "RAG-AGENT"
sys.path.insert(0, str(ROOT))


def serve(port, folder):
    import httpx
    import uvicorn
    from openai import AsyncOpenAI
    from rank_bm25 import BM25Okapi

    sys.path.insert(0, str(RAG))
    for key in (
        "OPENROUTER_API_KEY",
        "AIHUBMIX_API_KEY",
        "COHERE_API_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ):
        os.environ[key] = "disabled-in-node-acceptance"
    os.environ.update(
        LANGFUSE_TRACING_ENABLED="false",
        OTEL_SDK_DISABLED="true",
        DATA_DIR=str(folder),
        RAW_DIR=str(ROOT / "var/test-assets"),
        DB_PATH=str(folder / "rag.db"),
        BM25_INDEX_PATH=str(folder / "unused.pkl"),
        LOG_PATH=str(folder / "rag.log"),
        MAX_TOKENS_RESPONSE="512",
    )
    from src.api.main import app
    from src.api.routes import query
    from src.generation import llm
    from src.ingestion.chunker import chunk_documents
    from src.ingestion.loaders import auto_load
    from src.retrieval import SearchResult

    class DeepSeekOnly(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.AsyncHTTPTransport(
                retries=0, proxy=os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
            )

        async def handle_async_request(self, request):
            if request.url.scheme != "https" or request.url.host != "api.deepseek.com":
                raise RuntimeError("Provider outside this acceptance scope")
            return await self.inner.handle_async_request(request)

        async def aclose(self):
            await self.inner.aclose()

    llm._deepseek_client = AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com/v1",
        max_retries=0,
        timeout=60,
        http_client=httpx.AsyncClient(transport=DeepSeekOnly(), trust_env=False),
    )
    nodes = chunk_documents(auto_load(ROOT / "var/test-assets/1706.03762v7.pdf", "paper"), "paper")
    tokenize = lambda value: re.findall(r"[a-z0-9]+", value.lower())
    index = BM25Okapi([tokenize(node.text) for node in nodes])
    calls = 0

    async def retrieve(prompt):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise RuntimeError("Two-question acceptance limit reached")
        scores = index.get_scores(tokenize(prompt))
        selected = sorted(range(len(nodes)), key=lambda i: scores[i], reverse=True)[:4]
        return [
            SearchResult(str(i), nodes[i].text, float(scores[i]), nodes[i].metadata)
            for i in selected
            if scores[i] > 0
        ]

    query.retrieve_top_chunks = retrieve
    (folder / "retrieval.json").write_text(json.dumps({"chunks": len(nodes), "mode": "BM25-only"}))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


async def sync_and_check(url, folder):
    import httpx

    from aos.integrations.rag.http import RagHttpAdapter
    from aos.adapters.sqlite import SQLiteArchive
    from aos.application import SyncService
    from aos.domain import Product
    from aos.host.web import create_app

    archive = SQLiteArchive(folder / "archive.db")
    async with httpx.AsyncClient(base_url=url, timeout=20, trust_env=False) as client:
        service = SyncService("rag-agent", RagHttpAdapter(client), archive)
        imported = await service.run()
        replay = await service.run()
        assert imported >= 4 and replay == 0
        rows = archive.items()["items"]
        assert len(rows) == 2
        app = create_app(Product("rag-agent", "RAG", url, url), RagHttpAdapter(client), archive)
        async with httpx.AsyncClient(
            base_url="http://testserver", transport=httpx.ASGITransport(app)
        ) as host:
            response = await host.get("/api/v1/history")
            assert response.json()["total"] == 2
            for row in rows:
                detail = await host.get(f"/history/{row['archive_id']}")
                assert detail.status_code == 200
        return {
            "imported_events": imported,
            "replay_new_events": replay,
            "local_records": len(rows),
            "records": rows,
            "host_history_http_status": response.status_code,
        }


def run():
    import httpx
    from streamlit.testing.v1 import AppTest

    assert os.getenv("DEEPSEEK_API_KEY"), "DEEPSEEK_API_KEY missing in this shell"
    folder = ROOT / "var/acceptance" / ("deepseek-" + uuid4().hex[:8])
    folder.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    report = {
        "mode": "real DeepSeek + actual original UI; BM25-only test retrieval",
        "folder": str(folder),
        "pdf_sha256": hashlib.sha256(
            (ROOT / "var/test-assets/1706.03762v7.pdf").read_bytes()
        ).hexdigest(),
        "complete_original_rag": False,
    }
    with (folder / "server.log").open("w") as log:
        proc = subprocess.Popen(
            [sys.executable, __file__, "--server", str(port), str(folder)],
            cwd=folder,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(base_url=url, timeout=90, trust_env=False) as client:
                for _ in range(150):
                    try:
                        client.get("/aos/v1/describe").raise_for_status()
                        break
                    except httpx.HTTPError:
                        if proc.poll() is not None:
                            raise RuntimeError("source_startup_failed; inspect local server.log")
                        time.sleep(0.2)
                else:
                    raise RuntimeError("source_startup_timeout")
                os.environ["API_BASE"] = url
                at = AppTest.from_file(str(RAG / "ui/app.py"), default_timeout=90).run()
                assert not at.exception
                at.segmented_control[0].set_value("Cheap").run()
                questions = [
                    "How many identical layers are in the encoder and decoder stacks, and what is d_model? Cite the paper.",
                    "What exact total training cost in US dollars does this paper report? If it does not report dollars, say so. Cite what is actually reported.",
                ]
                for question in questions:
                    at.chat_input[0].set_value(question).run()
                    assert not at.exception, "original_ui_exception"
                events = client.get("/aos/v1/changes").json()["events"]
                latest = {}
                for event in events:
                    item = event["interaction"]
                    latest[item["record_uid"]] = item
                source = list(latest.values())
                report["source_records"] = source
                assert len(source) == 2
                assert all(x["source_channel"] == "product_ui" for x in source)
                report["archive"] = asyncio.run(sync_and_check(url, folder))
                for record in report["archive"]["records"]:
                    original = next(x for x in source if x["record_uid"] == record["record_uid"])
                    assert original["answer"] == record["answer"]
                    assert original["revision"] == record["revision"]
                    if original["status"] == "succeeded":
                        assert record["provider"] == "deepseek"
                assert all(x["status"] == "succeeded" for x in source), "source_query_failed"
                report["pipeline_status"] = "PASS"
        except Exception as exc:
            report["pipeline_status"] = "FAIL"
            report["error_type"] = type(exc).__name__
            raise
        finally:
            (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print("Evidence:", folder / "report.json")
            print("Pipeline:", report.get("pipeline_status"))
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        serve(int(sys.argv[2]), Path(sys.argv[3]))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--allow-deepseek", action="store_true", required=True)
        parser.parse_args()
        run()
