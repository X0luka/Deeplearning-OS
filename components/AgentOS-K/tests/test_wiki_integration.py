import asyncio
import json

import httpx

from aos.adapters.sqlite import SQLiteArchive
from aos.application import SyncService
from aos.domain import Product, ProductError
from aos.host.web import create_app
from aos.integrations.rag.http import RagHttpAdapter
from aos.integrations.wiki.http import WikiHttpAdapter
from aos.worker import run_worker


def batch():
    item = {
        "record_uid": "wiki-record",
        "source_interaction_id": 1,
        "revision": 1,
        "source_channel": "product_ui",
        "conversation_id": "thread",
        "created_at": "2026-09-09",
        "updated_at": "2026-09-09",
        "status": "completed",
        "query": "startup?",
        "answer": "source evidence",
        "citations": [
            {
                "source": "main.py",
                "start": 1,
                "end": 2,
                "commit": "a" * 40,
                "url": "https://github.com/e/r/blob/" + ("a" * 40) + "/main.py#L1-L2",
                "text_preview": "def startup(): pass",
            }
        ],
    }
    return {
        "contract_version": "1.0",
        "instance_id": "wiki-instance",
        "export_epoch": "epoch",
        "events": [{"event_id": 1, "revision": 1, "operation": "upsert", "interaction": item}],
        "next_cursor": "next",
        "has_more": False,
    }


async def test_wiki_mapping_replay_and_product_filter(tmp_path):
    archive = SQLiteArchive(tmp_path / "archive.db")
    async with httpx.AsyncClient(
        base_url="http://wiki",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=batch())),
    ) as client:
        adapter = WikiHttpAdapter(client)
        service = SyncService("github-wiki", adapter, archive)
        assert await service.run() == 1
        assert await service.run() == 0
        assert archive.items(product="github-wiki")["total"] == 1
        assert archive.items(product="rag-agent")["total"] == 0
        row = archive.items()["items"][0]
        assert row["citations"][0]["text_preview"] == "def startup(): pass"
        try:
            await adapter.query("unsupported")
        except ProductError as exc:
            assert exc.code == "unsupported"
        else:
            raise AssertionError("Wiki cannot use the unversioned RAG query endpoint")


async def test_worker_dispatch_and_failure_isolation(tmp_path):
    archive = SQLiteArchive(tmp_path / "archive.db")
    calls = []

    class Fake:
        def __init__(self, product):
            self.product = product

        async def run(self):
            calls.append(self.product)
            if self.product == "rag":
                raise ProductError("unreachable")
            return 2

    services = {p: Fake(p) for p in ("rag", "wiki")}
    task = asyncio.create_task(run_worker(services, archive, tmp_path / "worker.lock"))
    try:
        for _ in range(100):
            if any(j["product"] == "wiki" and j["state"] == "succeeded" for j in archive.jobs()):
                break
            await asyncio.sleep(0.03)
        assert calls[:2] == ["rag", "wiki"]
        assert any(j["product"] == "rag" and j["error"] == "unreachable" for j in archive.jobs())
        assert any(j["product"] == "wiki" and j["imported"] == 2 for j in archive.jobs())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_multi_product_host_routes_and_citations(tmp_path):
    archive = SQLiteArchive(tmp_path / "archive.db")
    archive.apply("github-wiki", batch(), None)
    async with httpx.AsyncClient(
        base_url="http://upstream",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"})),
    ) as client:
        rag = Product("rag-agent", "RAG", "http://127.0.0.1:8000", "http://127.0.0.1:8501")
        wiki = Product("github-wiki", "Wiki", "http://127.0.0.1:8601", "http://127.0.0.1:8601")
        registry = {
            rag.id: (rag, RagHttpAdapter(client), None),
            wiki.id: (wiki, WikiHttpAdapter(client), None),
        }
        app = create_app(rag, registry[rag.id][1], archive, registry=registry)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as c:
            assert len((await c.get("/api/v1/apps")).json()) == 2
            page = (await c.get("/?product_id=github-wiki")).text
            assert "Wiki" in page and "startup?" in page
            assert (await c.get("/api/v1/history?product_id=rag-agent")).json()["total"] == 0
            detail = (await c.get("/history/1")).text
            assert "main.py" in detail and "查看源码" in detail and "行 1" in detail
            assert (await c.get("/products/github-wiki/open")).status_code == 200
            assert (await c.get("/products/missing/open")).status_code == 404


async def test_archive_backup_old_rows_unchanged(tmp_path):
    archive = SQLiteArchive(tmp_path / "archive.db")
    old = batch()
    old["instance_id"] = "rag-instance"
    archive.apply("rag-agent", old, None)
    before = json.dumps(archive.items(product="rag-agent"), sort_keys=True)
    archive.apply("github-wiki", batch(), None)
    assert json.dumps(archive.items(product="rag-agent"), sort_keys=True) == before


async def test_autostart_only_selected_product(tmp_path):
    from aos.application import ProductRuntime

    class Supervisor:
        calls = 0

        async def ensure_product(self):
            self.calls += 1

        async def ensure_worker(self):
            return "running"

        async def close(self):
            pass

    rag_super, wiki_super = Supervisor(), Supervisor()
    rag_runtime, wiki_runtime = ProductRuntime(rag_super), ProductRuntime(wiki_super)
    archive = SQLiteArchive(tmp_path / "archive.db")
    async with httpx.AsyncClient(
        base_url="http://upstream",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"})),
    ) as client:
        rag = Product("rag-agent", "RAG", "http://127.0.0.1:8000", "http://127.0.0.1:8501")
        wiki = Product("github-wiki", "Wiki", "http://127.0.0.1:8601", "http://127.0.0.1:8601")
        adapter = RagHttpAdapter(client)
        app = create_app(
            rag,
            adapter,
            archive,
            runtime=rag_runtime,
            registry={
                rag.id: (rag, adapter, rag_runtime),
                wiki.id: (wiki, WikiHttpAdapter(client), wiki_runtime),
            },
            autostart=("github-wiki",),
        )
        async with app.router.lifespan_context(app):
            await wiki_runtime.task
            assert wiki_super.calls == 1 and rag_super.calls == 0
