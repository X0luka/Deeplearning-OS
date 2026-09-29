"""GitHub Wiki history mapping; business operations stay in the product UI."""
from aos.adapters.history import HistoryHttpAdapter
from aos.domain import ProductError


class WikiHttpAdapter(HistoryHttpAdapter):
    async def query(self, text: str) -> dict:
        raise ProductError("unsupported")
