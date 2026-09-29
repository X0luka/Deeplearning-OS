import importlib.util
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/api_settings.py"
spec = importlib.util.spec_from_file_location("api_settings", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


async def test_configuration_entry_blocks_generation_and_catalog(tmp_path):
    app = module.create_settings_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        page = await c.get("/settings/models")
        assert page.status_code == 200
        assert "独立测试配置" in page.text and "deepseek-flash" in page.text
        headers = {"X-Aos-Token": app.state.write_token}
        for path in ("/assistant", "/api/v1/assistant/conversations",
                     "/api/v1/llm/connections/fake/refresh", "/internal/llm/v1/paper/resolve"):
            assert (await c.post(path, headers=headers, json={})).status_code == 403
        response = await c.post("/api/v1/llm/connections", headers=headers, json={
            "name": "Synthetic", "protocol": "chat", "base_url": "https://models.test",
            "key": "synthetic-test-key"})
        assert response.status_code == 200 and "synthetic-test-key" not in response.text
        assert (await c.get("/api/v1/llm")).status_code == 403


def test_outbound_guard_rejects_connect_dns_and_datagrams():
    for event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
        with pytest.raises(PermissionError, match="configuration_only_no_outbound"):
            module.deny_outbound(event, ())
    module.deny_outbound("socket.bind", ())
