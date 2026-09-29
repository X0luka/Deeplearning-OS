"""RAG public query mapping over the shared history contract."""
from aos.adapters.history import Batch, HistoryHttpAdapter, Record
from aos.domain import ProductError

__all__ = ["Batch", "RagHttpAdapter", "Record"]


class RagHttpAdapter(HistoryHttpAdapter):
    async def query(self, text: str) -> dict:
        value = await self._request(
            "POST",
            "/api/query/sync",
            json={"query": text, "include_history": False, "source_channel": "agentos_probe"},
        )
        if not isinstance(value.get("answer"), str) or not isinstance(
            value.get("interaction_id"), int
        ):
            raise ProductError("schema_incompatible")
        return value
