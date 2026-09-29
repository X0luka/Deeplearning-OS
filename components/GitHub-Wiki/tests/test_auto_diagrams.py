import asyncio
import copy
import io
import json
import zipfile

import httpx
import pytest

from wiki_app.architecture import auto_diagrams as diagrams
from wiki_app.architecture.knowledge import write_json
from wiki_app.architecture.page_diagrams import decorate, fingerprint
from wiki_app.main import create_app
from wiki_app.store import Store


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    store = Store(tmp_path)
    pages = [
        {"slug": s, "title": s, "markdown": "# Topic\n\nTwo functions cooperate."}
        for s in ("overview", "flows")
    ]
    knowledge = {
        "knowledge_id": "k1",
        "commit": "a" * 40,
        "pages": pages,
        "domains": [],
        "evidence": {},
        "model": {"title": "Fixture"},
    }
    evidence = {"E1": {"path": "main.py", "start": 1, "end": 2, "text": "def main(): return save()"}}
    graph = {
        "section": "0",
        "title": "Entry to save",
        "caption": "Source",
        "nodes": [
            {
                "id": "a",
                "label": "main",
                "explanation": "Entry calls save",
                "basis": "source",
                "evidence": ["E1"],
            },
            {"id": "b", "label": "save", "basis": "source", "evidence": ["E1"]},
        ],
        "edges": [{"source": "a", "target": "b", "label": "calls", "basis": "source", "evidence": ["E1"]}],
    }
    monkeypatch.setattr(diagrams, "load", lambda *a: knowledge)
    monkeypatch.setattr(diagrams, "sources_for", lambda *a: evidence)
    monkeypatch.setattr(diagrams.service, "current", lambda *a: {})
    monkeypatch.setattr(diagrams, "select_sources", lambda *a: evidence)
    settings = {"enabled": True, "model": "fixture", "base_url": "offline"}
    calls = []

    async def model(settings, task, payload):
        calls.append((task, payload))
        if "规划是否" in task:
            return {
                "section": "0",
                "decision": "draw",
                "kind": "flow",
                "question": "How?",
                "reason": "",
                "queries": [],
                "paths": ["main.py"],
            }
        if "独立源码审核" in task:
            return {
                "approved": True,
                "issues": [],
                "read_more": [],
                "checks": [
                    {"object": key, "supported": True, "reason": "Fixture source", "evidence": ["E1"]}
                    for key in diagrams.audit_objects(payload["graph"])
                ],
            }
        return {"graph": copy.deepcopy(graph)}

    return store, knowledge, settings, model, calls


async def test_generate_audit_cache_and_export(fixture):
    store, knowledge, settings, model, calls = fixture
    result = await diagrams.build(store, "v", settings=settings, model=model)
    assert result["counts"]["ready"] == 2 and len(calls) == 6
    artifact = diagrams.published(store, "v")
    assert artifact["review"]["method"] == "model_source_audit"
    assert len(decorate(store, "v", knowledge)[0]["visual_sections"]) == 1
    exported = diagrams.export_markdown(artifact, "https://github.com/example/repo")
    assert "main.py" in exported and "Entry calls save" in exported
    await diagrams.build(store, "v", settings=settings, model=model)
    assert len(calls) == 6
    knowledge["pages"][0]["markdown"] += " changed"
    assert diagrams.published(store, "v") is None
    with pytest.raises(ValueError, match="knowledge_changed"):
        await diagrams.build(store, "v", pages_hash="old", settings=settings, model=model)


async def test_partial_review_failure_is_not_published_as_graph(fixture):
    store, knowledge, settings, original, _calls = fixture

    async def model(s, task, payload):
        if "独立源码审核" in task and payload["question"] == "bad":
            return {"approved": False, "issues": ["Not a call"], "read_more": []}
        result = await original(s, task, payload)
        if "规划是否" in task and payload["page"] == "flows":
            result["question"] = "bad"
        return result

    result = await diagrams.build(store, "v", settings=settings, model=model)
    assert result["counts"]["ready"] == 1 and result["counts"]["review_failed"] == 1
    assert result["partial"] is True
    from wiki_app.pipeline import status
    job = store.enqueue("page_diagrams", {"version": "v"})
    store.finish(job, "succeeded", result)
    summary = status(store, job)
    assert summary["state"] == "partial" and summary["retryable"]
    artifact = diagrams.published(store, "v")
    assert artifact["pages"]["flows"] == []
    assert decorate(store, "v", knowledge)[1]["visual_sections"][0]["graph"] is None


