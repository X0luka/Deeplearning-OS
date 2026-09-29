"""Product-specific dependency checks live alongside extension coverage."""
import json

import pytest

from aos.domain import ProductError
from aos.integrations.rag.process import RagProcesses


async def test_container_ports_checked_before_start(tmp_path):
    processes = RagProcesses([], None, tmp_path, tmp_path / "locks")
    calls = []

    async def docker(*args):
        calls.append(args)
        return json.dumps([{"Id": "test", "Config": {"Image": "qdrant/qdrant:local"},
                            "HostConfig": {"PortBindings": {
                                "6333/tcp": [{"HostIp": "0.0.0.0", "HostPort": "6333"}]}}}])

    processes.docker = docker
    with pytest.raises(ProductError, match="ports_not_local"):
        await processes.start_qdrant()
    assert calls == [("inspect", "rag_memory_qdrant")]
