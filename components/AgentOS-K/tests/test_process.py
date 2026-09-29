"""Exercise real isolated processes, startup failure and ownership boundaries."""
import asyncio
import socket
import sys
from dataclasses import replace

import pytest

from aos.adapters.process import LocalProcesses, Service, identity
from aos.application import ProductRuntime
from aos.domain import ProductError


@pytest.fixture
def service(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    script = tmp_path / "server.py"
    script.write_text('''from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"product_id":"rag-agent","contract_version":"1.0","instance_id":"test"}')
HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
'''.replace("PORT", str(port)))
    return Service("test_api", (sys.executable, str(script)), tmp_path,
                   f"http://127.0.0.1:{port}/aos/v1/describe", "api", product_id="rag-agent")


def manager(service, tmp_path, timeout=3):
    return LocalProcesses([service], service, tmp_path, tmp_path / "locks", timeout)


async def test_concurrent_start_restart_reuse_and_external_protection(service, tmp_path):
    first, second = manager(service, tmp_path), manager(service, tmp_path)
    try:
        await asyncio.gather(first.ensure_product(), second.ensure_product())
        record = first.saved(service)
        assert record and await first.probe(service)
        assert len(first.children) + len(second.children) == 1
        # A replacement Host reuses the same process and does not stop it on exit.
        replacement = manager(service, tmp_path)
        await replacement.ensure_product()
        assert replacement.saved(service) == record
        await replacement.close()
        assert identity(record["pid"]) == record["start"]
        # A stale PID identity must never kill the running process.
        await replacement.stop({**record, "start": "wrong-start-ticks"})
        assert await replacement.probe(service)
    finally:
        await first.stop(first.saved(service))


@pytest.mark.parametrize("mode", ["exit", "timeout"])
async def test_failed_start_cleanup(service, tmp_path, mode):
    code = "raise SystemExit(7)" if mode == "exit" else "import time; time.sleep(60)"
    service = replace(service, command=(sys.executable, "-c", code))
    processes = manager(service, tmp_path, .3)
    with pytest.raises(ProductError, match="exited|timeout"):
        await processes.ensure_product()
    assert processes.saved(service) is None
    assert all(child.poll() is not None for child in processes.children)


async def test_wrong_service_on_port_never_stopped(service, tmp_path):
    owner = manager(service, tmp_path)
    record = owner.spawn(service)
    try:
        for _ in range(50):
            if await owner.probe(service):
                break
            await asyncio.sleep(.05)
        unrelated = replace(service, kind="ui", command=(sys.executable, "different.py"))
        other = manager(unrelated, tmp_path)
        with pytest.raises(ProductError, match="port_conflict"):
            await other.ensure_product()
        assert identity(record["pid"]) == record["start"]
    finally:
        await owner.stop(record)


async def test_worker_reuse_and_only_owner_cleanup(service, tmp_path):
    script = "import fcntl,time; f=open('worker.lock','a'); fcntl.flock(f,6); time.sleep(60)"
    worker = replace(service, name="worker", url="", kind="worker",
                     command=(sys.executable, "-c", script))
    owner, other = manager(worker, tmp_path), manager(worker, tmp_path)
    try:
        assert await owner.ensure_worker() == "running"
        record = owner.owned_worker
        assert await other.ensure_worker() == "external"
        await other.close()
        assert identity(record["pid"]) == record["start"]
        await owner.close()
        assert identity(record["pid"]) != record["start"]
    finally:
        await owner.close()


async def test_runtime_deduplicates_failure_and_retry():
    class Supervisor:
        calls = 0

        async def ensure_product(self):
            self.calls += 1
            await asyncio.sleep(.02)
            if self.calls == 1:
                raise ProductError("rag_api_timeout")

        async def close(self):
            pass

    supervisor = Supervisor()
    runtime = ProductRuntime(supervisor)
    for _ in range(10):
        assert runtime.request_start()["state"] == "starting"
    await runtime.task
    assert supervisor.calls == 1 and runtime.snapshot()["error"] == "rag_api_timeout"
    runtime.request_start()
    await runtime.task
    assert runtime.snapshot()["state"] == "ready" and supervisor.calls == 2




async def test_worker_restart_after_kill(service, tmp_path):
    import os
    import signal

    script = "import fcntl,time; f=open('worker.lock','a'); fcntl.flock(f,6); time.sleep(60)"
    worker = replace(service, name="worker", url="", kind="worker",
                     command=(sys.executable, "-c", script))
    processes = manager(worker, tmp_path)
    try:
        await processes.ensure_worker()
        old = processes.owned_worker.copy()
        os.kill(old["pid"], signal.SIGKILL)
        for _ in range(50):
            if identity(old["pid"]) is None:
                break
            await asyncio.sleep(.05)
        assert await processes.ensure_worker() == "running"
        assert processes.owned_worker["pid"] != old["pid"]
        assert await processes.ensure_worker() == "running"
    finally:
        await processes.close()


async def test_two_host_processes_share_startup_lock(service, tmp_path):
    from pathlib import Path

    script = f'''
import asyncio
from pathlib import Path
from aos.adapters.process import LocalProcesses, Service
s = Service({service.name!r}, {service.command!r}, Path({str(service.cwd)!r}),
            {service.url!r}, {service.kind!r}, product_id={service.product_id!r})
p = LocalProcesses([s], s, Path({str(tmp_path)!r}), Path({str(tmp_path / "locks")!r}))
asyncio.run(p.ensure_product())
'''
    parent = manager(service, tmp_path)
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, cwd=Path(__file__).resolve().parents[1])
    try:
        await parent.ensure_product()
        record = parent.saved(service)
        assert await asyncio.wait_for(child.wait(), 10) == 0
        assert record == parent.saved(service)
        assert await parent.probe(service)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
        await parent.stop(parent.saved(service))