async def test_timeout_keeps_previous_and_manual_retry_resumes(fixture):
    store, knowledge, settings, original, calls = fixture
    prior = {
        "schema_version": 2,
        "knowledge_id": "k1",
        "pages_hash": fingerprint(knowledge),
        "version": "v",
        "commit": knowledge["commit"],
        "review": {"status": "approved"},
        "pages": {},
    }
    write_json(diagrams.folder(store, "v") / "current.json", prior)
    attempts = 0

    async def interrupted(s, task, payload):
        nonlocal attempts
        attempts += 1
        if attempts == 4:
            raise TimeoutError()
        return await original(s, task, payload)

    with pytest.raises(TimeoutError):
        await diagrams.build(store, "v", settings=settings, model=interrupted)
    assert attempts == 4 and diagrams.published(store, "v") == prior
    await diagrams.build(store, "v", settings=settings, model=original)
    assert len(calls) == 6  # completed first section is reused, not billed twice


async def test_skip_invalid_evidence_and_cancel(fixture):
    store, knowledge, settings, original, calls = fixture

    async def skip(s, task, payload):
        result = await original(s, task, payload)
        if "规划是否" in task:
            result.update(decision="not_suitable", reason="Simple text")
        return result

    await diagrams.build(store, "v", settings=settings, model=skip)
    assert len(calls) == 2
    assert diagrams.published(store, "v")["counts"]["not_suitable"] == 2
    knowledge["knowledge_id"] = "k2"

    async def bad(s, task, payload):
        result = await original(s, task, payload)
        if "graph" in result:
            result["graph"]["edges"][0]["evidence"] = ["invented"]
        return result

    result = await diagrams.build(store, "v", settings=settings, model=bad)
    assert result["counts"]["ready"] == 0
    knowledge["knowledge_id"] = "k3"

    async def cancel(*a):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await diagrams.build(store, "v", settings=settings, model=cancel)
    assert diagrams.published(store, "v") is None


def test_queue_deduplicates_and_recovery(fixture):
    store, _knowledge, *_ = fixture
    first = diagrams.enqueue(store, "v")
    assert first == diagrams.enqueue(store, "v")
    store.claim()
    store.recover()
    assert store.job(first)["state"] == "interrupted"
    second = diagrams.enqueue(store, "v")
    assert second != first
    assert store.job(second)["args"]["knowledge_id"] == "k1"


