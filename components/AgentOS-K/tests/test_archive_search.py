"""Real SQLite filtering and HTTP validation, independent of product internals."""
import httpx
from test_archive import batch

from aos.adapters.sqlite import SQLiteArchive
from aos.host.web import create_app


def populate(path):
    archive = SQLiteArchive(path)
    for i in range(25):
        data = batch(uid=f"item-{i}", cursor=f"cursor-{i}")
        data["events"][0]["event_id"] = i + 1
        item = data["events"][0]["interaction"]
        item["query"] = "中文 Attention 100%_" if i % 2 else "Other method"
        item["updated_at"] = "2026-09-20T23:59:59Z" if i < 20 else "2026-09-21T00:00:00Z"
        archive.apply("fixture", data, None if i == 0 else f"cursor-{i-1}")
    return archive


def test_combined_literal_search_and_inclusive_dates(tmp_path):
    a = populate(tmp_path / "archive.db")
    assert a.items()["total"] == 25
    assert len(a.items(page=2)["items"]) == 5
    assert a.items(q="attention")["total"] == 12
    assert a.items(q="%_")["total"] == 12
    assert a.items(q="' OR 1=1 --")["total"] == 0
    assert a.items(q="中文", product="fixture", thread="thread",
                   date_from="2026-09-20", date_to="2026-09-20")["total"] == 10
    assert a.items(date_from="2026-09-21")["total"] == 5
    assert a.items(source="legacy_unknown", q="Attention")["total"] == 0


async def test_history_filters_render_and_validate(tmp_path):
    archive = populate(tmp_path / "archive.db")
    app = create_app(None, None, archive, registry={})
    async with httpx.AsyncClient(base_url="http://testserver", transport=httpx.ASGITransport(app)) as c:
        params = {"q": "中文", "date_from": "2026-09-20", "date_to": "2026-09-20"}
        result = await c.get("/api/v1/history", params=params)
        assert result.status_code == 200 and result.json()["total"] == 10
        page = await c.get("/", params=params)
        assert page.status_code == 200 and 'name="q" value="中文"' in page.text
        assert 'datetime="2026-09-20T23:59:59Z"' in page.text and '已完成' in page.text
        for path in ("/", "/api/v1/history"):
            assert (await c.get(path, params={"q": "Attention", "date_from": "", "date_to": ""})).status_code == 200
            assert (await c.get(path, params={"date_from": "bad"})).status_code == 422
            assert (await c.get(path, params={"date_from": "2026-09-21", "date_to": "2026-09-20"})).status_code == 422
            assert (await c.get(path, params={"q": "x" * 201})).status_code == 422
