"""Data-center scope, truthful sync status, read-only behavior and compatibility."""
import json
import sqlite3

import httpx
import pytest
from test_archive import batch

from aos.adapters.sqlite import SQLiteArchive
from aos.data_center import DataCenter, safe_source_url
from aos.domain import Product
from aos.host.web import create_app


def seed(path):
    store = SQLiteArchive(path)
    for index in range(25):
        value = batch(uid=f"record-{index}", cursor=f"c{index}")
        value["events"][0]["event_id"] = index + 1
        value["events"][0]["interaction"].update(
            query=f"Question {index}", answer="中文答案 100%_" if index % 2 else "Other")
        store.apply("alpha", value, None if index == 0 else f"c{index-1}")
    store.apply("old-product", batch(source="legacy_unknown"), None)
    store.apply("probe", batch(source="agentos_probe"), None)
    return store


def logical_snapshot(store):
    with store.connection() as connection:
        return list(connection.iterdump())


def test_scope_literal_answer_search_and_pagination(tmp_path):
    store = seed(tmp_path / "archive.db")
    center = DataCenter(store)
    before = logical_snapshot(store)
    assert center.records()["total"] == 26
    assert len(center.records(page=2)["items"]) == 6
    assert center.records(source="product_ui")["total"] == 25
    assert center.records(source="legacy_unknown")["total"] == 1
    assert center.records(q="中文", product="alpha", thread="thread")["total"] == 12
    assert center.records(q="%_")["total"] == 12
    assert center.records(q="' OR 1=1 --")["total"] == 0
    assert center.records(product="missing")["total"] == 0
    assert center.detail(27) is None
    assert logical_snapshot(store) == before


def test_latest_sync_status_not_hidden_by_other_products(tmp_path):
    store = seed(tmp_path / "archive.db")
    failed = store.enqueue("alpha")
    store.finish(failed, "interrupted")
    for _ in range(35):
        store.finish(store.enqueue("another"), "succeeded")
    rows = {p["product"]: p for p in DataCenter(store).records()["sources"]}
    assert rows["alpha"]["sync_state"] == "interrupted"
    assert rows["alpha"]["observed_at"] is not None
    assert rows["another"]["observed_at"] is None
    assert rows["another"]["sync_state"] == "succeeded"
    assert rows["old-product"]["sync_state"] is None


def test_archive_identity_cannot_be_overridden_by_payload(tmp_path):
    store = SQLiteArchive(tmp_path / "archive.db")
    value = batch()
    value["events"][0]["interaction"].update(archive_id=999, product="wrong",
                                             rollback_detected=True)
    store.apply("alpha", value, None)
    item = DataCenter(store).detail(1)
    assert item["archive_id"] == 1 and item["product"] == "alpha"
    assert item["rollback_detected"] is False
    assert DataCenter(store).records()["items"][0] == item


async def test_routes_do_not_probe_products_and_export_original_record(tmp_path):
    store = seed(tmp_path / "archive.db")
    class Unavailable:
        async def health(self):
            pytest.fail("data reads must not probe products")
    product = Product("alpha", "Fixture App", "http://127.0.0.1:1", "http://127.0.0.1:2")
    app = create_app(product, Unavailable(), store)
    before = logical_snapshot(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        page = await c.get("/data")
        assert page.status_code == 200
        assert "数据与知识中心" in page.text and "不代表全部论文" in page.text
        assert 'href="/products/alpha/open"' in page.text
        assert "agentos_probe" not in page.text
        result = (await c.get("/api/v1/data/records", params={"q": "中文"})).json()
        assert result["total"] == 12
        detail = await c.get("/data/26")
        assert "来源应用当前未加载" in detail.text
        assert "&lt;script&gt;unsafe" in detail.text
        exported = await c.get("/api/v1/data/records/26/export")
        assert exported.headers["content-disposition"].endswith('"agentos-record-26.json"')
        assert exported.json()["record"] == store.detail(26)
        assert exported.headers["cache-control"] == "no-store"
        for path in ("/data/27", "/api/v1/data/records/27", "/api/v1/data/records/27/export",
                     "/data/999"):
            assert (await c.get(path)).status_code == 404
    assert logical_snapshot(store) == before


@pytest.mark.parametrize("params", [{"source":"agentos_probe"}, {"page":0},
    {"q":"x"*201}, {"product_id":"x"*201}, {"thread":"x"*65}])
async def test_invalid_filters(tmp_path, params):
    app = create_app(None, None, SQLiteArchive(tmp_path / "a.db"), registry={})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        for path in ("/data", "/api/v1/data/records"):
            assert (await c.get(path, params=params)).status_code == 422


async def test_empty_offline_and_unrecognized_status_are_explicit(tmp_path):
    store = SQLiteArchive(tmp_path / "a.db")
    app = create_app(None, None, store, registry={})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        page = await c.get("/data")
        assert page.status_code == 200 and 'id="knowledge-center"' in page.text
        assert (await c.get("/api/v1/data/items")).json()["total"] == 0
        store.finish(store.enqueue("unknown"), "new-state")
        status = (await c.get("/api/v1/data/status")).json()
        assert status["sources"][0]["sync_state"] == "new-state"
        assert status["sources"][0]["observed_at"] is None
        assert (await c.get("/api/v1/data/items?q=absent")).json()["total"] == 0
        assert (await c.get("/")).status_code == 200



@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,unsafe",
    "//evil.test", "https://user:pass@example.com", "https://[", "https://x\n.test", None])
def test_unsafe_citation_links(url):
    assert safe_source_url(url) is None


async def test_citation_links_and_no_schema_change(tmp_path):
    store = SQLiteArchive(tmp_path / "a.db")
    with store.connection() as c:
        schema = c.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    value = batch()
    value["events"][0]["interaction"]["citations"] = [
        {"source":"<img src=x onerror=alert(1)>", "url":"javascript:alert(1)"},
        {"source":"safe", "url":"https://example.com/source"}]
    store.apply("alpha", value, None)
    app = create_app(None, None, store, registry={})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        page = await c.get("/data/1")
        assert 'href="javascript:' not in page.text
        assert '&lt;img' in page.text and 'href="https://example.com/source"' in page.text
    with store.connection() as c:
        assert c.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == schema
    # Baseline-compatible SQL still retrieves the same unmodified payload.
    with sqlite3.connect(store.path) as c:
        payload = json.loads(c.execute("SELECT payload FROM records WHERE id=1").fetchone()[0])
    assert payload == value["events"][0]["interaction"]
