"""Explicit browser check using temporary HTTP/Streamlit services, no providers.

Requires Playwright+Chromium outside the application dependencies and the adjacent
RAG virtualenv for Streamlit. All files and databases use TemporaryDirectory.
"""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aos.adapters.process import Service
from aos.integrations.rag.process import RagProcesses as LocalProcesses


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def check(work):
    api_port, ui_port, host_port = free_port(), free_port(), free_port()
    api_url = f"http://127.0.0.1:{api_port}"
    ui_url = f"http://127.0.0.1:{ui_port}"
    host_url = f"http://127.0.0.1:{host_port}"
    api_script = work / "api.py"
    api_script.write_text('''from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"product_id":"rag-agent","contract_version":"1.0","instance_id":"fixture"}')
HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
'''.replace("PORT", str(api_port)))
    (work / "ui").mkdir()
    (work / "ui/app.py").write_text(
        "import streamlit as st\nst.title('Isolated product')\nst.write('Browser startup verified')\n")
    api = Service("api", (sys.executable, str(api_script)), work,
                  api_url + "/aos/v1/describe", "api", product_id="rag-agent")
    ui_python = str(ROOT.parent / "RAG-AGENT/.venv/bin/python")
    ui = Service("ui", (ui_python, "-m", "streamlit", "run", "ui/app.py",
                        "--server.address=127.0.0.1", f"--server.port={ui_port}",
                        "--browser.gatherUsageStats=false"), work,
                 ui_url + "/_stcore/health", "ui")
    lock_script = "import fcntl,time; f=open('worker.lock','a'); fcntl.flock(f,6); time.sleep(60)"
    worker = Service("worker", (sys.executable, "-c", lock_script), work, "", "worker")
    supervisor = LocalProcesses([api, ui], worker, work, work / "locks")
    runner = work / "host.py"
    runner.write_text(f'''from pathlib import Path
import uvicorn,httpx
from aos.adapters.process import Service
from aos.integrations.rag.process import RagProcesses as LocalProcesses
from aos.integrations.rag.http import RagHttpAdapter
from aos.adapters.sqlite import SQLiteArchive
from aos.application import ProductRuntime
from aos.domain import Product
from aos.host.web import create_app
api = Service({api.name!r}, {api.command!r}, Path({str(work)!r}), {api.url!r}, "api", product_id="rag-agent")
ui = Service({ui.name!r}, {ui.command!r}, Path({str(work)!r}), {ui.url!r}, "ui")
worker = Service("worker", {worker.command!r}, Path({str(work)!r}), "", "worker")
runtime = ProductRuntime(LocalProcesses([api, ui], worker, Path({str(work)!r}), Path({str(work / 'locks')!r})))
product = Product("rag-agent", "Fixture", {api_url!r}, {ui_url!r})
client = httpx.AsyncClient(base_url={api_url!r}, trust_env=False)
app = create_app(product, RagHttpAdapter(client), SQLiteArchive(Path({str(work / 'archive.db')!r})), runtime=runtime)
uvicorn.run(app, host="127.0.0.1", port={host_port})
''')
    with (work / "host.log").open("w") as log:
        process = await asyncio.create_subprocess_exec(sys.executable, str(runner), stdout=log, stderr=log,
                                   env={**os.environ, "PYTHONPATH": str(ROOT)})
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(100):
                try:
                    if (await client.get(host_url + "/api/v1/apps")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.1)
            else:
                raise AssertionError("fixture Host did not start")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.context.route("**/*", lambda route: route.continue_()
                                     if route.request.url.startswith(("http://127.0.0.1:",
                                                                       "http://localhost:"))
                                     else route.abort())
            await page.goto(host_url)
            async with page.expect_popup() as first_popup:
                await page.get_by_role("link", name="打开原产品 ↗").click()
            product_page = await first_popup.value
            first_waiting = product_page
            await first_waiting.get_by_role("link", name="在新标签页打开 Fixture ↗").wait_for(timeout=60000)
            async with first_waiting.expect_popup() as opened:
                await first_waiting.get_by_role("link", name="在新标签页打开 Fixture ↗").click()
            product_page = await opened.value
            assert first_waiting.url == host_url + "/products/rag-agent/open"
            assert page.url == host_url + "/"
            await product_page.wait_for_url(ui_url + "/", timeout=30000)
            await product_page.get_by_text("Browser startup verified").wait_for(timeout=30000)
            record = supervisor.saved(ui)
            async with page.expect_popup() as second_popup:
                await page.get_by_role("link", name="打开 Fixture ↗").click()
            second = await second_popup.value
            await second.get_by_role("link", name="在新标签页打开 Fixture ↗").wait_for(timeout=30000)
            assert second.url == host_url + "/products/rag-agent/open"
            assert page.url == host_url + "/"
            assert record == supervisor.saved(ui), "duplicate browser click launched another UI"
            # Kill the owned UI and verify a later click restarts it.
            await supervisor.stop(record)
            async with page.expect_popup() as third_popup:
                await page.get_by_role("link", name="打开原产品 ↗").click()
            third = await third_popup.value
            await third.get_by_role("link", name="在新标签页打开 Fixture ↗").wait_for(timeout=30000)
            async with third.expect_popup() as reopened:
                await third.get_by_role("link", name="在新标签页打开 Fixture ↗").click()
            assert third.url == host_url + "/products/rag-agent/open"
            assert page.url == host_url + "/"
            third = await reopened.value
            await third.wait_for_url(ui_url + "/", timeout=30000)
            await third.get_by_text("Browser startup verified").wait_for(timeout=30000)
            assert supervisor.saved(ui)["pid"] != record["pid"]
            await browser.close()
        print("PASS: real browser cold start, both entry links, Streamlit render, reuse and restart")
    finally:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 10)
        except TimeoutError:
            process.kill()
            await process.wait()
        for service in (ui, api, worker):
            await supervisor.stop(supervisor.saved(service))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="aos-browser-") as directory:
        asyncio.run(check(Path(directory)))
