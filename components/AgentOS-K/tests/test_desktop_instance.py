import socket
from pathlib import Path

import pytest
from test_process import manager, service  # noqa: F401

from aos.bootstrap import assemble, parse_args
from aos.desktop_instance import plan, check_ports
from aos.adapters.session_process import SessionLedger, SessionProcesses
from aos.domain import ProductError


@pytest.fixture
def copy_root(tmp_path):
    root = tmp_path / "copy"
    (root / ".local/preparation").mkdir(parents=True)
    (root / ".local/preparation/capture-complete.json").write_text("{}")
    for name in ("AgentOS-K", "PaperReader", "GitHub-Wiki"):
        (root / "components" / name).mkdir(parents=True)
    return root


@pytest.mark.parametrize("name", ["../production", "release", "dev-../x", "dev-x/y", "dev-X", "dev-x\n"])
def test_invalid_instance_never_creates_state(copy_root, name):
    with pytest.raises(ValueError):
        plan(copy_root, name, (18777, 18601, 18602))
    assert not (copy_root / ".local/instances").exists()


@pytest.mark.parametrize("ports", [(7777,18601,18602),(18777,8601,18602),(18777,18601,8602),
                                  (18777,18777,18602),(80,18601,18602),(65536,18601,18602)])
def test_bad_ports_rejected_without_writes(copy_root, ports):
    with pytest.raises(ValueError):
        plan(copy_root, "dev-test", ports)
    assert not (copy_root / ".local/instances").exists()


def test_linked_state_is_rejected(copy_root, tmp_path):
    (copy_root / ".local/instances").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        plan(copy_root, "dev-test", (18777,18601,18602))


