import asyncio
import io
import json
import tarfile

import httpx
import pytest

from wiki_app.backup import backup, restore
from wiki_app.engine import Engine, model_answer
from wiki_app.main import create_app
from wiki_app.source import canonical, unpack
from wiki_app.store import Store

FILES = {
    "README.md": "# Fixture\nA task reading project.\nRun pytest for tests.\n",
    "src/main.py": "from .store import save\n\ndef startup():\n    return save('started')\n",
    "src/store.py": "def save(value):\n    return {'record': value}\n",
}
SHA = "a" * 40


async def fixture_fetch(url, ref):
    return (
        SHA,
        FILES,
        {
            "url": url,
            "commit": SHA,
            "included": len(FILES),
            "bytes": sum(len(x.encode()) for x in FILES.values()),
            "skipped": [],
            "files": [{"path": p, "lines": len(t.splitlines())} for p, t in FILES.items()],
        },
    )


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, lambda store: Engine(store, fetcher=fixture_fetch))


async def wait_job(client, identity):
    for _ in range(100):
        job = (await client.get("/api/jobs/" + identity)).json()
        if job["state"] not in {"running", "queued"}:
            return job
        await asyncio.sleep(0.03)
    raise AssertionError("job deadline exceeded")


async def create_version(client):
    r = await client.post("/api/repos", json={"url": "https://github.com/example/fixture"})
    assert r.status_code == 201
    repo = r.json()["repo_id"]
    j = (await client.post(f"/api/repos/{repo}/build")).json()["job_id"]
    job = await wait_job(client, j)
    assert job["state"] == "succeeded", job
    return repo, job["result"]["version_id"]


async def test_complete_reading_history_and_archive(app):
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"x-wiki-token": app.state.token},
        ) as c,
    ):
        repo, version = await create_version(c)
        v = (await c.get("/api/versions/" + version)).json()
        assert len(v["pages"]) == 6 and v["sha"] == SHA and v["mode"] == "source"
        for page in v["pages"]:
            for citation in page["citations"]:
                lines = FILES[citation["source"]].splitlines()
                assert citation["text_preview"] == "\n".join(lines[citation["start"] - 1 : citation["end"]])
                assert SHA in citation["url"]
        assert (await c.get("/versions/" + version)).status_code == 200
        assert (
            await c.get("/api/versions/" + version + "/source", params={"path": "../../wiki.db"})
        ).status_code == 404
        exported = await c.get("/api/versions/" + version + "/export")
        assert exported.content.startswith(b"PK")
        j = (
            await c.post(
                f"/api/versions/{version}/ask",
                json={"question": "启动 startup 流程", "conversation": "thread-a"},
            )
        ).json()["job_id"]
        job = await wait_job(c, j)
        assert job["state"] == "succeeded"
        item = (await c.get("/api/history/" + str(job["result"]["interaction_id"]))).json()
        assert item["citations"] and item["conversation_id"] == "thread-a"
        assert "源码检索模式" in item["answer"]
        assert (await c.get("/history/" + str(item["source_interaction_id"]))).status_code == 200
        batch = (await c.get("/aos/v1/changes")).json()
        assert batch["events"][0]["interaction"] == item
        next_batch = (await c.get("/aos/v1/changes", params={"cursor": batch["next_cursor"]})).json()
        assert not next_batch["events"]
        assert (await c.get("/aos/v1/changes?cursor=bad")).status_code == 409
        again = (await c.post(f"/api/repos/{repo}/build")).json()["job_id"]
        again_result = (await wait_job(c, again))["result"]
        assert {key: again_result[key] for key in ("version_id", "unchanged")} == {
            "version_id": version,
            "unchanged": True,
        }
        assert (await c.get("/api/jobs/" + again_result["next_job_id"])).json()["kind"] == "architecture"


async def test_no_evidence_and_no_thread_pollution(app):
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"x-wiki-token": app.state.token},
        ) as c,
    ):
        _, version = await create_version(c)
        for question, thread in [("startup", "alpha"), ("alien_zebra_nebula", "beta")]:
            j = (
                await c.post(
                    f"/api/versions/{version}/ask", json={"question": question, "conversation": thread}
                )
            ).json()["job_id"]
            assert (await wait_job(c, j))["state"] == "succeeded"
        items = (await c.get("/api/history")).json()["items"]
        assert items[0]["conversation_id"] == "beta" and not items[0]["citations"]
        assert "证据不足" in items[0]["answer"]
        assert items[1]["conversation_id"] == "alpha" and items[1]["citations"]


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://evil.com/a/b",
        "https://github.com/a/b/tree/main",
        "https://github.com/../b",
        "https://user:pass@github.com/a/b",
        "file:///etc/passwd",
    ],
)
def test_reject_urls(url):
    with pytest.raises(ValueError):
        canonical(url)


