import httpx
from test_archive import batch

from aos.adapters.history import HistoryHttpAdapter
from aos.adapters.sqlite import SQLiteArchive
from aos.application import SyncService
from aos.bootstrap import assemble, parse_args


async def test_paper_extension_uses_independent_process_and_public_history(tmp_path):
    args, providers = parse_args(["serve", "--products", "paper", "--data-dir", str(tmp_path)])
    app, _, services, entries = assemble(args, providers)
    try:
        assert set(services) == {"paper"}
        assert isinstance(entries[0].adapter, HistoryHttpAdapter)
        service = entries[0].services[0]
        assert "paper_reader.web" in service.command
        assert str(service.cwd).endswith("PaperReader")
        assert not entries[0].autostart
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
            assert "PaperReader" in (await c.get("/")).text
            assert (await c.get("/products/paper/open")).status_code == 200
    finally:
        for entry in entries:
            await entry.client.aclose()


async def test_paper_archive_replay_and_epoch_rotation(tmp_path):
    first = batch()
    first.update(contract_version="1.0", has_more=False)
    first["events"][0]["interaction"]["created_at"] = "2026-09-17T00:00:00Z"
    current = first
    async def endpoint(request):
        if request.url.params.get("cursor") and current["export_epoch"] != first["export_epoch"]:
            return httpx.Response(409)
        return httpx.Response(200, json=current)
    archive = SQLiteArchive(tmp_path / "archive.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint),base_url="http://paper") as client:
        sync = SyncService("paper",HistoryHttpAdapter(client),archive)
        await sync.run()
        await sync.run()
        assert archive.items(product="paper")["total"] == 1
        current = {**first,"export_epoch":"restored-epoch","next_cursor":"restored:1"}
        await sync.run()
        assert archive.items(product="paper")["total"] == 1
