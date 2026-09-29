"""Versioned history HTTP mapping with response limits and explicit schema validation."""

import json
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aos.domain import ProductError


class Record(BaseModel):
    model_config = ConfigDict(extra="allow")
    record_uid: str = Field(min_length=1, max_length=128)
    source_interaction_id: int
    revision: int = Field(ge=1)
    source_channel: str
    conversation_id: str | None = None
    created_at: str
    updated_at: str
    status: str
    query: str
    answer: str
    citations: list[dict] = Field(default_factory=list)


class Event(BaseModel):
    event_id: int = Field(ge=1)
    revision: int = Field(ge=1)
    operation: str
    interaction: Record


class Batch(BaseModel):
    contract_version: str
    instance_id: str = Field(min_length=1)
    export_epoch: str = Field(min_length=1)
    events: list[Event] = Field(max_length=100)
    next_cursor: str = Field(min_length=1, max_length=2048)
    has_more: bool


class HistoryHttpAdapter:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with self.client.stream(method, path, **kwargs) as response:
                if response.status_code == 404:
                    raise ProductError("unsupported")
                if response.status_code == 409:
                    raise ProductError("cursor_expired")
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 8 * 1024 * 1024:
                        raise ProductError("response_too_large")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ProductError("schema_incompatible")
            return value
        except httpx.TimeoutException as exc:
            raise ProductError("timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise ProductError("provider_error") from exc
        except httpx.RequestError as exc:
            raise ProductError("unreachable") from exc
        except (ValueError, UnicodeError) as exc:
            raise ProductError("schema_incompatible") from exc

    async def health(self) -> dict:
        stamp = datetime.now(UTC).isoformat()
        try:
            value = await self._request("GET", "/health", timeout=3)
            if value.get("status") not in {"ok", "degraded"}:
                raise ProductError("schema_incompatible")
            return {
                "reachable": True,
                "status": value["status"],
                "observed_at": stamp,
                "checks": {k: str(v).split(":")[0] for k, v in value.get("checks", {}).items()},
            }
        except ProductError as exc:
            return {
                "reachable": False,
                "status": "offline",
                "reason": exc.code,
                "observed_at": stamp,
            }

    async def history(self, page: int = 1, page_size: int = 20) -> dict:
        value = await self._request(
            "GET", "/api/history", params={"page": page, "page_size": page_size}
        )
        if not isinstance(value.get("items"), list) or not isinstance(value.get("total"), int):
            raise ProductError("schema_incompatible")
        return value

    async def detail(self, identity: int) -> dict:
        return await self._request("GET", f"/api/history/{identity}")

    async def query(self, text: str) -> dict:
        raise ProductError("unsupported")

    async def changes(self, cursor: str | None) -> dict:
        value = await self._request(
            "GET", "/aos/v1/changes", params={"cursor": cursor} if cursor else {}
        )
        try:
            batch = Batch.model_validate(value)
        except ValidationError as exc:
            raise ProductError("schema_incompatible") from exc
        if batch.contract_version != "1.0":
            raise ProductError("schema_incompatible")
        ids = [e.event_id for e in batch.events]
        if ids != sorted(set(ids)) or any(
            e.revision != e.interaction.revision or e.operation != "upsert" for e in batch.events
        ):
            raise ProductError("schema_incompatible")
        return batch.model_dump()
