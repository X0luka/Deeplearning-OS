import importlib.util
import json
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "deepseek_acceptance", Path(__file__).resolve().parents[3] / "scripts/deepseek_acceptance.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def settings():
    return {"base_url": "https://api.deepseek.com", "protocol": "chat", "model": "deepseek-flash",
            "api_key": "synthetic-key", "proxy": None, "custom": "", "max_output_tokens": 64,
            "thinking": "off", "tools": False, "web_search": False, "document_input": "text",
            "capabilities": {}}


@pytest.mark.parametrize("case", [1, 2, 3])
async def test_fixed_samples_limits_and_no_answer_logging(case):
    seen = []
    def respond(request):
        seen.append(request)
        payload = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert payload["max_tokens"] == 64 and payload["thinking"] == {"type": "disabled"}
        assert "tools" not in payload
        if case == 2:
            data = {"choices": [{"delta": {"content": "一加一等于二。"}, "finish_reason": "stop"}],
                    "usage": {"completion_tokens": 8}}
            return httpx.Response(200, text="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n")
        content = "API_TEST_OK" if case == 1 else "CONFIG_RELOAD_OK"
        return httpx.Response(200, json={"choices": [{"message": {"content": content},
                                                    "finish_reason": "stop"}],
                                        "usage": {"completion_tokens": 4}})
    result = await module.attempt(settings(), case, httpx.MockTransport(respond))
    assert result["status"] == "passed" and len(seen) == 1
    assert "synthetic-key" not in json.dumps(result) and "content" not in result


async def test_failure_is_not_retried_and_config_drift_blocks_request():
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(401, text="synthetic-key")
    with pytest.raises(ValueError, match="provider_http_401"):
        await module.attempt(settings(), 1, httpx.MockTransport(respond))
    assert len(seen) == 1
    value = settings()
    value["base_url"] = "https://other.test"
    with pytest.raises(ValueError, match="acceptance_configuration_changed"):
        await module.attempt(value, 1, httpx.MockTransport(respond))
    assert len(seen) == 1