def test_archive_paths_binary_symlink():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as t:
        for name, text in [
            ("repo/README.md", b"safe"),
            ("repo/../../escape", b"bad"),
            ("repo/binary", b"\x00"),
            ("repo/node_modules/a", b"vendor"),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(text)
            t.addfile(info, io.BytesIO(text))
        link = tarfile.TarInfo("repo/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        t.addfile(link)
    files, manifest = unpack(data.getvalue())
    assert files == {"README.md": "safe"}
    assert len(manifest["skipped"]) == 4


async def test_api_protection_and_xss(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        assert (await c.post("/api/repos", json={"url": "https://github.com/a/b"})).status_code == 403
        assert (
            await c.post(
                "/api/repos",
                headers={"x-wiki-token": app.state.token, "Origin": "https://evil.com"},
                json={"url": "https://github.com/a/b"},
            )
        ).status_code == 403
        assert (
            await c.post(
                "/api/settings",
                headers={"x-wiki-token": app.state.token},
                json={"enabled": True, "base_url": "https://example.com/v1", "model": "test"},
            )
        ).status_code == 422
        assert (await c.get("/", headers={"Host": "evil.com"})).status_code == 400
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False})
    assert "<script>" not in md.render("<script>alert(1)</script>")


async def test_update_failure_keeps_current_and_history(tmp_path):
    store = Store(tmp_path)
    with store.db() as c:
        c.execute("INSERT INTO repos VALUES('r','https://github.com/e/r','',NULL)")
    e = Engine(store, fixture_fetch)
    first = await e.build({"repo": "r"}, "not-queued")
    old = store.version(first["version_id"])

    async def fail_fetch(*args):
        raise ValueError("network_failed")

    e.fetcher = fail_fetch
    with pytest.raises(ValueError):
        await e.build({"repo": "r"}, "not-queued")
    assert store.repo("r")["current"] == old["id"]
    assert (store.root / "wiki" / old["id"] / "index.md").exists()


async def test_cancel_retry_and_restart(tmp_path):
    started = asyncio.Event()

    async def slow(*args):
        started.set()
        await asyncio.sleep(100)

    app = create_app(tmp_path, lambda s: Engine(s, slow))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"x-wiki-token": app.state.token},
        ) as c,
    ):
        repo = (await c.post("/api/repos", json={"url": "https://github.com/e/r"})).json()["repo_id"]
        j = (await c.post(f"/api/repos/{repo}/build")).json()["job_id"]
        await asyncio.wait_for(started.wait(), 2)
        await c.post("/api/jobs/" + j + "/cancel")
        assert (await wait_job(c, j))["state"] == "cancelled"
        retry = (await c.post("/api/jobs/" + j + "/retry")).json()["job_id"]
        assert retry != j
    app.state.store.recover()
    assert app.state.store.job(retry)["state"] in {"interrupted", "queued"}


async def test_backup_restore_integrity(tmp_path):
    store = Store(tmp_path / "data")
    with store.db() as c:
        c.execute("INSERT INTO repos VALUES('r','https://github.com/e/r','',NULL)")
    result = await Engine(store, fixture_fetch).build({"repo": "r"}, "none")
    version = result["version_id"]
    store.record("q", "a", [], version, "thread", "source")
    backup(store.root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.meta("instance") == store.meta("instance")
    assert restored.meta("epoch") != store.meta("epoch")
    assert restored.version(version) == store.version(version)
    assert (restored.root / "raw" / version / "src/main.py").read_text() == FILES["src/main.py"]
    assert restored.changes()["events"][0]["interaction"] == store.changes()["events"][0]["interaction"]
    with pytest.raises(ValueError):
        backup(store.root, tmp_path / "backup")


async def test_model_invalid_citation_rejected(monkeypatch):
    monkeypatch.setenv("WIKI_TEST_KEY", "fixture-key")
    original = httpx.AsyncClient

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["max_tokens"] == 1800
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "claims": [{"text": "unsupported", "evidence": ["E999"]}],
                                    "uncertainties": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )
    settings = {"key_env": "WIKI_TEST_KEY", "base_url": "https://model.test/v1", "model": "fixture"}
    with pytest.raises(ValueError, match="invalid_model_evidence"):
        await model_answer(settings, "question", [])