def test_environment_does_not_copy_credentials(copy_root, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://synthetic.invalid")
    monkeypatch.setenv("AOS_PAPER_DIR", "/not-this-copy")
    a = plan(copy_root, "dev-a", (18777,18601,18602))
    b = plan(copy_root, "dev-b", (19777,19601,19602))
    env = a.environment()
    assert "GEMINI_API_KEY" not in env and "HTTPS_PROXY" not in env and "AOS_PAPER_DIR" not in env
    assert a.state != b.state
    assert Path(env["AOS_RUNTIME_LOCK_DIR"]).is_relative_to(a.state)
    assert not a.state.exists()


def test_occupied_port_not_taken_over():
    with socket.socket() as existing:
        existing.bind(("127.0.0.1", 0))
        existing.listen()
        port = existing.getsockname()[1]
        with pytest.raises(ValueError, match="occupied"):
            check_ports([port])
        assert existing.getsockname()[1] == port


async def test_explicit_data_directories_reach_both_products(copy_root):
    value = plan(copy_root, "dev-test", (18777,18601,18602))
    args, providers = parse_args(["serve", *value.arguments()])
    app, _, _, entries = assemble(args, providers)
    try:
        for entry in entries:
            command = entry.services[0].command
            data = Path(command[command.index("--data-dir") + 1])
            assert data == value.path("reader" if entry.product.id == "paper" else "wiki")
            assert data.is_relative_to(value.state)
    finally:
        for entry in entries:
            await entry.client.aclose()


async def test_strict_session_does_not_adopt_existing_product(service, tmp_path):  # noqa: F811
    outside = manager(service, tmp_path / "outside")
    ledger = SessionLedger(tmp_path / "ledger", "strict", grace=.2, reject_external=True)
    desktop = SessionProcesses([service], service, tmp_path / "inside", ledger, timeout=2)
    try:
        await outside.ensure_product()
        with pytest.raises(ProductError, match="port_conflict"):
            await desktop.ensure_product()
        assert not desktop.owned
        await desktop.close()
        assert await outside.probe(service)
    finally:
        await outside.stop(outside.saved(service))


@pytest.mark.parametrize("shutdown", ["exit", "eof"])
async def test_real_instance_lifecycle_and_duplicate_lock(shutdown):
    import asyncio
    import json
    import os
    import sys
    import uuid
    import httpx

    root = Path(__file__).resolve().parents[3]
    instance = "dev-smoke-" + uuid.uuid4().hex[:10]
    listeners = [socket.socket() for _ in range(6)]
    try:
        for listener in listeners:
            listener.bind(("127.0.0.1", 0))
        ports = [listener.getsockname()[1] for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()
    value = plan(root, instance, ports[:3])
    async def launch(chosen_ports, chosen_instance=instance):
        return await asyncio.create_subprocess_exec(
            sys.executable, "-m", "aos.desktop_instance", "--instance", chosen_instance,
            "--host-port", str(chosen_ports[0]), "--wiki-port", str(chosen_ports[1]),
            "--paper-port", str(chosen_ports[2]),
            cwd=root / "components/AgentOS-K",
            env={**os.environ, "GEMINI_API_KEY": "synthetic-only", "AOS_PAPER_DIR": "/wrong-copy"},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    process = await launch(ports[:3])
    async def until(kind):
        async def receive():
            while True:
                line = await process.stdout.readline()
                assert line, "Session exited before " + kind
                event = json.loads(line)
                assert event["event"] != "error", event
                if event["event"] == kind:
                    return event
        return await asyncio.wait_for(receive(), 45)
    async def send(**message):
        process.stdin.write((json.dumps(message)+"\n").encode())
        await process.stdin.drain()
    duplicate = None
    try:
        await send(token="s"*32)
        await until("ready")
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            for port, product in ((ports[1], "github-wiki"), (ports[2], "paper")):
                async def product_ready():
                    while True:
                        try:
                            response = await client.get(f"http://127.0.0.1:{port}/aos/v1/describe")
                            if response.status_code == 200:
                                return response
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(.1)
                reply = await asyncio.wait_for(product_ready(), 25)
                assert reply.status_code == 200
                assert reply.json()["product_id"] == product
                assert reply.json()["configuration_host"] == f"http://127.0.0.1:{ports[0]}"
        assert (value.path("workbench") / "archive.db").is_file()
        assert value.path("reader").is_dir() and value.path("wiki").is_dir()
        records = list((value.path("workbench") / "desktop/processes").glob("*.json"))
        assert len(records) >= 2
        for record in records:
            pid = json.loads(record.read_text())["pid"]
            environment = Path(f"/proc/{pid}/environ").read_bytes()
            assert b"synthetic-only" not in environment and b"/wrong-copy" not in environment
        duplicate = await launch(ports[3:])
        message = json.loads(await asyncio.wait_for(duplicate.stdout.readline(), 10))
        assert message["event"] == "error" and "另一个桌面会话" in message["message"]
        assert await asyncio.wait_for(duplicate.wait(), 10) == 0
        # Original instance must still answer after duplicate rejection.
        await send(token="s"*32, action="status")
        await until("status")
        if shutdown == "exit":
            await send(token="s"*32, action="exit")
        else:
            process.stdin.close()
        await until("closed")
        assert await asyncio.wait_for(process.wait(), 15) == 0
        assert not list((value.path("workbench") / "desktop/processes").glob("*.json"))
        for port in ports[:3]:
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        for child in (duplicate, process):
            if child and child.returncode is None:
                if child.stdin:
                    child.stdin.close()
                try:
                    await asyncio.wait_for(child.wait(), 40)
                except TimeoutError:
                    child.kill()
                    await child.wait()


def test_prepared_parser_uses_copy_assets_and_instance_caches(copy_root, monkeypatch):
    base = copy_root / ".local/pdf-cpu"
    (base / "venv/bin").mkdir(parents=True)
    (base / "venv/bin/python").write_text("fixture executable")
    (base / "models").mkdir()
    (base / "ready.json").write_text("{}")
    monkeypatch.setenv("PAPER_DOCLING_PYTHON", "/original/parser")
    value = plan(copy_root, "dev-parser", (18777,18601,18602))
    env = value.environment()
    assert env["PAPER_DOCLING_PYTHON"] == str(base / "venv/bin/python")
    assert env["DOCLING_ARTIFACTS_PATH"] == str(base / "models")
    assert Path(env["HF_HOME"]).is_relative_to(value.state)
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"


def test_prepared_parser_rejects_linked_external_models(copy_root, tmp_path):
    base = copy_root / ".local/pdf-cpu"
    base.mkdir()
    (base / "ready.json").write_text("{}")
    (base / "models").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        plan(copy_root, "dev-parser", (18777,18601,18602)).environment()

def test_developer_marker_allows_fresh_checkout_without_capture(copy_root):
    (copy_root / ".local/preparation/capture-complete.json").unlink()
    (copy_root / ".local/development.json").write_text('{"schema":1}')
    value = plan(copy_root, "dev-fresh", (18777,18778,18779))
    assert value.state == copy_root / ".local/instances/dev-fresh"

def test_developer_marker_rejects_external_symlink(copy_root, tmp_path):
    (copy_root / ".local/preparation/capture-complete.json").unlink()
    outside = tmp_path / "outside-marker.json"
    outside.write_text("{}")
    (copy_root / ".local/development.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic"):
        plan(copy_root, "dev-fresh", (18777,18778,18779))
