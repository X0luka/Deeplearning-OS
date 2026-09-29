import json

import pytest
from test_architecture import fixture_store

from wiki_app.architecture import service
from wiki_app.architecture.reading import reading, semantic_path
from wiki_app.architecture.semantic import build, validate


async def test_search_preserves_neighbors_and_direction(tmp_path):
    store, version = await fixture_store(tmp_path)
    await service.build(store, version)
    view = reading(store, version, query="a.py")
    assert {n["path"] for n in view["nodes"]} == {"src/a.py", "src/b.py"}
    assert len(view["edges"]) == 1
    assert len(reading(store, version, focus="file:src/b.py", direction="incoming")["edges"]) == 1
    assert len(reading(store, version, focus="file:src/b.py", direction="outgoing")["nodes"]) == 1
    assert reading(store, version, query="absent")["nodes"] == []


def test_cross_scope_and_large_graph_remain_visible(tmp_path, monkeypatch):
    from wiki_app.architecture import reading as module
    from wiki_app.architecture.graph import analyze
    from wiki_app.store import Store

    files = {f"src/f{i}.py": "from lib import helper\n" for i in range(160)}
    files["lib/helper.py"] = "def save(): pass\n"
    graph = analyze(files, tmp_path / "cache", "v", "a" * 40)
    graph["analysis_id"] = "analysis"
    monkeypatch.setattr(module, "current", lambda *_: graph)
    view = reading(Store(tmp_path / "data"), "v", scope="src")
    assert len(view["nodes"]) == 161
    assert len(view["edges"]) == 160
    assert any(n["kind"] == "boundary" for n in view["nodes"])


def test_semantic_evidence_must_belong_to_module():
    expected = [{"id": "a"}]
    item = {"id": "a", "title": "名称", "responsibility": "职责", "role": "entry", "evidence": ["E1"]}
    with pytest.raises(ValueError, match="evidence"):
        validate({"modules": [item]}, expected, {"E1": {"module": "b"}})
    with pytest.raises(ValueError, match="module"):
        validate({"modules": [None]}, expected, {})


async def test_semantic_cache_failure_and_backup(tmp_path):
    from wiki_app.backup import backup, restore
    from wiki_app.store import Store

    store, version = await fixture_store(tmp_path / "data")
    await service.build(store, version)
    store.set_meta("model_config", json.dumps({"enabled": True}))
    calls = []

    async def model(settings, payload):
        calls.append(payload)
        return {
            "modules": [
                {
                    "id": m["id"],
                    "title": "模块",
                    "responsibility": "基于证据归纳",
                    "role": "application",
                    "evidence": m["evidence"],
                }
                for m in payload["modules"]
            ]
        }

    await build(store, version, model=model)
    assert 0 < len(calls) <= 6
    assert reading(store, version)["semantic_status"] == "ready"
    assert (await build(store, version, model=model))["cached"]
    path = semantic_path(store, version)
    value = json.loads(path.read_text())
    value["schema_version"] = 0
    path.write_text(json.dumps(value))
    original = path.read_bytes()

    async def failed(*_):
        raise ValueError("model_failed")

    with pytest.raises(ValueError, match="model_failed"):
        await build(store, version, model=failed)
    assert path.read_bytes() == original
    backup(store.root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    assert semantic_path(Store(tmp_path / "restored"), version).read_bytes() == original


async def test_reading_api_requires_analysis_model_and_write_token(tmp_path):
    import httpx

    from wiki_app.main import create_app

    store, version = await fixture_store(tmp_path)
    app = create_app(store.root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        base = f"/api/versions/{version}/reading"
        assert (await client.get(base)).json()["status"] == "missing"
        assert (await client.post(base)).status_code == 403
        client.headers["x-wiki-token"] = app.state.token
        assert (await client.post(base)).status_code == 409
        await service.build(store, version)
        assert len((await client.get(base)).json()["edges"]) == 1
        store.set_meta("model_config", json.dumps({"enabled": False}))
        assert (await client.post(base)).status_code == 422
