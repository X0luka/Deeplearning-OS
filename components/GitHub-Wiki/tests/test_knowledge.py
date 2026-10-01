import json

import httpx
import pytest
from test_architecture import fixture_store

from wiki_app.architecture import knowledge, service
from wiki_app.main import create_app


def fake_model(approved=True):
    async def model(settings, task, payload):
        if "subject_domain" in payload:
            d = payload["subject_domain"]
            key = next(iter(payload["sources"]))
            return {"flow_consistent": True, "flow_reason": "fixture sequence", "checks": [{"kind": kind, "index": i, "supported": True,
                                "reason": "fixture implementation supports this claim", "evidence": [key]}
                               for kind, field in (("claim", "claims"), ("step", "steps"))
                               for i, _ in enumerate(d[field])] + [
                {"kind": "transition", "index": i, "supported": True,
                 "reason": "fixture direct call", "evidence": [key]}
                for i in range(max(0, len(d["steps"])-1))] + [
                {"kind": "scope", "index": i, "supported": True,
                 "reason": "fixture bounded scope", "evidence": [key]}
                for i in range(2)]}
        if "核对并修订研究笔记" in task:
            result = dict(payload["note"])
            key = next(iter(payload["sources"]))
            result["claims"] = [{"text": c["text"], "evidence": [key]} for c in result["claims"]]
            result["steps"] = [{"text": c["text"], "evidence": [key]} for c in result["steps"]]
            return result
        if "最多100字符" in task:
            return {"summary": payload["summary"], "boundary": payload["boundary"]}
        if "topics" in task:
            return {
                "title": "示例系统",
                "topics": [
                    {
                        "id": name,
                        "title": title,
                        "question": title,
                        "queries": ["save", "import"],
                        "paths": ["src/a.py", "src/b.py"],
                    }
                    for name, title in [("request", "请求协调"), ("storage", "数据保存")]
                ],
            }
        if "read_more" in task:
            key = next(iter(payload["sources"]))
            return {
                "id": payload["topic"]["id"],
                "title": payload["topic"]["title"],
                "responsibility": "处理此项功能",
                "boundary": "只描述当前证据范围",
                "claims": [{"text": "存在对应实现", "evidence": [key]}],
                "steps": [{"text": "执行已定义操作", "evidence": [key]}],
                "gaps": [],
                "read_more": [],
            }
        if "approved" in task:
            return {"approved": approved, "issues": [] if approved else ["边界不清楚"]}
        key = next(iter(payload.get("sources", {})))
        return {
            "title": "示例系统",
            "summary": {"text": "协调请求并保存数据", "evidence": [key]},
            "boundary": {"text": "仅负责本地功能", "evidence": [key]},
            "nodes": [
                {
                    "id": name,
                    "title": title,
                    "responsibility": "处理功能",
                    "kind": "internal",
                    "domains": [name],
                    "evidence": [key],
                }
                for name, title in [("request", "请求协调"), ("storage", "数据保存")]
            ],
            "relations": [
                {
                    "source": "request",
                    "target": "storage",
                    "label": "请求保存",
                    "explanation": "通过接口传递请求",
                    "evidence": [key],
                    "basis": "inferred",
                }
            ],
            "omissions": ["测试配置留在运行章节"],
            "questions": [],
        }

    return model


async def prepared(tmp_path):
    store, version = await fixture_store(tmp_path)
    await service.build(store, version)
    store.set_meta("model_config", json.dumps({"enabled": True}))
    return store, version


