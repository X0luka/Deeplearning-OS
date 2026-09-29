"""Manual products are inert until explicitly started, including cross-process work."""
import asyncio
import multiprocessing

import httpx
import pytest
from test_archive import batch

from aos.adapters.activity import ProductActivity
from aos.adapters.sqlite import SQLiteArchive
from aos.application import ProductRuntime, SyncService
from aos.domain import Product, ProductError
from aos.host.web import create_app
from aos.integrations.rag.http import RagHttpAdapter
from aos.worker import run_worker


class Supervisor:
    def __init__(self, failure=False):
        self.calls = 0
        self.failure = failure

    async def ensure_product(self):
        self.calls += 1
        if self.failure:
            raise ProductError("rag_api_environment_missing")

    async def ensure_worker(self):
        return "running"

    async def close(self):
        pass


async def test_disabled_http_ui_jobs_and_restart(tmp_path):
    store = SQLiteArchive(tmp_path / "archive.db")
    store.apply("rag-agent", batch(), None)
    original = store.items(product="rag-agent")
    legacy = store.enqueue("rag-agent")
    gate = ProductActivity(store, "rag-agent")
    supervisor = Supervisor()
    rt = ProductRuntime(supervisor, gate)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(base_url="http://rag", transport=httpx.MockTransport(respond)) as upstream:
        product = Product("rag-agent", "RAG", "http://127.0.0.1:8000", "http://127.0.0.1:8501")
        app = create_app(product, RagHttpAdapter(upstream), store, allow_query=True, runtime=rt,
                         autostart=("rag-agent",))
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            base_url="http://testserver", transport=httpx.ASGITransport(app)
        ) as c:
            headers = {"X-AOS-Token": app.state.write_token}
            for _ in range(3):
                assert 'role="switch"' in (await c.get("/")).text
                page = (await c.get("/products/rag-agent/open")).text
                assert '\nstartProduct();' not in page
                assert (await c.get("/api/v1/apps/rag-agent/health")).json()["status"] == "disabled"
                assert (await c.post("/api/v1/apps/rag-agent/sync", headers=headers)).status_code == 409
                assert (await c.post("/api/v1/apps/rag-agent/invoke", headers=headers,
                                     json={"query": "hello"})).status_code == 409
            assert calls == [] and supervisor.calls == 0
            assert len(store.jobs()) == 1 and store.job(legacy)["state"] == "cancelled"
            assert store.items(product="rag-agent") == original
            assert (await c.get("/history/1")).status_code == 200
            assert (await c.post("/api/v1/apps/rag-agent/pause")).status_code == 403
            bad = {**headers, "Origin": "http://evil.test"}
            assert (await c.post("/api/v1/apps/rag-agent/pause", headers=bad)).status_code == 403
            await c.post("/api/v1/apps/rag-agent/start", headers=headers)
            await rt.task
            await c.post("/api/v1/apps/rag-agent/start", headers=headers)
            assert supervisor.calls == 1
            assert (await c.get("/api/v1/apps/rag-agent/health")).json()["status"] == "ok"
            assert (await c.post("/api/v1/apps/rag-agent/sync", headers=headers)).status_code == 202
            await c.get("/")
            assert gate.snapshot()["state"] == "ready"
            await c.post("/api/v1/apps/rag-agent/pause", headers=headers)
            await rt.pause_task
            before = list(calls)
            await c.get("/")
            assert calls == before and gate.snapshot()["state"] == "disabled"
        gate.set_state("ready")  # Simulate a Host killed without normal shutdown.
        async with app.router.lifespan_context(app):
            assert gate.snapshot()["state"] == "disabled"


async def test_failed_start_never_schedules(tmp_path):
    store = SQLiteArchive(tmp_path / "a.db")
    gate = ProductActivity(store, "manual")
    supervisor = Supervisor(failure=True)
    rt = ProductRuntime(supervisor, gate)
    rt.request_start()
    await rt.task
    class Source:
        async def changes(self, cursor):
            raise AssertionError("must not contact product")
    for _ in range(2):
        await run_worker(SyncService("manual", Source(), store, gate), store,
                         tmp_path / "worker.lock", once=True)
    assert supervisor.calls == 1 and rt.snapshot()["state"] == "failed"
    assert store.jobs() == []


