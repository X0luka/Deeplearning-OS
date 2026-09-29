"""Extension boundaries, independent core startup and third-product composition."""
import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_archive import batch

from aos.adapters.sqlite import SQLiteArchive
from aos.bootstrap import assemble, parse_args
from aos.domain import Product
from aos.integrations import Integration
from aos.worker import run_worker

ROOT = Path(__file__).resolve().parents[1]


def test_core_contains_no_product_knowledge():
    forbidden = ("rag-agent", "github-wiki", "qdrant", "streamlit", "rag_api", "rag_ui",
                 "RagHttpAdapter", "WikiHttpAdapter", "/api/query/sync", "--start-wiki")
    for path in (ROOT / "aos").rglob("*"):
        if path.suffix not in {".py", ".html"} or "integrations" in path.parts:
            continue
        contents = path.read_text()
        assert not any(word in contents for word in forbidden), path


def test_none_never_imports_product_modules():
    code = "from aos.bootstrap import parse_args; import sys; parse_args(['serve','--products','none']); assert not any(n.startswith('aos.integrations.') for n in sys.modules)"
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


@pytest.mark.parametrize("selection,expected", [("none", []), ("rag", ["rag-agent"]),
    ("wiki", ["github-wiki"]), ("paper", ["paper"]), ("all", ["paper", "rag-agent", "github-wiki"])])
async def test_selection_and_archive_only(tmp_path, selection, expected):
    args, providers = parse_args(["serve", "--products", selection, "--data-dir", str(tmp_path)])
    app, store, services, entries = assemble(args, providers, ["--products", selection])
    assert list(services) == expected
    try:
        store.apply("old-product", batch(), None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
            assert (await c.get("/api/v1/identity")).json()["products"] == expected
            page = await c.get("/?product_id=old-product")
            assert page.status_code == 200 and "old-product" in page.text
            assert (await c.get("/history/1")).status_code == 200
            if not expected:
                assert "档案工作台" in page.text
                assert not (tmp_path / "worker.lock").exists()
            if "rag-agent" in expected:
                assert store.activity("rag-agent")["state"] == "disabled"
        if entries:
            command = app.state.worker_command
            assert command[command.index("--products") + 1] == selection
            assert command[-1] == str(tmp_path.resolve())
    finally:
        for e in entries:
            await e.client.aclose()


async def test_third_extension_requires_no_core_change(tmp_path):
    calls = []
    class Adapter:
        async def health(self):
            calls.append("health")
            return {"status": "ok"}
        async def changes(self, cursor):
            calls.append("changes")
            return {**batch(), "has_more": False}
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    product = Product("paper-tool", "Paper Tool", "http://127.0.0.1:19991", "http://127.0.0.1:19991")
    provider = SimpleNamespace(create=lambda _: Integration(product, client, Adapter()))
    args, _ = parse_args(["serve", "--products", "none", "--data-dir", str(tmp_path)])
    app, store, services, _ = assemble(args, [provider])
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
            assert "Paper Tool" in (await c.get("/")).text
            headers = {"X-AOS-Token": app.state.write_token}
            assert (await c.post("/api/v1/apps/paper-tool/sync", headers=headers)).status_code == 202
        await app.state.manual_sync.task
        assert store.items(product="paper-tool")["total"] == 1
        assert calls == ["health", "changes"]
    finally:
        await client.aclose()


async def test_unselected_jobs_are_not_claimed(tmp_path):
    store = SQLiteArchive(tmp_path / "a.db")
    old = store.enqueue("unselected")
    class Source:
        product = "selected"
        async def run(self):
            return 0
    await run_worker(Source(), store, tmp_path / "worker.lock", once=True)
    assert store.job(old)["state"] == "queued"
    assert store.jobs()[0]["state"] == "succeeded"


async def test_real_core_server_without_products(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with (tmp_path / "server.log").open("w") as log:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "aos.bootstrap", "serve", "--products", "none",
            "--port", str(port), "--data-dir", str(tmp_path / "data"), cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=log, stderr=log)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as c:
            for _ in range(100):
                try:
                    response = await c.get("/")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(.05)
            else:
                pytest.fail((tmp_path / "server.log").read_text())
            assert response.status_code == 200 and "档案工作台" in response.text
            assert (await c.get("/api/v1/apps")).json() == []
        assert not (tmp_path / "data/worker.lock").exists()
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except TimeoutError:
            proc.kill()
            await proc.wait()
