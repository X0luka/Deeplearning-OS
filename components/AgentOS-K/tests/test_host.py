"""Exercise adapter failures, actual host rendering and write protection."""
import ast
from pathlib import Path

import httpx
import pytest
from test_archive import batch

from aos.adapters.sqlite import SQLiteArchive
from aos.domain import Product, ProductError
from aos.host.web import create_app
from aos.integrations.rag.http import RagHttpAdapter


async def test_adapter_degraded_and_timeout():
    async with httpx.AsyncClient(base_url="http://rag", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "degraded", "checks": {}}))) as c:
        assert (await RagHttpAdapter(c).health())["status"] == "degraded"
    def fail(request):
        raise httpx.ReadTimeout("secret provider detail")
    async with httpx.AsyncClient(base_url="http://rag", transport=httpx.MockTransport(fail)) as c:
        result = await RagHttpAdapter(c).health()
        assert result["reason"] == "timeout" and "secret" not in str(result)


async def test_unknown_schema_and_query_mapping():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"answer": "ok", "interaction_id": 1})
    async with httpx.AsyncClient(base_url="http://rag", transport=httpx.MockTransport(respond)) as c:
        adapter = RagHttpAdapter(c)
        assert (await adapter.query("q"))["answer"] == "ok"
        assert b'"include_history":false' in requests[0].content
        with pytest.raises(ProductError, match="schema_incompatible"):
            await adapter.changes(None)


async def test_html_archive_and_csrf(tmp_path):
    a = SQLiteArchive(tmp_path / "a.db")
    a.apply("rag", batch(), None)
    async with httpx.AsyncClient(base_url="http://rag", transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"status": "ok"}))) as upstream:
        app = create_app(Product("rag", "RAG", "http://127.0.0.1:8000", "http://127.0.0.1:8501"),
                         RagHttpAdapter(upstream), a)
        async with httpx.AsyncClient(base_url="http://testserver", transport=httpx.ASGITransport(app)) as c:
            page = await c.get("/")
            assert page.status_code == 200 and "会话档案" in page.text
            assert "&lt;script&gt;" in page.text and "<script>unsafe" not in page.text
            assert (await c.get("/history/1")).status_code == 200
            assert (await c.post("/api/v1/apps/rag/sync")).status_code == 403
            headers = {"X-AOS-Token": app.state.write_token}
            assert (await c.post("/api/v1/apps/rag/sync", headers=headers)).status_code == 202
            headers["Origin"] = "http://evil.test"
            assert (await c.post("/api/v1/apps/rag/sync", headers=headers)).status_code == 403
            assert (await c.get("/", headers={"Host": "evil.test"})).status_code == 400


def test_import_boundaries():
    root = Path(__file__).parents[1] / "aos"
    for name in ("domain.py", "ports.py", "application.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else ""
            assert not (module or "").startswith(("src", "aos.adapters", "fastapi", "httpx"))


async def test_product_start_page_auth_polling_and_lifespan(tmp_path):
    from aos.application import ProductRuntime

    class Supervisor:
        started = 0
        closed = False

        async def ensure_product(self):
            self.started += 1

        async def ensure_worker(self):
            return "running"

        async def close(self):
            self.closed = True

    supervisor = Supervisor()
    runtime = ProductRuntime(supervisor)
    a = SQLiteArchive(tmp_path / "a.db")
    async with httpx.AsyncClient(base_url="http://rag", transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"status": "ok"}))) as upstream:
        app = create_app(Product("rag", "RAG", "http://127.0.0.1:8000", "http://127.0.0.1:8501"),
                         RagHttpAdapter(upstream), a, runtime=runtime)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(base_url="http://testserver",
                              transport=httpx.ASGITransport(app)) as c,
        ):
            assert (await c.get("/products/rag/open")).status_code == 200
            assert supervisor.started == 0  # GET cannot spawn services.
            page = (await c.get("/")).text
            assert page.count('href="/products/rag/open"') == 2
            assert (await c.post("/api/v1/apps/rag/start")).status_code == 403
            headers = {"X-AOS-Token": app.state.write_token}
            assert (await c.post("/api/v1/apps/missing/start", headers=headers)).status_code == 404
            bad = {**headers, "Origin": "http://evil.test"}
            assert (await c.post("/api/v1/apps/rag/start", headers=bad)).status_code == 403
            assert (await c.post("/api/v1/apps/rag/start", headers=headers)).status_code == 202
            await runtime.task
            result = (await c.get("/api/v1/apps/rag/runtime")).json()
            assert result["state"] == "ready"
            assert result["ui_url"] == "http://127.0.0.1:8501"
        assert supervisor.closed
