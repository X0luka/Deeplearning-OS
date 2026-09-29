"""Read AgentOS's public appearance setting through the product's own origin."""
import httpx
from fastapi.responses import JSONResponse


async def appearance():
    try:
        async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
            response = await client.get("http://127.0.0.1:7777/api/v1/appearance")
            response.raise_for_status()
            value = response.json()
        if isinstance(value, dict) and value.get("mode") in ("light", "dark", "system"):
            return JSONResponse({"mode": value["mode"]}, headers={"Cache-Control": "no-store"})
    except (httpx.HTTPError, ValueError):
        pass
    return JSONResponse({"error": "appearance_unavailable"}, status_code=503)
