"""RAG-specific Qdrant and Streamlit lifecycle rules."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aos.adapters.process import LocalProcesses
from aos.domain import ProductError


class RagProcesses(LocalProcesses):
    def matching_ui(self, service):
        record = self.saved(service)
        port_number = urlsplit(service.url).port
        if record and self.listening(record["pid"], port_number):
            return True
        # Match cwd, application and explicit port for manually started Streamlit.
        for path in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                argv = path.read_bytes().decode().split("\0")
                cwd = (path.parent / "cwd").resolve()
                port = str(urlsplit(service.url).port)
                if (cwd == service.cwd and any(Path(arg).name == "streamlit" for arg in argv)
                        and "ui/app.py" in argv
                        and self.listening(int(path.parent.name), port_number)
                        and (f"--server.port={port}" in argv
                             or any(argv[i:i + 2] == ["--server.port", port]
                                    for i in range(len(argv))))):
                    return True
            except (OSError, UnicodeError):
                continue
        return False

    async def probe(self, service):
        if service.kind not in {"qdrant", "ui"}:
            return await super().probe(service)
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                response = await client.get(service.url)
            if response.status_code != 200:
                return False
            if service.kind == "qdrant":
                return response.json().get("title") == "qdrant - vector search engine"
            return response.text.strip() == "ok" and self.matching_ui(service)
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    async def start_service(self, service):
        if service.kind == "qdrant":
            await self.start_qdrant()
            return None, False
        return await super().start_service(service)

    async def docker(self, *args):
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            raise ProductError("docker_unavailable") from None
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 10)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise ProductError("qdrant_container_unavailable")
        return stdout

    async def start_qdrant(self):
        try:
            data = json.loads(await self.docker("inspect", "rag_memory_qdrant"))[0]
            ports = data["HostConfig"]["PortBindings"]
            bindings = ports.get("6333/tcp", [])
            if not bindings or not any(b.get("HostPort") == "6333" for b in bindings):
                raise ProductError("qdrant_ports_invalid")
            if any(b.get("HostIp") != "127.0.0.1"
                   for entries in ports.values() for b in (entries or [])):
                raise ProductError("qdrant_ports_not_local")
            image = data["Config"]["Image"]
            if image != "qdrant/qdrant" and not image.startswith(("qdrant/qdrant:",
                                                                 "qdrant/qdrant@")):
                raise ProductError("qdrant_container_invalid")
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProductError("qdrant_container_invalid") from None
        await self.docker("start", data["Id"])
