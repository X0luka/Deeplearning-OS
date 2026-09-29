import asyncio

import httpx
import pytest

from wiki_app import model_retry


@pytest.fixture(autouse=True)
def instant_retry(monkeypatch):
    monkeypatch.setattr(model_retry, "DELAYS", (0, 0))


async def test_transient_disconnect_recovers_with_bounded_attempts():
    calls = 0
    async def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.RemoteProtocolError("fixture")
        return "valid response"
    assert await model_retry.recover_transport(operation) == "valid response"
    assert calls == 3


async def test_persistent_failure_stops_after_three_attempts():
    calls = 0
    async def operation():
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("fixture")
    with pytest.raises(httpx.ReadTimeout):
        await model_retry.recover_transport(operation)
    assert calls == 3


@pytest.mark.parametrize("code", ["provider_http_401", "provider_http_402",
                                  "knowledge_invalid_evidence", "model_finish_length"])
async def test_configuration_credit_and_quality_errors_not_transport_retried(code):
    calls = 0
    async def operation():
        nonlocal calls
        calls += 1
        raise ValueError(code)
    with pytest.raises(ValueError, match=code):
        await model_retry.recover_transport(operation)
    assert calls == 1


async def test_cancel_during_backoff_stops_before_another_request(monkeypatch):
    waiting = asyncio.Event()
    async def pause(_):
        waiting.set()
        await asyncio.Future()
    monkeypatch.setattr(model_retry.asyncio, "sleep", pause)
    calls = 0
    async def operation():
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("fixture")
    task = asyncio.create_task(model_retry.recover_transport(operation))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 1


async def test_knowledge_request_uses_generation_recovery(monkeypatch):
    from wiki_app.architecture import knowledge
    calls = 0
    async def request_once(settings, task, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("provider_http_503")
        return {"verified": True}
    monkeypatch.setattr(knowledge, "request_once", request_once)
    assert await knowledge.request({}, "fixture", {}) == {"verified": True}
    assert calls == 2


@pytest.mark.parametrize("status", [429, 503])
async def test_http_status_errors_recover_for_direct_provider(status):
    calls = 0
    async def operation():
        nonlocal calls
        calls += 1
        response = httpx.Response(status if calls == 1 else 200,
                                  request=httpx.Request("POST", "https://provider.invalid"))
        response.raise_for_status()
        return "ok"
    assert await model_retry.recover_transport(operation) == "ok"
    assert calls == 2


@pytest.mark.parametrize("status", [400, 401, 402, 403])
async def test_permanent_http_errors_are_not_retried(status):
    calls = 0
    async def operation():
        nonlocal calls
        calls += 1
        httpx.Response(status, request=httpx.Request("POST", "https://provider.invalid")).raise_for_status()
    with pytest.raises(httpx.HTTPStatusError):
        await model_retry.recover_transport(operation)
    assert calls == 1
