"""Registered local process startup, with OS locks and durable process identities."""
import asyncio
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aos.domain import ProductError


@dataclass(frozen=True)
class Service:
    name: str
    command: tuple[str, ...]
    cwd: Path
    url: str
    kind: str
    env: dict = field(default_factory=dict)
    product_id: str = ""


def identity(pid):
    """Linux start ticks prevent acting on a reused PID."""
    try:
        values = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return values[19] if values[0] != "Z" else None
    except (OSError, IndexError):
        return None


class LocalProcesses:
    def __init__(self, services, worker, data_dir, lock_dir=None, timeout=45):
        self.services = services
        self.worker = worker
        self.data_dir = data_dir
        self.lock_dir = lock_dir or Path(os.getenv("AOS_RUNTIME_LOCK_DIR", f"/tmp/agentos-runtime-{os.getuid()}"))
        self.lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.timeout = timeout
        self.children = []
        self.owned_worker = None

    def key(self, service):
        # All Host instances targeting the same port share one startup lock.
        port = urlsplit(service.url).port
        raw = str(port) if port else str(service.cwd) + service.name
        return hashlib.sha256(raw.encode()).hexdigest()[:20]

    @asynccontextmanager
    async def lock(self, service):
        path = self.lock_dir / (self.key(service) + ".lock")
        with path.open("a") as handle:
            deadline = time.monotonic() + self.timeout + 5
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ProductError("startup_busy") from None
                    await asyncio.sleep(.05)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def state_path(self, service):
        return self.lock_dir / (self.key(service) + ".json")

    def saved(self, service):
        try:
            record = json.loads(self.state_path(service).read_text())
            if (record["start"] and identity(record["pid"]) == record["start"]
                    and record["command"] == list(service.command)
                    and record["cwd"] == str(service.cwd)):
                return record
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def listening(self, pid, port):
        inodes = set()
        try:
            for name in ("tcp", "tcp6"):
                for line in Path(f"/proc/net/{name}").read_text().splitlines()[1:]:
                    fields = line.split()
                    if fields[3] == "0A" and int(fields[1].split(":")[1], 16) == port:
                        inodes.add(f"socket:[{fields[9]}]")
            return any(os.readlink(fd) in inodes for fd in Path(f"/proc/{pid}/fd").iterdir())
        except OSError:
            return False

    async def probe(self, service):
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                response = await client.get(service.url)
            if response.status_code != 200:
                return False
            if service.kind == "api":
                data = response.json()
                return (data.get("product_id") == service.product_id
                        and data.get("contract_version") == "1.0"
                        and bool(data.get("instance_id"))
                        and (service.env.get("AOS_LLM_MODE") != "managed" or
                             (data.get("model_configuration") == "managed-v1" and
                              data.get("configuration_host") == service.env.get("AOS_LLM_URL"))))
            return False
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    async def occupied(self, service):
        url = urlsplit(service.url)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(url.hostname, url.port or 80), timeout=1)
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, TimeoutError):
            return False

    def spawn(self, service):
        if not service.cwd.is_dir() or not Path(service.command[0]).is_file():
            raise ProductError(f"{service.name}_environment_missing")
        log_dir = self.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(log_dir / f"{service.name}.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as log:
            try:
                child = subprocess.Popen(service.command, cwd=service.cwd,
                                         env={**os.environ, **service.env}, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=log, start_new_session=True)
            except OSError:
                raise ProductError(f"{service.name}_launch_failed") from None
        self.children.append(child)
        record = {"pid": child.pid, "start": identity(child.pid),
                  "command": list(service.command), "cwd": str(service.cwd)}
        path = self.state_path(service)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(record))
            temporary.replace(path)
        except OSError:
            child.kill()
            child.wait()
            raise ProductError("process_state_write_failed") from None
        return record

    async def stop(self, record):
        # Signal only the exact process started by this supervisor, never by port/name.
        if not record or not record["start"] or identity(record["pid"]) != record["start"]:
            return
        try:
            os.kill(record["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 3
            while identity(record["pid"]) == record["start"] and time.monotonic() < deadline:
                await asyncio.sleep(.05)
            if identity(record["pid"]) == record["start"]:
                os.kill(record["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass
        for child in self.children:
            child.poll()

    async def ensure(self, service):
        async with self.lock(service):
            if await self.probe(service):
                return
            record = self.saved(service)
            if not record and await self.occupied(service):
                raise ProductError(f"{service.name}_port_conflict")
            created = False
            if not record:
                record, created = await self.start_service(service)
            try:
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    if record and identity(record["pid"]) != record["start"]:
                        raise ProductError(f"{service.name}_exited")
                    if await self.probe(service):
                        return
                    await asyncio.sleep(.1)
                raise ProductError(f"{service.name}_timeout")
            except BaseException:
                if created:
                    await self.stop(record)
                raise

    async def start_service(self, service):
        return self.spawn(service), True

    async def ensure_product(self):
        for service in self.services:
            await self.ensure(service)

    async def ensure_worker(self):
        for child in self.children:
            child.poll()
        service = self.worker
        async with self.lock(service):
            # A separately started Worker owns the same OS lock; leave it running.
            worker_lock = self.data_dir / "worker.lock"
            worker_lock.parent.mkdir(parents=True, exist_ok=True)
            with worker_lock.open("a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if (self.owned_worker and identity(self.owned_worker["pid"])
                            == self.owned_worker["start"]):
                        return "running"
                    return "external"
                fcntl.flock(handle, fcntl.LOCK_UN)
                record = self.spawn(service)
                self.owned_worker = record
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    if identity(record["pid"]) != record["start"]:
                        raise ProductError("worker_exited")
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(handle, fcntl.LOCK_UN)
                    except BlockingIOError:
                        return "running"
                    await asyncio.sleep(.1)
                await self.stop(record)
                self.owned_worker = None
                raise ProductError("worker_timeout")

    async def close(self):
        await self.stop(self.owned_worker)
        # Product processes survive Host shutdown, so original UI sessions can continue.
        for child in self.children:
            child.poll()
