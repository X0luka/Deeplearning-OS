"""Offline fault injection, not real-provider or no-proxy acceptance."""
import json

import httpx
import pytest

from aos.assistant.transport import stream
from aos.llm.catalog import discover, model_item
from aos.llm.store import ModelStore


@pytest.mark.parametrize("protocol", ["chat", "gemini"])
@pytest.mark.parametrize("failure,code", [
    (httpx.ConnectTimeout, "timeout"), (httpx.ReadTimeout, "timeout"),
    (httpx.ProxyError, "proxy_error"), (httpx.ConnectError, "connect_error"),
])
async def test_network_error_is_safe_and_not_replayed(tmp_path, protocol, failure, code):
    store = ModelStore(tmp_path / "llm")
    store.save_connection({"name":"Synthetic", "protocol":protocol,
        "base_url":"https://model.invalid/v1", "key":"synthetic-secret"})
    identity = store.read()["connections"][0]["id"]
    store.mutate(lambda s:s["connections"][0]["models"].append(model_item("fixture")))
    old = store.read()["connections"][0]["models"]
    calls = []
    def broken(request):
        calls.append(request)
        raise failure("synthetic-secret MUST NOT LEAK", request=request)
    transport = httpx.MockTransport(broken)
    with pytest.raises(ValueError, match="^catalog_" + code + "$"):
        await discover(store, identity, transport)
    assert len(calls) == 1
    assert store.read()["connections"][0]["models"] == old
    assert "synthetic-secret" not in json.dumps(store.public())
    calls.clear()
    settings = {"protocol":protocol, "base_url":"https://model.invalid/v1", "model":"fixture",
                "api_key":"synthetic-secret", "max_output_tokens":30, "capabilities":{}}
    async def emit(text):
        raise AssertionError("No output expected")
    with pytest.raises(ValueError, match="^model_" + code + "$"):
        await stream(settings, [{"role":"user","content":"test"}], [], emit, transport)
    assert len(calls) == 1


@pytest.mark.parametrize("protocol", ["chat", "gemini"])
@pytest.mark.parametrize("status", [302, 401, 403, 429, 500, 503])
async def test_http_failure_never_follows_redirect_or_replays(protocol, status):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(status, text="synthetic-secret", headers={"Location":"https://elsewhere.invalid"})
    async def emit(text):
        raise AssertionError("No output expected")
    settings = {"protocol":protocol, "base_url":"https://model.invalid/v1", "model":"fixture",
                "api_key":"synthetic-secret", "max_output_tokens":30, "capabilities":{}}
    with pytest.raises(ValueError, match="^provider_http_" + str(status) + "$"):
        await stream(settings, [{"role":"user","content":"test"}], [], emit, httpx.MockTransport(upstream))
    assert len(calls) == 1


async def test_midstream_failure_keeps_emitted_text_without_retry():
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ReadError("synthetic-secret")
    calls, emitted = [], []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, stream=Broken())
    async def emit(text):
        emitted.append(text)
    settings = {"protocol":"chat","base_url":"https://model.invalid/v1","model":"fixture",
                "api_key":"synthetic-secret","max_output_tokens":30,"capabilities":{}}
    with pytest.raises(ValueError, match="^model_network_error$"):
        await stream(settings,[{"role":"user","content":"test"}],[],emit,httpx.MockTransport(upstream))
    assert emitted == ["partial"] and len(calls) == 1


async def test_thinking_tool_continuation_preserves_protocol_without_displaying_reasoning():
    from aos.assistant.transport import model_request
    frames = [
        {"choices":[{"delta":{"reasoning_content":"synthetic protocol reasoning"}}]},
        {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"tool-1","function":{"name":"read_material","arguments":"{}"}}]},"finish_reason":"tool_calls"}]},
        {"choices":[],"usage":{"total_tokens":12}},
    ]
    body = "".join("data: "+json.dumps(f)+"\n\n" for f in frames)+"data: [DONE]\n\n"
    emitted = []
    async def emit(value):
        emitted.append(value)
    settings={"protocol":"chat","base_url":"https://api.deepseek.com","model":"deepseek-flash",
              "api_key":"synthetic","max_output_tokens":16384,"thinking":"default","capabilities":{}}
    tools=[{"type":"function","function":{"name":"read_material","parameters":{"type":"object"}}}]
    message, usage = await stream(settings,[{"role":"user","content":"fixture"}],tools,emit,
        httpx.MockTransport(lambda _:httpx.Response(200,text=body)))
    assert emitted == []
    assert message["content"] == ""
    assert message["reasoning_content"] == "synthetic protocol reasoning"
    _, _, payload = model_request(settings,[message,{"role":"tool","tool_call_id":"tool-1","content":"result"}],tools)
    assert payload["messages"][0]["reasoning_content"] == message["reasoning_content"]
    assert usage["total_tokens"] == 12
