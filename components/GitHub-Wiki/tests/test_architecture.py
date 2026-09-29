import json

from wiki_app.architecture.facts import parse
from wiki_app.architecture.graph import analyze, project


def test_python_relative_imports_and_cache(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/a.py": "from . import b\n", "pkg/b.py": "def save(): pass\n"}
    first = analyze(files, tmp_path, "v", "a" * 40)
    assert [(e["source"], e["target"]) for e in first["edges"]] == [("file:pkg/a.py", "file:pkg/b.py")]
    assert first["edges"][0]["evidence"][0]["start"] == 1
    second = analyze(files, tmp_path, "v", "a" * 40)
    assert second["coverage"]["cache_hits"] == 3
    assert first["edges"] == second["edges"]


def test_ts_alias_tsx_reexport_dynamic_and_config_change(tmp_path):
    files = {
        "tsconfig.json": '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}',
        "src/a.tsx": 'import type {B} from "@/b";\nexport {B} from "./b.js";\nconst f=()=>import("./b"); require(variable);',
        "src/b.ts": "export interface B {}",
        "other/b.ts": "export interface B {}",
    }
    graph = analyze(files, tmp_path, "v", "a" * 40)
    internal = [e for e in graph["edges"] if e["status"] == "internal"]
    assert len(internal) == 3
    assert {e["target"] for e in internal} == {"file:src/b.ts"}
    assert {e["kind"] for e in internal} == {"type", "reexport", "dynamic"}
    assert graph["coverage"]["unresolved"] == 1
    files["tsconfig.json"] = '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["other/*"]}}}'
    changed = analyze(files, tmp_path, "w", "b" * 40)
    assert next(e for e in changed["edges"] if e["kind"] == "type")["target"] == "file:other/b.ts"
    assert changed["coverage"]["cache_hits"] == 3


def test_workspace_package_and_jsonc(tmp_path):
    files = {
        "tsconfig.json": '{// comment\n"compilerOptions":{"baseUrl":".",},}',
        "app.ts": 'import {x} from "@demo/lib";',
        "packages/lib/package.json": json.dumps({"name": "@demo/lib", "types": "src/index.ts"}),
        "packages/lib/src/index.ts": "export const x=1;",
    }
    graph = analyze(files, tmp_path, "v", "a" * 40)
    assert graph["edges"][0]["target"] == "file:packages/lib/src/index.ts"
    assert not graph["coverage"]["issues"]


def test_syntax_errors_unsupported_and_projection(tmp_path):
    assert parse("def !", "python")["errors"]
    files = {
        "a.py": "import other\n",
        "src/b.py": "import os\n",
        "tests/t.py": "import os",
        "image.svg": "<svg/>",
    }
    graph = analyze(files, tmp_path, "v", "a" * 40)
    graph["analysis_id"] = "a"
    assert graph["coverage"]["unsupported"] == 1
    view = project(graph)
    assert any(n["id"] == "module:src" for n in view["nodes"])
    assert not any(n["kind"] == "external" for n in view["nodes"])
    assert view["counts"]["hidden_files"] == 1


def test_large_view_is_explicit(tmp_path):
    graph = analyze({f"src/f{i}.py": "" for i in range(151)}, tmp_path, "v", "a" * 40)
    graph["analysis_id"] = "a"
    view = project(graph, "src", "dependencies")
    assert view["too_large"] and not view["nodes"] and view["counts"]["nodes"] == 151


async def fixture_store(tmp_path):
    from wiki_app.architecture.facts import digest
    from wiki_app.engine import Engine
    from wiki_app.store import Store

    store = Store(tmp_path)
    with store.db() as db:
        db.execute("INSERT INTO repos VALUES('r','https://github.com/example/repo','',NULL)")
    files = {"README.md": "# Fixture", "src/a.py": "from . import b\n", "src/b.py": "def save(): pass\n"}

    async def fetch(url, ref):
        return (
            "a" * 40,
            files,
            {
                "files": [
                    {"path": p, "lines": len(t.splitlines()), "sha256": digest(t.encode())}
                    for p, t in files.items()
                ],
                "included": 3,
                "bytes": 80,
                "skipped": [],
            },
        )

    result = await Engine(store, fetcher=fetch).build({"repo": "r"}, "none")
    return store, result["version_id"]


async def test_real_subprocess_publication_reuse_and_failure(tmp_path, monkeypatch):
    import pytest

    from wiki_app.architecture import service

    store, version = await fixture_store(tmp_path)
    result = await service.build(store, version)
    graph = service.load(store, version)
    assert graph["analysis_id"] == result["analysis_id"]
    assert (await service.build(store, version))["cached"]
    original = (service.directory(store, version) / "current.json").read_bytes()
    monkeypatch.setattr(service, "ANALYZER_VERSION", "next")
    (store.root / "raw" / version / "src/a.py").write_text("tampered")
    with pytest.raises(ValueError, match="snapshot_hash_mismatch") as caught:
        await service.build(store, version)
    assert caught.value.failure["rule_id"] == "STORAGE.INTEGRITY"
    assert (service.directory(store, version) / "current.json").read_bytes() == original


async def test_cancel_kills_analysis_process(tmp_path, monkeypatch):
    import asyncio

    import pytest

    from wiki_app.architecture import service

    store, version = await fixture_store(tmp_path)
    entered = asyncio.Event()

    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            entered.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(service.build(store, version))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed
    assert not (service.directory(store, version) / "current.json").exists()


async def test_api_graph_scope_export_model_and_backup(tmp_path):
    import httpx

    from wiki_app.architecture import service
    from wiki_app.backup import backup, restore
    from wiki_app.engine import Engine
    from wiki_app.main import create_app

    store, version = await fixture_store(tmp_path / "data")
    result = await service.build(store, version)
    app = create_app(store.root)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"x-wiki-token": app.state.token},
    ) as client:
        base = f"/api/versions/{version}/architecture"
        assert (await client.get(f"/versions/{version}/architecture")).status_code == 200
        page = await client.get(f"/versions/{version}/architecture")
        assert "img-src 'self' data:" in page.headers["content-security-policy"]

        view = (await client.get(base)).json()
        assert view["status"] == "ready"
        detail = (
            await client.get(
                base, params={"scope": "src", "view": "dependencies", "node_id": "file:src/a.py"}
            )
        ).json()
        assert detail["detail"]["relations"][0]["target_label"] == "src/b.py"
        assert (await client.get(base, params={"node_id": "module:missing"})).status_code == 422
        assert (await client.get(base + "/export")).json()["analysis_id"] == result["analysis_id"]
        assert "flowchart" in (await client.get(base + "/export?format=mermaid")).text
        filtered = await client.get(base + "/export", params={"format": "mermaid", "query": "absent"})
        assert filtered.text.strip() == "flowchart LR"
        bad = await client.post(
            f"/api/versions/{version}/ask",
            json={"question": "模块职责", "analysis_id": "0" * 64, "node_id": "file:src/a.py"},
        )
        assert bad.status_code == 422
    store.set_meta("model_config", json.dumps({"enabled": True}))

    async def model(settings, topic, sources):
        assert "file:src/a.py -> file:src/b.py" in topic
        assert {x["source"] for x in sources} == {"src/a.py"}
        return "a 导入 b", sources

    answer = await Engine(store, model=model).ask(
        {
            "version": version,
            "question": "模块职责",
            "conversation": "scope",
            "analysis_id": result["analysis_id"],
            "node_id": "file:src/a.py",
        },
        "none",
    )
    record = json.loads(
        store.one("SELECT payload FROM interactions WHERE id=?", (answer["interaction_id"],))["payload"]
    )
    assert record["architecture_context"]["node_id"] == "file:src/a.py"
    backup(store.root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    from wiki_app.store import Store

    restored = Store(tmp_path / "restored")
    assert service.load(restored, version) == service.load(store, version)
    assert restored.meta("epoch") != store.meta("epoch")


async def test_update_check_pinned_and_remote():
    import httpx

    from wiki_app.source import check_update

    assert (await check_update("https://github.com/e/r", "a" * 40))["pinned"]
    result = await check_update(
        "https://github.com/e/r",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"sha": "b" * 40})),
    )
    assert result == {"commit": "b" * 40, "pinned": False}


