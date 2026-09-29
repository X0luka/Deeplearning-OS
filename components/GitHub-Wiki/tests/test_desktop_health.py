"""Desktop exit advisory is read-only and does not dispatch queued work."""
import httpx
import pytest

from wiki_app.main import create_app


@pytest.mark.asyncio
async def test_health_busy_with_pending_jobs(tmp_path):
    app = create_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url='http://testserver') as client:
        assert (await client.get('/health')).json()['busy'] is False
        app.state.store.enqueue('desktop-offline-fixture', {'fixture': True})
        assert (await client.get('/health')).json()['busy'] is True