async def test_api_auth_no_read_side_effects_and_export(fixture, monkeypatch):
    store, knowledge, settings, model, _calls = fixture
    with store.db() as db:
        db.execute(
            "INSERT INTO repos VALUES(?,?,?,?)", ("repo", "https://github.com/example/repo", "main", None)
        )
    store.publish(
        "v",
        "repo",
        "a" * 40,
        "model",
        {"files": [], "included": 0, "skipped": [], "bytes": 0},
        knowledge["pages"],
        [],
    )
    monkeypatch.setattr("wiki_app.architecture.knowledge.load", lambda *a: knowledge)
    monkeypatch.setattr(
        "wiki_app.main.config", lambda *a, **k: {"enabled": True, "key_env": "", "model": "fixture"}
    )
    app = create_app(store.root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        assert (await client.post("/api/versions/v/page-diagrams")).status_code == 403
        before = store.rows("SELECT id FROM jobs")
        assert (await client.get("/api/versions/v/page-diagrams")).status_code == 200
        assert store.rows("SELECT id FROM jobs") == before
        client.headers["x-wiki-token"] = app.state.token
        one = (await client.post("/api/versions/v/page-diagrams")).json()
        two = (await client.post("/api/versions/v/page-diagrams")).json()
        assert one == two
        await diagrams.build(store, "v", settings=settings, model=model)
        response = await client.get("/api/versions/v/export")
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            assert {"page-diagrams.json", "page-diagrams.md"} <= set(z.namelist())
            assert json.loads(z.read("page-diagrams.json"))["knowledge_id"] == "k1"


async def test_worker_schedules_diagrams_after_cached_knowledge(fixture, monkeypatch):
    from wiki_app.engine import worker

    store, *_ = fixture

    class Engine:
        def __init__(self):
            self.store = store

        async def run(self, row):
            if row["kind"] == "knowledge":
                return {"version_id": "v", "cached": True}
            return {"version_id": "v"}

    store.enqueue("knowledge", {"version": "v"})
    task = asyncio.create_task(worker(Engine()))
    for _ in range(80):
        if store.rows("SELECT id FROM jobs WHERE kind='page_diagrams'"):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(store.rows("SELECT id FROM jobs WHERE kind='page_diagrams'")) == 1


def test_pipeline_links_all_stages_and_respects_disabled_model(fixture, monkeypatch):
    from wiki_app.engine import schedule_followup

    store, *_ = fixture
    monkeypatch.setattr("wiki_app.engine.config", lambda *a, **k: {"enabled": True})
    job = schedule_followup(store, "build", {"version_id": "v"})["next_job_id"]
    assert store.job(job)["kind"] == "architecture"
    job = schedule_followup(store, "architecture", {"version_id": "v"})["next_job_id"]
    assert store.job(job)["kind"] == "knowledge"
    job = schedule_followup(store, "knowledge", {"version_id": "v", "cached": True})["next_job_id"]
    assert store.job(job)["kind"] == "page_diagrams"
    monkeypatch.setattr("wiki_app.engine.config", lambda *a, **k: {"enabled": False})
    assert "next_job_id" not in schedule_followup(store, "architecture", {"version_id": "other"})


async def test_structured_audit_findings_are_repaired(fixture):
    store, _knowledge, settings, original, _calls = fixture
    audit_calls = 0

    async def model(s, task, payload):
        nonlocal audit_calls
        if "独立源码审核" in task:
            audit_calls += 1
            if audit_calls == 1:
                return {
                    "approved": False,
                    "issues": [{"issue": "wrong label", "evidence": ["E1"]}],
                    "read_more": [],
                }
        if "裁决步骤" in task:
            return {
                "approved": False,
                "issues": [{"issue": "wrong label", "evidence": ["E1"]}],
                "read_more": [],
            }
        return await original(s, task, payload)

    result = await diagrams.build(store, "v", settings=settings, model=model)
    assert result["counts"]["ready"] == 2
    assert audit_calls == 3  # initial review, repaired review, next section review


async def test_malformed_json_gets_one_format_repair(fixture):
    store, _knowledge, settings, original, calls = fixture
    attempted = 0

    async def model(s, task, payload):
        nonlocal attempted
        attempted += 1
        if attempted == 1:
            raise json.JSONDecodeError("bad", "{broken", 1)
        return await original(s, task, payload)

    await diagrams.build(store, "v", settings=settings, model=model)
    assert attempted == 7 and len(calls) == 6


async def test_no_model_never_calls_provider(fixture):
    store, _knowledge, settings, model, calls = fixture
    with pytest.raises(ValueError, match="model_not_configured"):
        await diagrams.build(store, "v", settings={**settings, "enabled": False}, model=model)
    assert calls == []


def test_context_catalog_is_bounded_and_corrupt_artifact_falls_back(fixture):
    store, knowledge, *_ = fixture
    items = [{"path": "very-long-path" * 30, "id": str(i)} for i in range(3000)]
    assert len(json.dumps(diagrams.bounded(items))) < 13000
    target = diagrams.folder(store, "v") / "current.json"
    target.parent.mkdir(parents=True)
    target.write_text("{broken")
    assert decorate(store, "v", knowledge) == knowledge["pages"]


async def test_billing_failure_does_not_retry_or_publish(fixture):
    store, _knowledge, settings, _model, _calls = fixture
    attempted = 0

    async def unavailable(*args):
        nonlocal attempted
        attempted += 1
        raise ValueError("provider_http_402")

    with pytest.raises(ValueError, match="provider_http_402"):
        await diagrams.build(store, "v", settings=settings, model=unavailable)
    assert attempted == 1
    assert diagrams.published(store, "v") is None


def test_status_without_job_preserves_partial_and_disabled(fixture, monkeypatch):
    store, knowledge, *_ = fixture
    monkeypatch.setattr(diagrams, "config", lambda *a, **k: {"enabled": False, "error": "no_model"})
    assert diagrams.status(store, "v")["status"] == "disabled"
    write_json(
        diagrams.folder(store, "v") / "current.json",
        {
            "version": "v",
            "commit": knowledge["commit"],
            "knowledge_id": "k1",
            "pages_hash": fingerprint(knowledge),
            "review": {"status": "approved"},
            "counts": {"ready": 1, "review_failed": 1},
            "pages": {},
        },
    )
    assert diagrams.status(store, "v")["status"] == "partial"
    assert diagrams.published(store, "another-version") is None


async def test_manual_retry_carries_rejection_and_reuses_approved_section(fixture):
    store, _knowledge, settings, original, calls = fixture

    async def reject_flow(s, task, payload):
        if ("独立源码审核" in task or "裁决步骤" in task) and payload["question"] == "bad":
            return {"approved": False, "issues": ["Source target boundary is wrong"], "read_more": []}
        value = await original(s, task, payload)
        if "规划是否" in task and payload["page"] == "flows":
            value["question"] = "bad"
        return value

    await diagrams.build(store, "v", settings=settings, model=reject_flow)
    calls.clear()
    result = await diagrams.build(store, "v", settings=settings, model=original)
    assert result["counts"]["ready"] == 2
    assert len(calls) == 3
    assert calls[0][1]["previous_rejection"] == "Source target boundary is wrong"
    assert calls[1][1]["previous_rejection"] == "Source target boundary is wrong"
    assert calls[1][1]["retry_policy"] == diagrams.REPAIR_POLICY


async def test_flow_page_rejects_composition_plan(fixture):
    store, _knowledge, settings, original, _calls = fixture

    async def composition(s, task, payload):
        value = await original(s, task, payload)
        if "规划是否" in task:
            value["kind"] = "modules"
        return value

    result = await diagrams.build(store, "v", settings=settings, model=composition)
    assert result["counts"]["ready"] == 1
    assert result["counts"]["review_failed"] == 1
    artifact = diagrams.published(store, "v")
    assert artifact["outcomes"]["flows"][0]["reason"] == "diagram_kind_mismatch"


def test_approval_requires_every_claim_and_valid_review_evidence():
    graph = {"nodes": [{"id": "a"}, {"id": "b"}], "edges": [{}]}
    answer = {"approved": True, "issues": [], "read_more": [], "checks": []}
    assert not diagrams.audited(answer, graph, {"E1": {}}).approved
    answer["checks"] = [
        {"object": key, "supported": True, "reason": "Supported", "evidence": ["E1"]}
        for key in diagrams.audit_objects(graph)
    ]
    assert diagrams.audited(answer, graph, {"E1": {}}).approved
    answer["checks"][-1]["evidence"] = ["invented"]
    assert not diagrams.audited(answer, graph, {"E1": {}}).approved
    answer["checks"][-1].update(evidence=["E1"], supported=False, reason="Wrong target role")
    assert not diagrams.audited(answer, graph, {"E1": {}}).approved


def test_queued_retry_does_not_show_prior_completed_progress(fixture):
    store, knowledge, *_ = fixture
    write_json(
        diagrams.folder(store, "v") / "progress.json",
        {
            "version": "v",
            "commit": knowledge["commit"],
            "knowledge_id": knowledge["knowledge_id"],
            "pages_hash": fingerprint(knowledge),
            "done": 3,
            "total": 3,
            "stage": "完成",
        },
    )
    job = diagrams.enqueue(store, "v")
    result = diagrams.status(store, "v")
    assert result["job_id"] == job
    assert result["status"] == "queued"
    assert result["progress"] is None


async def test_no_drawing_needs_no_graph_kind_or_question(fixture):
    store, _knowledge, settings, _original, _calls = fixture
    async def model(s, task, payload):
        if "规划是否" in task:
            return {"section": "0", "decision": "not_suitable", "kind": None,
                    "question": "", "reason": "已由简短文字说明", "paths": [], "queries": []}
        raise AssertionError("A non-drawing section must not generate or audit a graph")
    result = await diagrams.build(store, "v", settings=settings, model=model)
    assert result["counts"]["not_suitable"] == 2
    assert result["counts"]["review_failed"] == 0
    with pytest.raises(ValueError, match="drawing_requires"):
        diagrams.Plan(section="0",decision="draw",reason="",question="",kind=None)


async def test_bad_read_hint_preserves_valid_retrieval_but_never_becomes_evidence(fixture):
    store, _knowledge, settings, original, _calls = fixture
    async def model(s, task, payload):
        result = await original(s, task, payload)
        if "规划是否" in task:
            result["paths"] = ["missing.py", "main.py"]
        return result
    result = await diagrams.build(store, "v", settings=settings, model=model)
    assert result["counts"]["ready"] == 2
    artifact = diagrams.published(store, "v")
    for outcomes in artifact["outcomes"].values():
        assert outcomes[0]["warnings"][0]["rule_id"] == "EVIDENCE.READ_HINT_UNAVAILABLE"
        assert outcomes[0]["plan"]["paths"] == ["main.py"]
    assert all(e["path"] != "missing.py" for e in artifact["evidence"].values())


async def test_overview_planning_and_generation_keep_project_roles(fixture):
    store, knowledge, settings, model, calls = fixture
    knowledge["model"]["nodes"] = [
        {"id": "engine", "title": "Execution", "responsibility": "Coordinates a request",
         "kind": "internal", "evidence": ["E1"]},
        {"id": "storage", "title": "Storage", "responsibility": "Stores results",
         "kind": "external", "evidence": ["E1"]},
    ]
    await diagrams.build(store, "v", settings=settings, model=model)
    planning = next(payload for task, payload in calls if "规划是否" in task and payload["page"] == "overview")
    generation = next(payload for task, payload in calls if "根据给出的固定" in task and payload["page"] == "overview")
    assert {n["id"] for n in planning["project_roles"]} == {"engine", "storage"}
    assert generation["knowledge_context"]["project_roles"] == knowledge["model"]["nodes"]