def test_deleted_renamed_file_does_not_keep_stale_edge(tmp_path):
    first = {"a.ts": 'import "./b";', "b.ts": "export const x=1"}
    old = analyze(first, tmp_path, "old", "a" * 40)
    second = {"a.ts": first["a.ts"], "renamed.ts": first["b.ts"]}
    new = analyze(second, tmp_path, "new", "b" * 40)
    assert old["edges"][0]["target"] == "file:b.ts"
    assert new["edges"][0]["status"] == "unresolved"
    assert new["coverage"]["cache_hits"] == 2


def test_product_proxy_setting_survives_gui_environment(tmp_path, monkeypatch):
    from wiki_app.model_config import config
    from wiki_app.store import Store

    store = Store(tmp_path)
    monkeypatch.delenv("WIKI_HTTPS_PROXY", raising=False)
    store.set_meta("network_proxy", "http://127.0.0.1:7897")
    assert config(store)["proxy"] == "http://127.0.0.1:7897"
    monkeypatch.setenv("WIKI_HTTPS_PROXY", "http://localhost:9999")
    assert config(store)["proxy"] == "http://localhost:9999"


async def test_incoming_dependency_has_importer_evidence(tmp_path):
    from wiki_app.architecture import service
    from wiki_app.engine import Engine

    store, version = await fixture_store(tmp_path)
    graph = await service.build(store, version)
    store.set_meta("model_config", json.dumps({"enabled": True}))

    async def model(settings, topic, sources):
        assert {c["source"] for c in sources} == {"src/a.py", "src/b.py"}
        assert "file:src/a.py -> file:src/b.py" in topic
        return "a imports b", sources

    await Engine(store, model=model).ask(
        {
            "version": version,
            "question": "上游",
            "conversation": "incoming",
            "analysis_id": graph["analysis_id"],
            "node_id": "file:src/b.py",
        },
        "none",
    )