async def test_cached_knowledge_pages_api_export_and_recovery(tmp_path):
    from wiki_app.backup import backup, restore
    from wiki_app.store import Store

    store, version = await prepared(tmp_path / "data")
    await knowledge.build(store, version, model=fake_model())
    value = knowledge.load(store, version)
    assert value["model"]["nodes"][0]["domains"] == ["request"]
    assert (await knowledge.build(store, version, model=fake_model()))["cached"]
    app = create_app(store.root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        base = f"/api/versions/{version}"
        assert (await client.get(base + "/knowledge")).json()["knowledge_id"] == value["knowledge_id"]
        assert "协调请求并保存数据" in (await client.get(f"/versions/{version}")).text
        assert (await client.post(base + "/knowledge")).status_code == 403
        response = await client.get(
            base + "/reading", params={"knowledge_id": value["knowledge_id"], "entity_id": "request"}
        )
        assert response.status_code == 200
        assert (await client.get(base + "/reading", params={"entity_id": "request"})).status_code == 422
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO((await client.get(base + "/export")).content)) as archive:
            assert json.loads(archive.read("knowledge.json"))["knowledge_id"] == value["knowledge_id"]
            assert "协调请求并保存数据" in archive.read("overview.md").decode()
    backup(store.root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    assert knowledge.load(Store(tmp_path / "restored"), version)["knowledge_id"] == value["knowledge_id"]


async def test_review_failure_preserves_published_version_and_reuses_stages(tmp_path, monkeypatch):
    store, version = await prepared(tmp_path)
    await knowledge.build(store, version, model=fake_model())
    old = knowledge.load(store, version)
    monkeypatch.setattr(knowledge, "GENERATOR", "new-generator")
    result = await knowledge.build(store, version, model=fake_model(False))
    assert result["partial"]  # Failed overview must preserve usable, reviewed topics.
    assert knowledge.load(store, version) == old
    assert result["preserved_previous"]
    from wiki_app.engine import schedule_followup
    assert "next_job_id" not in schedule_followup(store, "knowledge", result)
    assert list(knowledge.directory(store, version).glob("stages/*/review-required.json"))


def test_reject_false_evidence_and_overloaded_graph():
    with pytest.raises(ValueError, match="invalid_evidence"):
        knowledge.check_refs({"evidence": ["invented"]}, {})
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        knowledge.Model.model_validate({"nodes": [{}] * 10})


async def test_cross_directory_entity_question_uses_archived_evidence(tmp_path):
    from wiki_app.engine import Engine

    store, version = await prepared(tmp_path)
    await knowledge.build(store, version, model=fake_model())
    value = knowledge.load(store, version)

    async def answer(settings, topic, sources):
        assert "阅读范围" in topic
        assert sources
        return "基于功能证据回答", sources

    result = await Engine(store, model=answer).ask(
        {
            "version": version,
            "question": "解释职责",
            "conversation": "knowledge-test",
            "knowledge_id": value["knowledge_id"],
            "entity_id": "request",
        },
        "none",
    )
    record = json.loads(
        store.one("SELECT payload FROM interactions WHERE id=?", (result["interaction_id"],))["payload"]
    )
    assert record["architecture_context"]["knowledge_id"] == value["knowledge_id"]


@pytest.mark.parametrize("reason", ["按功能合并", "missing", None, "", {"text": "not a string"}])
async def test_abstraction_preserves_evidence_and_projects_relations(tmp_path, reason):
    store, version = await prepared(tmp_path)
    base = fake_model()

    async def model(settings, task, payload):
        if "内部功能的归并分组" in task:
            return {
                "groups": [
                    {
                        "id": "request",
                        "title": "请求协调",
                        "responsibility": "协调请求",
                        "members": ["n0", "n1", "n2", "n3"],
                    },
                    {
                        "id": "storage",
                        "title": "数据保存",
                        "responsibility": "保存数据",
                        "members": ["n4", "n5", "n6", "n7"],
                    },
                ],
                **({"reason": reason} if reason != "missing" else {}),
            }
        result = await base(settings, task, payload)
        if "nodes" in result:
            original = result["nodes"]
            result["nodes"] = [dict(original[i // 4], id="n" + str(i)) for i in range(8)]
            result["relations"][0].update(source="n0", target="n4")
        return result

    await knowledge.build(store, version, model=model)
    value = knowledge.load(store, version)
    assert len(value["model"]["nodes"]) == 2
    assert all(isinstance(item, str) for item in value["model"]["omissions"])
    if reason == "按功能合并":
        assert reason in value["model"]["omissions"]
    edge = value["model"]["relations"][0]
    assert (edge["source"], edge["target"]) == ("request", "storage")
    assert all(k in value["evidence"] for k in edge["evidence"])


async def test_reference_repair_selects_actual_source_before_publication(tmp_path):
    store, version = await prepared(tmp_path)
    base = fake_model()

    async def model(settings, task, payload):
        if "候选片段的整数序号" in task:
            assert payload["candidates"]
            return {"indexes": [0]}
        result = await base(settings, task, payload)
        if "nodes" in result:
            result["nodes"][0]["evidence"].append("invented")
        return result

    await knowledge.build(store, version, model=model)
    value = knowledge.load(store, version)
    assert "invented" not in value["model"]["nodes"][0]["evidence"]


async def test_focused_flow_rejection_preserves_other_verified_claims(tmp_path):
    store, version = await prepared(tmp_path)
    base = fake_model()
    focused_reviews = 0

    async def model(settings, task, payload):
        nonlocal focused_reviews
        result = await base(settings, task, payload)
        if "本轮只核对steps及相邻transition" in task:
            focused_reviews += 1
            assert payload["subject_domain"]["claims"] == []
            result["flow_consistent"] = False
            result["flow_reason"] = "Statement reverses two operations in the same step"
            for check in result["checks"]:
                if check["kind"] == "step":
                    check.update(supported=False, reason=result["flow_reason"])
        return result

    result = await knowledge.build(store, version, model=model)
    value = knowledge.load(store, version)
    assert focused_reviews >= 4  # Initial review and bounded repair of two topics.
    assert result["partial"]
    assert len(value["domains"]) == 2
    assert all(d["claims"] and not d["steps"] for d in value["domains"])
    assert any("reverses" in gap for d in value["domains"] for gap in d["gaps"])


async def test_resume_reuses_completed_rejected_audit_and_revision(tmp_path):
    store, version = await prepared(tmp_path)
    base = fake_model()
    audits = 0
    fail_synthesis = True

    async def model(settings, task, payload):
        nonlocal audits
        if "综合功能研究" in task and fail_synthesis:
            raise RuntimeError("synthetic interruption after topics")
        result = await base(settings, task, payload)
        if "subject_domain" in payload:
            audits += 1
            if audits == 1:
                result["checks"][0].update(supported=False, reason="synthetic unsupported claim")
        return result

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        await knowledge.build(store, version, model=model)
    assert list(knowledge.directory(store, version).glob("stages/*/*.rejected.json"))
    before = audits
    fail_synthesis = False
    await knowledge.build(store, version, model=model)
    assert knowledge.load(store, version)
    assert audits == before, "Resume repeated a completed audit"
    await knowledge.build(store, version, model=model, refresh=True)
    assert audits > before, "Explicit refresh must reconsider rejected audits"


async def test_partial_retry_preserves_completed_topics_with_prior_rejections(tmp_path):
    store, version = await prepared(tmp_path)
    base = fake_model()
    request_audits = 0

    async def first(settings, task, payload):
        nonlocal request_audits
        result = await base(settings, task, payload)
        if "domains" in payload and "nodes" in result:
            available = {d["id"] for d in payload["domains"]}
            result["nodes"] = [n for n in result["nodes"] if set(n["domains"]) <= available]
            ids = {n["id"] for n in result["nodes"]}
            result["relations"] = [r for r in result["relations"] if r["source"] in ids and r["target"] in ids]
        if "subject_domain" in payload:
            identity = payload["subject_domain"].get("id")
            if identity == "request":
                request_audits += 1
            if identity == "operations" or (identity == "request" and request_audits == 1):
                result["checks"][0].update(supported=False, reason="synthetic rejected claim")
        return result

    initial = await knowledge.build(store, version, model=first)
    assert initial["partial"]
    before = knowledge.load(store, version)
    kept = next(d for d in before["domains"] if d["id"] == "request")
    assert not any(f["topic"]["id"] == "request" for f in before["failures"])
    touched = []

    async def retry(settings, task, payload):
        if "subject_domain" in payload:
            touched.append(payload["subject_domain"].get("id"))
        if "topic" in payload:
            touched.append(payload["topic"]["id"])
        return await base(settings, task, payload)

    await knowledge.build(store, version, model=retry, refresh=True)
    after = knowledge.load(store, version)
    assert next(d for d in after["domains"] if d["id"] == "request") == kept
    assert "request" not in touched
    assert "operations" in touched


async def test_operations_revision_keeps_instructions_out_of_runtime_flow(tmp_path):
    store, version = await prepared(tmp_path)
    base = fake_model()
    audits = 0
    async def model(settings, task, payload):
        nonlocal audits
        result = await base(settings, task, payload)
        if payload.get("subject_domain", {}).get("id") == "operations":
            audits += 1
            assert payload["subject_domain"]["steps"] == []
            if audits == 1:
                result["checks"][0].update(supported=False, reason="synthetic claim needs revision")
        if payload.get("topic", {}).get("id") == "operations" and "根据独立源码审核" in task:
            key = next(k for k, value in payload["sources"].items() if value["path"] == "README.md")
            result["steps"] = [{"text":"文档中的运行命令", "evidence":[key], "basis":"documented"}]
        return result
    result = await knowledge.build(store, version, model=model)
    assert not result["partial"]
    assert audits == 2
    assert knowledge.load(store, version)["operations"]["steps"] == []


async def test_structured_audit_issues_reach_source_revision(tmp_path, monkeypatch):
    store, version = await prepared(tmp_path)
    base = fake_model()
    issue = {"type": "boundary", "detail": "Check src/a.py implementation ownership"}
    revisions = []
    queries = []
    retrieve = knowledge.retrieve

    def capture(evidence, request, graph):
        queries.extend(request["queries"])
        return retrieve(evidence, request, graph)

    monkeypatch.setattr(knowledge, "retrieve", capture)

    async def model(settings, task, payload):
        if "approved" in task:
            return {"approved": False, "issues": [issue]} if not revisions else {"approved": True, "issues": []}
        if "依据补读实现修正" in task:
            revisions.append(payload)
            return payload["model"]
        return await base(settings, task, payload)

    result = await knowledge.build(store, version, model=model)
    value = knowledge.load(store, version)
    assert not result["partial"]
    assert value["audit"]["approved"]
    assert revisions and all(item == issue for item in revisions[0]["issues"])
    assert "src/a.py" in queries

    async def no_more_calls(*args):
        raise AssertionError("Completed knowledge should be reused")

    assert (await knowledge.build(store, version, model=no_more_calls))["cached"]