async def test_real_model_protocol_valid_offline(monkeypatch):
    monkeypatch.setenv("WIKI_TEST_KEY", "fixture-key")
    original = httpx.AsyncClient

    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "claims": [{"text": "入口调用 save。", "evidence": ["E1"]}],
                                    "uncertainties": ["未运行代码"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )
    settings = {"key_env": "WIKI_TEST_KEY", "base_url": "https://model.test/v1", "model": "fixture"}
    citation = {
        "id": "E1",
        "source": "src/main.py",
        "start": 1,
        "end": 4,
        "url": "https://github.com/e/r/blob/" + SHA + "/src/main.py#L1-L4",
    }
    answer, citations = await model_answer(settings, "入口", [citation])
    assert "入口调用 save" in answer and citations == [citation]


async def test_model_build_success_and_failed_update_preserves_current(tmp_path):
    store = Store(tmp_path)
    with store.db() as c:
        c.execute("INSERT INTO repos VALUES('r','https://github.com/e/r','',NULL)")
    store.set_meta("model_config", json.dumps({"enabled": True}))
    calls = []

    async def model(settings, topic, sources):
        calls.append(topic)
        return "已核对的合成说明", sources[:1]

    e = Engine(store, fixture_fetch, model)
    result = await e.build({"repo": "r"}, "none")
    assert len(calls) == 6
    assert store.version(result["version_id"])["mode"] == "model"

    async def fail(*args):
        raise ValueError("invalid_model_evidence")

    e.model = fail
    with pytest.raises(ValueError):
        await e.build({"repo": "r", "force": True}, "none")
    assert store.repo("r")["current"] == result["version_id"]


async def test_changed_commit_renamed_file_retains_old_snapshot(tmp_path):
    store = Store(tmp_path)
    with store.db() as c:
        c.execute("INSERT INTO repos VALUES('r','https://github.com/e/r','',NULL)")
    e = Engine(store, fixture_fetch)
    first = await e.build({"repo": "r"}, "none")

    async def changed(url, ref):
        return (
            "b" * 40,
            {"new.py": "def renamed(): pass"},
            {"included": 1, "bytes": 19, "skipped": [], "files": [{"path": "new.py", "lines": 1}]},
        )

    e.fetcher = changed
    second = await e.build({"repo": "r"}, "none")
    assert second["version_id"] != first["version_id"]
    assert (store.root / "raw" / first["version_id"] / "src/main.py").exists()
    assert (store.root / "raw" / second["version_id"] / "new.py").exists()
    assert store.repo("r")["current"] == second["version_id"]


def test_size_limits_and_empty_archive(monkeypatch):
    from wiki_app import source

    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz"):
        pass
    with pytest.raises(ValueError, match="no_readable_files"):
        unpack(data.getvalue())
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as t:
        content = b"hello world"
        info = tarfile.TarInfo("repo/file.txt")
        info.size = len(content)
        t.addfile(info, io.BytesIO(content))
    monkeypatch.setattr(source, "MAX_TEXT", 3)
    with pytest.raises(ValueError, match="text_size_limit"):
        unpack(data.getvalue())


async def test_github_fetch_errors_are_explicit():
    from wiki_app.source import fetch

    for status, error in [(404, "repository_or_ref_not_found"), (403, "github_rate_limited")]:
        with pytest.raises(ValueError, match=error):
            await fetch(
                "https://github.com/e/r",
                transport=httpx.MockTransport(lambda request, status=status: httpx.Response(status)),
            )


async def test_record_and_event_transaction_rollback(tmp_path):
    store = Store(tmp_path)
    with store.db() as c:
        c.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN "
            "SELECT RAISE(ABORT, 'fixture failure'); END"
        )
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.record("q", "a", [], "v", "thread", "source")
    assert store.rows("SELECT * FROM interactions") == []
    assert store.changes()["events"] == []


async def test_local_line_view(app):
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"x-wiki-token": app.state.token},
        ) as c,
    ):
        _, v = await create_version(c)
        page = await c.get(f"/versions/{v}/source", params={"path": "src/main.py", "start": 3, "end": 4})
        assert page.status_code == 200
        assert 'id="L3"' in page.text and "def startup" in page.text
        assert (await c.get(f"/versions/{v}/source", params={"path": "../../wiki.db"})).status_code == 404


async def test_log_failure_does_not_publish_new_version(tmp_path, monkeypatch):
    from pathlib import Path

    store = Store(tmp_path)
    with store.db() as c:
        c.execute("INSERT INTO repos VALUES('r','https://github.com/e/r','',NULL)")
    engine = Engine(store, fixture_fetch)
    first = await engine.build({"repo": "r"}, "none")
    original_open = Path.open

    def fail_log(path, *args, **kwargs):
        if path == store.root / "wiki" / "log.md" and args and args[0] == "a":
            raise OSError("fixture log failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_log)
    with pytest.raises(OSError):
        await engine.build({"repo": "r", "force": True}, "none")
    assert store.repo("r")["current"] == first["version_id"]
    assert len(store.rows("SELECT id FROM versions")) == 1
