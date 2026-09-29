"""Appearance is durable, validated and protected independently of business data."""
import httpx

from aos.adapters.sqlite import SQLiteArchive
from aos.host.appearance import Appearance
from aos.host.web import create_app


async def test_appearance_persistence_and_write_protection(tmp_path):
    archive = SQLiteArchive(tmp_path / "archive.db")
    app = create_app(None, None, archive)
    async with httpx.AsyncClient(base_url="http://testserver",
                                transport=httpx.ASGITransport(app)) as client:
        assert (await client.get("/api/v1/appearance")).json() == {"mode": "system"}
        assert (await client.post("/api/v1/appearance", json={"mode": "dark"})).status_code == 403
        headers = {"X-AOS-Token": app.state.write_token}
        rejected = await client.post("/api/v1/appearance", json={"mode": "dark"},
                                     headers={**headers, "Origin": "http://evil.test"})
        assert rejected.status_code == 403
        invalid = await client.post("/api/v1/appearance", json={"mode": "unexpected"},
                                    headers=headers)
        assert invalid.status_code == 422
        for mode in ("dark", "light", "system"):
            response = await client.post("/api/v1/appearance", json={"mode": mode}, headers=headers)
            assert response.json() == {"mode": mode}
            assert Appearance(tmp_path / "appearance.json").read() == {"mode": mode}
        await client.post("/api/v1/appearance", json={"mode": "dark"}, headers=headers)
        assert 'data-mode="dark"' in (await client.get("/")).text
        assert (await client.get("/static/appearance.js")).status_code == 200
    restarted = create_app(None, None, archive)
    async with httpx.AsyncClient(base_url="http://testserver",
                                transport=httpx.ASGITransport(restarted)) as client:
        assert (await client.get("/api/v1/appearance")).json() == {"mode": "dark"}
    assert archive.items("product_ui", 1, None, None)["total"] == 0


def test_invalid_preference_file_recovers_without_deleting_data(tmp_path):
    path = tmp_path / "appearance.json"
    for value in ('broken', '[]', '{"mode":"invalid"}'):
        path.write_text(value)
        appearance = Appearance(path)
        assert appearance.read() == {"mode": "system"}
        appearance.write("light")
        assert appearance.read() == {"mode": "light"}
    assert not list(tmp_path.glob(".appearance-*"))
