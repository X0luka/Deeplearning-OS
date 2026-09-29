"""Failure isolation and deletion tests use temporary stores only."""
import io
import json
import tarfile

import httpx
import pytest
from test_knowledge import fake_model, prepared
from test_product import fixture_fetch

from wiki_app import source
from wiki_app.architecture import knowledge
from wiki_app.engine import Engine
from wiki_app.main import create_app
from wiki_app.repository_data import compact, execute_delete, preview, request_delete
from wiki_app.store import Store


def archive(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for path, value in files.items():
            info = tarfile.TarInfo("repo/"+path)
            info.size = len(value)
            tar.addfile(info, io.BytesIO(value))
    return out.getvalue()


def test_local_limit_preserves_other_files(monkeypatch, tmp_path):
    monkeypatch.setattr(source, "MAX_FILE", 12)
    files, manifest = source.unpack(archive({"large.py": b"x"*20, "src/main.py": b"print(1)"}), tmp_path)
    assert files["src/main.py"] == "print(1)"
    assert list(files) == ["src/main.py"]
    assert manifest["partial"]
    assert manifest["skipped"][0]["reason"] == "file_size_limit"
    assert manifest["skipped"][0]["bytes"] == 20


def test_scan_limit_and_traversal(monkeypatch, tmp_path):
    files, _manifest = source.unpack(archive({"../escape.py": b"oops", "main.py": b"ok"}), tmp_path)
    assert list(files) == ["main.py"]
    assert not (tmp_path.parent/"escape.py").exists()
    monkeypatch.setattr(source, "MAX_SCAN", 50)
    with pytest.raises(source.RuleError, match="archive_scan_limit") as caught:
        source.unpack(archive({"main.py": b"ok"}), tmp_path/"second")
    assert caught.value.failure["rule_id"] == "INGEST.SCAN_BYTES"


async def fixture(tmp_path):
    store = Store(tmp_path)
    with store.db() as c:
        c.execute("INSERT INTO repos VALUES('r','https://github.com/example/fixture','',NULL)")
        c.execute("INSERT INTO repos VALUES('other','https://github.com/example/other','',NULL)")
    engine = Engine(store, fetcher=fixture_fetch)
    result = await engine.build({"repo": "r"}, "build-test")
    version = result["version_id"]
    store.record("question", "answer", [{"source": "src/main.py", "text_preview": "retained"}],
                 version, "thread", "source")
    (store.root/"raw/other-version").mkdir()
    (store.root/"raw/other-version/keep.txt").write_text("keep")
    return store, version


async def test_delete_preserves_history_and_other_repository(tmp_path):
    store, version = await fixture(tmp_path)
    p = preview(store, "r")
    with pytest.raises(ValueError, match="deletion_confirmation_required"):
        request_delete(store, "r", p["preview_token"], "wrong")
    job = request_delete(store, "r", p["preview_token"], p["name"])["job_id"]
    with pytest.raises(ValueError, match="repo_deleting"):
        store.enqueue("knowledge", {"version": version})
    result = execute_delete(store, "r", job)
    assert result["history_retained"] and not store.repo("r")
    assert store.repo("other")
    assert not (store.root/"raw"/version).exists()
    assert (store.root/"raw/other-version/keep.txt").read_text() == "keep"
    item = json.loads(store.one("SELECT payload FROM interactions")["payload"])
    assert item["source_deleted"] and item["answer"] == "answer"
    assert item["citations"][0]["text_preview"] == "retained"
    assert not store.rows("SELECT * FROM chunks WHERE version=?", (version,))
    assert execute_delete(store, "r", job)["deleted_repo"] == "r"
    store.finish(job, "succeeded", result)
    compact(store)
    assert store.meta("database_compaction_pending") == "false"
    app = create_app(store.root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        response = await c.get("/history/1")
        assert response.status_code == 200 and "来源仓库已删除" in response.text
        assert (await c.get("/versions/"+version)).status_code == 410


async def test_busy_and_changed_preview_rejected(tmp_path):
    store, version = await fixture(tmp_path)
    original = preview(store, "r")
    job = store.enqueue("knowledge", {"version": version})
    with pytest.raises(ValueError, match="deletion_preview_changed"):
        request_delete(store, "r", original["preview_token"], original["name"])
    current = preview(store, "r")
    with pytest.raises(ValueError, match="repo_busy"):
        request_delete(store, "r", current["preview_token"], current["name"])
    store.finish(job, "cancelled")


async def test_delete_failure_resumes_and_rejects_symlinks(tmp_path, monkeypatch):
    store, version = await fixture(tmp_path)
    p = preview(store, "r")
    job = request_delete(store, "r", p["preview_token"], p["name"])["job_id"]
    import wiki_app.repository_data as data
    original = data.shutil.rmtree
    def fail(path):
        if str(path).endswith("wiki/"+version):
            raise PermissionError("locked")
        original(path)
    monkeypatch.setattr(data.shutil, "rmtree", fail)
    with pytest.raises(PermissionError):
        execute_delete(store, "r", job)
    assert store.repo("r")
    assert json.loads(store.meta("deletion:r"))["state"] == "incomplete"
    monkeypatch.setattr(data.shutil, "rmtree", original)
    assert execute_delete(store, "r", job)["history_retained"]


async def test_topic_failure_does_not_stop_other_topics(tmp_path):
    store, version = await prepared(tmp_path)
    base = fake_model()
    visited = []
    async def model(settings, task, payload):
        if payload.get("topic"):
            name = payload["topic"]["id"]
            visited.append(name)
            if name == "request":
                result = await base(settings, task, payload)
                result["claims"][0]["evidence"] = ["wrong"]
                return result
        if "allowed_evidence" in payload:
            raise ValueError("knowledge_unmapped_domain")
        return await base(settings, task, payload)
    result = await knowledge.build(store, version, model=model)
    assert result["partial"]
    assert "storage" in visited and "operations" in visited
    value = knowledge.load(store, version)
    assert any(d["id"] == "storage" for d in value["domains"])
    assert not any(d["id"] == "request" for d in value["domains"])
    assert value["failures"][0]["failure"]["rule_id"] == "EVIDENCE.UNKNOWN_ID"


async def test_invalid_cached_reference_is_not_reused(tmp_path):
    store, version = await prepared(tmp_path)
    await knowledge.build(store, version, model=fake_model())
    target = next(knowledge.directory(store, version).glob("stages/*/request-0-*.json"))
    value = json.loads(target.read_text())
    value["claims"][0]["evidence"] = ["truncated-id"]
    target.write_text(json.dumps(value))
    await knowledge.build(store, version, model=fake_model(), refresh=True)
    repaired = json.loads(target.read_text())
    assert repaired["claims"][0]["evidence"] != ["truncated-id"]
    assert target.with_suffix(".invalid.json").exists()

def test_disk_snapshot_preserves_crlf_hash(tmp_path):
    import hashlib
    original = b"first\r\nsecond\r\n"
    files, manifest = source.unpack(archive({"main.py": original}), tmp_path)
    assert files["main.py"].encode() == original
    assert hashlib.sha256(files["main.py"].encode()).hexdigest() == manifest["files"][0]["sha256"]

def test_delete_rejects_paths_outside_exact_owned_directory(tmp_path):
    from wiki_app.repository_data import bounded
    for value in ("raw/../wiki", "raw/../../outside", "wiki", "/tmp", "cache/syntax/x"):
        with pytest.raises(ValueError, match="delete_unsafe_path"):
            bounded(tmp_path, value)


def test_file_count_limit_is_local(monkeypatch):
    monkeypatch.setattr(source, "MAX_FILES", 1)
    files, manifest = source.unpack(archive({"first.py": b"first", "second.py": b"second"}))
    assert list(files) == ["first.py"] and manifest["partial"]
    assert manifest["skipped"][0]["reason"] == "file_count_limit"

async def test_partial_knowledge_retry_is_explicit_and_refreshes(tmp_path):
    store, version = await prepared(tmp_path)
    job = store.enqueue("knowledge", {"version": version})
    store.finish(job, "succeeded", {"version_id": version, "partial": True, "preserved_previous": True})
    app = create_app(store.root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as c:
        response = await c.post("/api/jobs/"+job+"/retry", headers={"X-Wiki-Token": app.state.token})
        assert response.status_code == 202
        retried = store.job(response.json()["job_id"])
        assert retried["args"] == {"version": version, "refresh": True}


def test_large_plan_merge_preserves_all_paths_and_rejects_missing_topics():
    topics = [{"id": f"p{i}", "question": "entry", "paths": [f"src/{i}"], "queries": ["run"]} for i in range(12)]
    result = knowledge.consolidate_topics(topics, {"groups": [{"members": list(range(6))}, {"members": list(range(6, 12))}]})
    assert len(result) == 2
    assert {p for t in result for p in t["paths"]} == {f"src/{i}" for i in range(12)}
    for groups in ([{"members": [0]}, {"members": [1]}], [{"members": list(range(12))}, {"members": [0]}]):
        with pytest.raises(ValueError, match="knowledge_invalid_research_plan"):
            knowledge.consolidate_topics(topics, {"groups": groups})


async def test_initial_page_error_keeps_other_pages_and_continues(tmp_path):
    store = Store(tmp_path)
    with store.db() as c:
        c.execute("INSERT INTO repos VALUES('r','https://github.com/e/r','',NULL)")
    store.set_meta("model_config", json.dumps({"enabled": True}))
    calls = []
    async def model(settings, topic, sources):
        calls.append(topic)
        if len(calls) == 2:
            raise ValueError("invalid_model_evidence")
        return "fixture verified claim", sources[:1]
    result = await Engine(store, fixture_fetch, model).build({"repo": "r"}, "fixture-build")
    assert len(calls) == 6 and result["partial"]
    version = store.version(result["version_id"])
    assert len(version["pages"]) == 6
    assert "本节模型说明未完成" in version["pages"][1]["markdown"]
    assert "fixture verified claim" in version["pages"][2]["markdown"]
    from wiki_app.engine import schedule_followup
    followup = schedule_followup(store, "build", result)
    assert store.job(followup["next_job_id"])["kind"] == "architecture"


async def test_disconnected_steps_are_not_published_as_a_flow(tmp_path):
    store, version = await prepared(tmp_path)
    base = fake_model()
    async def model(settings, task, payload):
        result = await base(settings, task, payload)
        if "read_more" in result and payload.get("topic", {}).get("id") == "request":
            result["steps"] = result["steps"] * 2
        if "subject_domain" in payload:
            for check in result["checks"]:
                if check["kind"] == "transition":
                    check.update(supported=False, reason="independent entrypoints have no causal link")
        return result
    result = await knowledge.build(store, version, model=model)
    assert result["partial"]
    current = knowledge.load(store, version)
    request = next(d for d in current["domains"] if d["id"] == "request")
    assert request["claims"] and not request["steps"]
    assert any("independent entrypoints" in gap for gap in request["gaps"])


async def test_incomplete_audit_is_repaired_before_caching(tmp_path):
    store, version = await prepared(tmp_path)
    base = fake_model()
    incomplete = False
    corrections = 0
    async def model(settings, task, payload):
        nonlocal incomplete, corrections
        result = await base(settings, task, payload)
        if payload.get("subject_domain") and not incomplete:
            incomplete = True
            result["checks"] = []
        elif payload.get("subject_domain") and "invalid_response" in payload:
            corrections += 1
        return result
    result = await knowledge.build(store, version, model=model)
    assert corrections == 1 and not result["partial"]
    assert all(d["claims"] for d in knowledge.load(store, version)["domains"])


def test_extra_audit_item_is_local_noise_but_real_rejection_survives():
    domain = {"claims": [{"text": "claim"}], "steps": [{"text": "step"}]}
    result = {"flow_consistent": True, "checks": [
        {"kind": "claim", "index": 0, "supported": False, "reason": "real error"},
        {"kind": "step", "index": 0, "supported": True, "reason": "valid"},
        {"kind": "transition", "index": 0, "supported": False, "reason": "nonexistent extra"}]}
    knowledge.validate_audit_coverage(result, domain)
    assert len(result["checks"]) == 2 and result["checks"][0]["supported"] is False
    assert result["protocol_notice"]


def test_boundary_describes_nodes_without_inventing_missing_implementation():
    result = knowledge.boundary_from_nodes([
        {"kind": "internal", "title": "界面与服务", "evidence": ["E1"]},
        {"kind": "external", "title": "模型服务", "evidence": ["E2"]}])
    assert "仓库内模块：界面与服务" in result["text"]
    assert "外部协作对象：模型服务" in result["text"]
    assert "不负责" not in result["text"] and result["evidence"] == ["E1", "E2"]


async def test_cosmetic_overview_length_does_not_block_publication(tmp_path):
    store, version = await prepared(tmp_path)
    base = fake_model()
    async def model(settings, task, payload):
        if "最多100字符" in task:
            return {"summary": "unusable shortening "*100, "boundary": None}
        return await base(settings, task, payload)
    result = await knowledge.build(store, version, model=model)
    current = knowledge.load(store, version)
    assert not result["partial"]
    assert current["model"]["summary"]["text"] == "协调请求并保存数据"
    assert any(t.get("notice") for t in current["trace"])


async def test_unsupported_scope_does_not_discard_verified_claims(tmp_path):
    from test_knowledge import fake_model, prepared

    from wiki_app.architecture import knowledge
    store, version = await prepared(tmp_path)
    base = fake_model()
    async def model(settings, task, payload):
        result = await base(settings, task, payload)
        if "subject_domain" in payload:
            for check in result["checks"]:
                if check["kind"] == "scope":
                    check.update(supported=False, reason="局部源码不能证明唯一外发通道或不存在其他实现")
        elif "responsibility" in result:
            result["responsibility"] = "这是项目唯一外发通道"
            result["boundary"] = "项目不存在其他网络实现"
        return result
    result = await knowledge.build(store, version, model=model)
    assert result["partial"]
    value = knowledge.load(store, version)
    assert value["domains"]
    for domain in value["domains"]:
        assert domain["claims"]
        assert "唯一外发通道" not in domain["responsibility"]
        assert "不存在其他网络实现" not in domain["boundary"]
    assert any(c["kind"] == "scope" for f in value["failures"] for c in f.get("issues", []))


def test_large_repo_can_keep_independent_topics_without_dropping_coverage():
    topics = [{"id": f"p{i}", "paths": [f"src/{i}"], "queries": [f"capability{i}"]} for i in range(16)]
    merged = knowledge.consolidate_topics(topics, {"groups": [{"members": [i]} for i in range(16)]})
    assert len(merged) == 16
    assert {p for t in merged for p in t["paths"]} == {f"src/{i}" for i in range(16)}
    from wiki_app.rules import RuleError
    with pytest.raises(RuleError) as error:
        knowledge.consolidate_topics(topics + [{"id": "p16"}], {"groups": [{"members": [i]} for i in range(17)]})
    assert error.value.failure["limit"] == 16
    assert error.value.failure["observed"] == 17


async def test_overview_evidence_selection_bounds_combined_payload():
    sources = {f"E{i}": {"path": f"source{i}.py", "start": 1, "end": 60, "text": "x" * 9000}
               for i in range(40)}
    calls = []
    async def select(payload):
        calls.append(payload)
        assert len(json.dumps(payload,ensure_ascii=False)) < 96000
        return {"indexes": list(range(payload["max_indexes"]))}
    chosen = await knowledge.compact_audit_sources(sources, {"text": "review"}, select)
    assert len(json.dumps(chosen,ensure_ascii=False)) <= 65000
    assert max(c["round"] for c in calls) >= 1
    assert all(value == sources[key] for key,value in chosen.items())
    assert len(json.dumps({"subject": {"text": "review"}, "sources": chosen},ensure_ascii=False)) < 96000


async def test_overview_selection_rejects_invented_or_empty_evidence():
    sources = {f"E{i}": {"path": f"{i}.py", "text": "x" * 40000} for i in range(3)}
    async def invented(payload):
        return {"indexes": [999]}
    async def empty(payload):
        return {"indexes": []}
    with pytest.raises(ValueError,match="selection_invalid"):
        await knowledge.compact_audit_sources(sources, {}, invented)
    with pytest.raises(ValueError,match="no_evidence"):
        await knowledge.compact_audit_sources(sources, {}, empty)


def test_retrieval_reserves_implementation_despite_keyword_heavy_docs():
    sources = {}
    for i in range(20):
        sources[f"D{i}"] = {"path": f"docs/{i}.md", "start": 1, "end": 60, "text": "agent loop turn " * 100}
    sources["CODE"] = {"path": "packages/agent/src/loop.ts", "start": 1, "end": 60,
                       "text": "async function turn() { return request(); }"}
    topic = {"paths": [f"docs/{i}.md" for i in range(20)]+["packages/agent"], "queries": ["agent loop turn"]}
    selected = knowledge.retrieve(sources, topic, {"edges": []})
    assert "CODE" in selected
    assert any(k.startswith("D") for k in selected)


def test_directed_reads_use_real_snapshot_paths_and_bounded_windows():
    sources = {f"E{i}": {"path": "packages/agent/src/loop.ts", "start": i*60+1,
                          "end": i*60+60, "text": "code"} for i in range(5)}
    assert knowledge.source_window(sources,"packages/agent/src/loop.ts",61,set()) == ["E1","E2","E3"]
    assert knowledge.source_window(sources,"packages/agent/src",1,{"E0"}) == ["E1","E2"]
    assert knowledge.source_window(sources,"../../outside",1,set()) == []
    assert knowledge.source_window(sources,"imaginary.ts",1,set()) == []


def test_navigation_finds_deep_implementation_without_sequential_paging():
    sources = {
        "header": {"path": "lib/engine.py", "start": 1, "end": 60, "text": "import typing"},
        "body": {"path": "lib/engine.py", "start": 2401, "end": 2460,
                 "text": "    async def execute_turn(self):\n        return await model.respond()"},
        "unrelated": {"path": "other.py", "start": 1, "end": 60, "text": "def execute_turn(): pass"},
    }
    locations = knowledge.source_navigation(sources, {"lib/engine.py"}, ["execute turn"])
    assert locations == [{"path": "lib/engine.py", "symbol": "execute_turn", "start": 2401}]
    assert knowledge.source_window(sources, locations[0]["path"], locations[0]["start"], set()) == ["body"]
    assert knowledge.source_navigation(sources, {"lib/engine.py"}, ["execute"], limit=20) == []


def test_complete_revision_budget_preserves_claims_and_whole_evidence():
    payload = {"model": {"summary": "s" * 20000}, "domains": [{"claims": ["c" * 21000]}],
               "issues": ["unsupported"]}
    sources = {f"E{i}": {"path": f"lib/{i}.py", "start": 1, "end": 60, "text": "x" * 8500}
               for i in range(12)}
    value = knowledge.source_payload(payload, sources, list(sources))
    assert len(json.dumps(value, ensure_ascii=False)) <= 90000
    assert {k: v for k, v in value.items() if k != "sources"} == payload
    assert 0 < len(value["sources"]) < len(sources)
    assert all(value["sources"][key] == sources[key] for key in value["sources"])


def test_directed_search_finds_late_branch_and_respects_scope_and_seen():
    sources = {
        "early": {"path": "core/loop.py", "text": "import typing"},
        "late": {"path": "core/loop.py", "text": "if isinstance(step, FinalOutput): return result"},
        "other": {"path": "other.py", "text": "FinalOutput " * 100},
    }
    assert knowledge.search_window(sources, "core/loop.py", "FinalOutput", set()) == ["late"]
    assert knowledge.search_window(sources, "core/loop.py", "FinalOutput", {"late"}) == []
    assert knowledge.search_window(sources, "../outside", "FinalOutput", set()) == []
