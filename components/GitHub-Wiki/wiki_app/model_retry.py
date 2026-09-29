"""Bounded recovery for generation-only model transport failures."""
import asyncio
import logging

import httpx

TRANSIENT_STATUS = {"provider_http_408", "provider_http_429", "provider_http_500",
                    "provider_http_502", "provider_http_503", "provider_http_504"}
DELAYS = (2, 5)


async def recover_transport(operation):
    for attempt in range(len(DELAYS) + 1):
        try:
            return await operation()
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError,
                httpx.TimeoutException, httpx.HTTPStatusError, ValueError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and "provider_http_" + str(exc.response.status_code) not in TRANSIENT_STATUS:
                raise
            if isinstance(exc, ValueError) and str(exc) not in TRANSIENT_STATUS:
                raise
            if attempt == len(DELAYS):
                raise
            logging.getLogger("uvicorn.error").warning(
                "Model generation transport recovery: %s, attempt %s/%s",
                str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                attempt + 2, len(DELAYS) + 1)
            await asyncio.sleep(DELAYS[attempt])