async def test_worker_without_lock_times_out_and_is_cleaned(service, tmp_path):
    worker = replace(service, name="worker", url="", kind="worker",
                     command=(sys.executable, "-c", "import time; time.sleep(60)"))
    processes = manager(worker, tmp_path, .2)
    with pytest.raises(ProductError, match="worker_timeout"):
        await processes.ensure_worker()
    assert processes.owned_worker is None
    assert all(child.poll() is not None for child in processes.children)


async def test_dependency_failure_does_not_start_later_services(service, tmp_path):
    failure = replace(service, name="first", command=(sys.executable, "-c", "raise SystemExit(1)"))
    processes = manager(failure, tmp_path)
    processes.services = [failure, replace(service, name="second")]
    with pytest.raises(ProductError, match="first_exited"):
        await processes.ensure_product()
    assert len(processes.children) == 1


async def test_bootstrap_starts_real_worker_and_stops_it(service, tmp_path):
    from aos.adapters.sqlite import SQLiteArchive
    from aos.application import ProductRuntime
    from aos.bootstrap import worker_for
    from aos.domain import Product

    product = Product("rag-agent", "Fixture", service.url.rsplit("/aos", 1)[0],
                      "http://127.0.0.1:18501")
    runtime = ProductRuntime(LocalProcesses([], worker_for(tmp_path, ("--products", "rag", "--rag-api", product.api_url, "--rag-ui", product.ui_url)), tmp_path))
    # No product startup: real Worker only, using the isolated archive and a closed local port.
    runtime.supervisor.lock_dir = tmp_path / "locks"
    runtime.supervisor.lock_dir.mkdir()
    await runtime.start()
    try:
        for _ in range(100):
            if runtime.snapshot()["worker"] == "running":
                break
            await asyncio.sleep(.05)
        assert runtime.snapshot()["worker"] == "running"
        archive = SQLiteArchive(tmp_path / "archive.db")
        await asyncio.sleep(.2)
        assert archive.jobs() == []
        assert archive.activity("rag-agent")["state"] == "disabled"
        record = runtime.supervisor.owned_worker.copy()
    finally:
        await runtime.close()
    assert identity(record["pid"]) != record["start"]