async def test_inflight_cancel_and_old_generation_cannot_commit(tmp_path):
    store = SQLiteArchive(tmp_path / "a.db")
    gate = ProductActivity(store, "manual")
    gate.set_state("ready")
    old = gate.snapshot()["generation"]
    entered, cancelled = asyncio.Event(), asyncio.Event()
    class Source:
        async def changes(self, cursor):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
    service = SyncService("manual", Source(), store, gate)
    worker = asyncio.create_task(run_worker(service, store, tmp_path / "worker.lock"))
    await asyncio.wait_for(entered.wait(), 2)
    rt = ProductRuntime(Supervisor(), gate)
    rt.request_pause()
    with pytest.raises(ProductError, match="product_stopping"):
        rt.request_start()
    await asyncio.wait_for(rt.pause_task, 2)
    assert cancelled.is_set() and store.items()["total"] == 0
    for _ in range(100):
        if store.jobs()[0]["state"] == "cancelled":
            break
        await asyncio.sleep(.01)
    assert store.jobs()[0]["state"] == "cancelled"
    assert len(store.jobs()) == 1
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    gate.set_state("ready")
    with pytest.raises(ProductError, match="product_disabled"):
        store.apply("manual", batch(), None, old)
    assert store.checkpoint("manual") is None


def child_worker(path, result):
    store = SQLiteArchive(path / "a.db")
    gate = ProductActivity(store, "manual")
    class Source:
        async def changes(self, cursor):
            result.put("called")
            return {**batch(), "has_more": False}
    asyncio.run(run_worker(SyncService("manual", Source(), store, gate), store,
                           path / "worker.lock", once=True))
    result.put("done")


def test_worker_process_restart_shares_gate(tmp_path):
    store = SQLiteArchive(tmp_path / "a.db")
    gate = ProductActivity(store, "manual")
    ctx = multiprocessing.get_context("spawn")
    for enabled in (False, True, False):
        gate.set_state("ready" if enabled else "disabled")
        results = ctx.Queue()
        process = ctx.Process(target=child_worker, args=(tmp_path, results))
        process.start()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join()
            pytest.fail("worker did not finish")
        assert process.exitcode == 0
        assert results.get(timeout=1) == ("called" if enabled else "done")
        if enabled:
            assert results.get(timeout=1) == "done"
        results.close()
    assert len(store.jobs()) == 1
    assert store.jobs()[0]["state"] == "succeeded"
    assert store.items(product="manual")["total"] == 1


async def test_pause_cancels_start_without_closing_independent_services(tmp_path):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    class SlowSupervisor(Supervisor):
        async def ensure_product(self):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def close(self):
            raise AssertionError("pause must not close independent services")

    store = SQLiteArchive(tmp_path / "a.db")
    gate = ProductActivity(store, "manual")
    runtime = ProductRuntime(SlowSupervisor(), gate)
    runtime.request_start()
    await entered.wait()
    runtime.request_pause()
    runtime.request_pause()
    await asyncio.wait_for(runtime.pause_task, 2)
    assert cancelled.is_set()
    assert runtime.snapshot()["state"] == "disabled"
    assert store.jobs() == []


async def test_pause_drains_health_request(tmp_path):
    store = SQLiteArchive(tmp_path / "a.db")
    gate = ProductActivity(store, "manual")
    gate.set_state("ready")
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def slow_health(generation):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    task = asyncio.create_task(gate.run(slow_health))
    await entered.wait()
    runtime = ProductRuntime(Supervisor(), gate)
    runtime.request_pause()
    await asyncio.wait_for(runtime.pause_task, 2)
    with pytest.raises(ProductError, match="product_disabled"):
        await task
    assert cancelled.is_set()
    assert store.jobs() == []
