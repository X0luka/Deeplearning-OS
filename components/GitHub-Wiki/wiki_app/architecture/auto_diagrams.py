"""Version-bound, resumable diagram research, review and publication."""

import json
import re
from urllib.parse import quote, unquote

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ..model_config import config
from ..store import now
from . import service
from .knowledge import digest, directory, load, pack, request, source_navigation, sources_for, write_json
from .page_diagrams import fingerprint, sections, validate

GENERATOR = "page-diagrams-9"
REPAIR_POLICY = "source-repair-3"
STATES = {"ready", "not_suitable", "insufficient_evidence", "review_failed"}
LABELS = {
    "ready": "模型生成并自动核对",
    "not_suitable": "本节无需配图",
    "insufficient_evidence": "配图证据不足",
    "review_failed": "配图审核未通过",
}


class Plan(BaseModel):
    section: str
    decision: str = Field(pattern=r"^(draw|not_suitable|insufficient_evidence)$")
    kind: str | None = Field(default=None, pattern=r"^(responsibility|modules|flow|reading)$")
    question: str = Field(default="", max_length=200)
    reason: str = Field(max_length=2000)
    queries: list[str] = Field(default_factory=list, max_length=16)
    paths: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def require_drawing_question(self):
        if self.decision == "draw" and (self.kind is None or not self.question.strip()):
            raise ValueError("drawing_requires_kind_and_question")
        return self


class AuditCheck(BaseModel):
    object: str
    supported: bool
    reason: str = Field(max_length=500)
    evidence: list[str] = Field(default_factory=list)


class Audit(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list, max_length=100)
    read_more: list[str] = Field(default_factory=list, max_length=16)
    checks: list[AuditCheck] = Field(default_factory=list, max_length=50)

    @field_validator("issues", mode="before")
    @classmethod
    def structured_findings(cls, value):
        if isinstance(value, list):
            return [
                (item.get("issue") or item.get("description") or json.dumps(item, ensure_ascii=False))
                if isinstance(item, dict)
                else item
                for item in value
            ]
        return value


def audit_objects(graph):
    return (
        ["caption"]
        + ["node:" + n["id"] for n in graph["nodes"]]
        + ["edge:" + str(i) for i in range(len(graph["edges"]))]
    )



def edge_propositions(graph):
    """Make both endpoint roles explicit so reviewers cannot validate only a verb."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    return [
        {
            "object": "edge:" + str(i),
            "subject": nodes[e["source"]],
            "predicate": e["label"],
            "target": nodes[e["target"]],
            "explanation": e.get("explanation", ""),
            "evidence": e["evidence"],
        }
        for i, e in enumerate(graph["edges"])
    ]


EDGE_REVIEW = (
    "逐条核对edge_propositions的完整主语—动作—目标，checks.reason须说清动作实际作用于哪个对象。"
    "源码证明动作发生还不够：发送响应的接收者是请求客户端或连接，不是监听循环；"
    "关闭单次连接不代表返回或重启监听服务。异步任务结束不代表调用创建者，"
    "一个请求结束与下一次请求开始之间不得凭时间先后画回边。"
    "不存在的回边应删除，不为凑闭环添加端点。"
)

def audited(answer, graph, sources):
    audit = Audit.model_validate(answer)
    # Approval is only meaningful if the reviewer actually checked every claim.
    if audit.approved:
        expected = set(audit_objects(graph))
        actual = [c.object for c in audit.checks]
        if len(actual) != len(expected) or set(actual) != expected:
            audit.issues.append("审核覆盖不完整：必须逐项核对caption、节点和连线。")
        for check in audit.checks:
            if not check.supported:
                audit.issues.append(check.object + "：" + check.reason)
            if not check.evidence or any(k not in sources for k in check.evidence):
                audit.issues.append(check.object + "：核对引用无效或为空。")
        audit.approved = not audit.issues and not audit.read_more
    return audit


def folder(store, version):
    return directory(store, version) / "page-diagrams"


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def matches(artifact, knowledge, version):
    return bool(
        artifact
        and knowledge
        and artifact.get("knowledge_id") == knowledge["knowledge_id"]
        and artifact.get("pages_hash") == fingerprint(knowledge)
        and artifact.get("version", version) == version
        and artifact.get("commit", knowledge.get("commit")) == knowledge.get("commit")
    )


def published(store, version, knowledge=None):
    knowledge = knowledge or load(store, version)
    value = read(folder(store, version) / "current.json")
    if not matches(value, knowledge, version) or value.get("review", {}).get("status") != "approved":
        return None
    return value


def enqueue(store, version):
    knowledge = load(store, version)
    if not knowledge:
        raise ValueError("knowledge_required")
    return store.enqueue(
        "page_diagrams",
        {"version": version, "knowledge_id": knowledge["knowledge_id"], "pages_hash": fingerprint(knowledge)},
    )


def status(store, version):
    knowledge = load(store, version)
    artifact = published(store, version, knowledge)
    jobs = store.rows(
        "SELECT id,args,state,error FROM jobs WHERE kind='page_diagrams' ORDER BY created DESC,rowid DESC"
    )
    job = next(
        (
            j
            for j in jobs
            if json.loads(j["args"]).get("version") == version
            and knowledge
            and json.loads(j["args"]).get("knowledge_id") == knowledge["knowledge_id"]
            and json.loads(j["args"]).get("pages_hash") == fingerprint(knowledge)
        ),
        None,
    )
    progress = read(folder(store, version) / "progress.json")
    if not matches(progress, knowledge, version):
        progress = None
    state = job["state"] if job else "ready" if artifact else "missing" if knowledge else "knowledge_required"
    if state == "queued":
        progress = None
    if state in {"succeeded", "ready"}:
        state = "partial" if artifact and artifact.get("counts", {}).get("review_failed") else "ready"
    result = {
        "status": state,
        "job_id": job["id"] if job else None,
        "error": job["error"] if job else None,
        "published_id": artifact.get("artifact_id", "legacy") if artifact else None,
        "counts": artifact.get("counts", {}) if artifact else {},
        "progress": {k: progress[k] for k in ("done", "total", "stage", "title") if k in progress}
        if progress
        else None,
    }
    if not knowledge:
        upstream = store.rows(
            "SELECT id,kind,state,error,args FROM jobs WHERE kind IN ('architecture','knowledge') ORDER BY created DESC,rowid DESC"
        )
        result["upstream"] = next(
            (
                {k: j[k] for k in ("id", "kind", "state", "error")}
                for j in upstream
                if json.loads(j["args"]).get("version") == version
            ),
            None,
        )
    if state in {"knowledge_required", "missing"} and not result.get("upstream"):
        settings = config(store, task="knowledge")
        if not settings["enabled"]:
            result.update(status="disabled", error=settings.get("error"))
    return result


def bounded(items, budget=12000):
    result, size = [], 0
    for item in items:
        length = len(json.dumps(item, ensure_ascii=False))
        if size + length > budget:
            continue
        result.append(item)
        size += length
    return result


def select_sources(all_sources, section, knowledge, plan=None):
    citations = [
        (unquote(p), int(a), int(b))
        for p, a, b in re.findall(r"[?&]path=([^&\s)]+)&start=(\d+)&end=(\d+)", section["markdown"])
    ]
    domain = next((d for d in knowledge.get("domains", []) if d["title"] == section["title"]), None)
    for d in [domain] if domain else knowledge.get("domains", []):
        for claim in d.get("claims", []) + d.get("steps", []):
            for key in claim.get("evidence", []):
                proof = knowledge["evidence"][key]
                citations.append((proof["path"], proof["start"], proof["end"]))
    paths = list(dict.fromkeys(p for p, _, _ in citations))
    words = re.findall(r"[a-zA-Z_][a-zA-Z_0-9-]{2,}", " ".join(plan.queries) if plan else section["title"])
    requested = plan.paths if plan else []

    def score(pair):
        _, proof = pair
        cited = any(
            proof["path"] == p and proof["start"] <= end and proof["end"] >= start
            for p, start, end in citations
        )
        body = (proof["path"] + " " + proof["text"]).lower()
        return (
            40 * cited
            + 15 * (proof["path"] in requested)
            + 5 * (proof["path"] in paths)
            + sum(min(body.count(w.lower()), 3) for w in words)
        )

    ranked = sorted(all_sources.items(), key=score, reverse=True)
    return pack(all_sources, [key for key, proof in ranked if score((key, proof)) > 0], 45000)


def graph_check(graph, section, evidence):
    graph = dict(graph)
    graph["section"] = section["id"]
    # Existing validation re-splits isolated Markdown with an index starting at zero.
    check = {**graph, "section": "0"}
    validate([check], {"markdown": "# Topic\nBody"}, evidence)
    if graph.get("direction", "DOWN") not in {"DOWN", "RIGHT"}:
        raise ValueError("diagram_direction")
    for item in graph["nodes"] + graph["edges"]:
        if len(item.get("explanation", "")) > 600:
            raise ValueError("diagram_explanation")
        if len(item.get("group", "")) > 40:
            raise ValueError("diagram_group")
    return graph


async def build(store, version, knowledge_id=None, pages_hash=None, *, model=request, settings=None):
    knowledge = load(store, version)
    if not knowledge:
        raise ValueError("knowledge_required")
    if (knowledge_id and knowledge_id != knowledge["knowledge_id"]) or (
        pages_hash and pages_hash != fingerprint(knowledge)
    ):
        raise ValueError("diagram_knowledge_changed")
    settings = settings or config(store, task="knowledge", execution=True)
    if not settings["enabled"]:
        raise ValueError("model_not_configured")
    generation = digest(
        [
            GENERATOR,
            knowledge["knowledge_id"],
            fingerprint(knowledge),
            {
                k: settings.get(k)
                for k in ("base_url", "model", "version", "temperature", "thinking", "max_output_tokens")
            },
        ]
    )
    old = published(store, version, knowledge)
    if old and (
        old.get("schema_version", 1) == 1
        or old.get("generation") == generation
        and not old.get("counts", {}).get("review_failed")
    ):
        return {"version_id": version, "page_diagrams": True, "cached": True}
    target = folder(store, version)
    work = target / "runs" / generation
    original_evidence = sources_for(store, version)
    aliases = {key: "E" + str(i + 1) for i, key in enumerate(original_evidence)}
    evidence = {aliases[key]: proof for key, proof in original_evidence.items()}

    def context_refs(value):
        if isinstance(value, dict):
            return {
                key: [aliases.get(x, x) for x in item] if key == "evidence" else context_refs(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [context_refs(x) for x in value]
        return value

    graph = service.current(store, version) or {}
    source_paths = sorted({e["path"] for e in evidence.values()})
    result = {
        "schema_version": 2,
        "generator": GENERATOR,
        "repair_policy": REPAIR_POLICY,
        "generation": generation,
        "version": version,
        "commit": knowledge["commit"],
        "knowledge_id": knowledge["knowledge_id"],
        "pages_hash": fingerprint(knowledge),
        "model": settings["model"],
        "created": now(),
        "pages": {},
        "outcomes": {},
        "evidence": {},
        "review": {"status": "approved", "method": "model_source_audit", "model": settings["model"]},
    }
    total = sum(len(sections(p["markdown"])) for p in knowledge["pages"])
    done = 0

    def progress(stage, title):
        write_json(
            target / "progress.json",
            {
                **{k: result[k] for k in ("version", "commit", "knowledge_id", "pages_hash")},
                "done": done,
                "total": total,
                "stage": stage,
                "title": title,
            },
        )

    async def call(name, task, payload):
        path = work / "calls" / (name + "-" + digest([task, payload]) + ".json")
        cached = read(path)
        if cached is not None:
            return cached
        print("DIAGRAM", name, flush=True)
        try:
            answer = await model(settings, task, payload)
        except json.JSONDecodeError as exc:
            # A completed malformed JSON response permits one format correction;
            # transient transport recovery is bounded inside the shared request adapter.
            write_json(path.with_suffix(".invalid.json"), {"output": exc.doc})
            answer = await model(
                settings,
                task + "\n上次输出不是合法JSON。仅返回简短、完整的JSON，禁止解释或复制源码。",
                {**payload, "invalid_json_excerpt": exc.doc[:8000]},
            )
        write_json(path, answer)
        return answer

    for page in knowledge["pages"]:
        slug = page["slug"]
        result["pages"][slug], result["outcomes"][slug] = [], []
        for section in sections(page["markdown"]):
            key = slug + "-" + section["id"]
            outcome_file = work / (key + ".json")
            cached = read(outcome_file)
            if cached and cached["status"] != "review_failed":
                outcome = cached
            else:
                attempt = (cached or {}).get("attempt", 0) + (
                    0 if cached and cached.get("reason", "").startswith("模型输出格式") else 1
                )
                prefix = key + "-" + str(attempt)
                selected = select_sources(evidence, section, knowledge)
                payload = {
                    "page": page["title"],
                    "required_kind": {"flows": "flow", "reading": "reading"}.get(slug),
                    "section": {**section, "markdown": section["markdown"][:12000]},
                    "sources": selected,
                    "project": knowledge.get("model", {}).get("title", ""),
                    "project_roles": [
                        {k: n[k] for k in ("id", "title", "responsibility", "kind")}
                        for n in knowledge.get("model", {}).get("nodes", [])
                    ] if slug == "overview" else [],
                    "source_navigation": source_navigation(
                        evidence, {v["path"] for v in selected.values()}, [], limit=7000),
                    "candidate_paths": bounded(
                        sorted(
                            source_paths,
                            key=lambda p: (p not in {e["path"] for e in selected.values()}, len(p)),
                        )
                    ),
                }
                # Manual retries learn from the saved rejection; approved sections
                # remain reusable because their source/knowledge binding is unchanged.
                prior_rejection = (cached or {}).get("reason", "")
                if prior_rejection:
                    payload["previous_rejection"] = prior_rejection
                    payload["retry_policy"] = REPAIR_POLICY
                progress("规划配图", section["title"])
                try:
                    planning = await call(
                        prefix + "-plan",
                        """为当前Wiki小节规划是否需要一张图。不要为凑数而画图。queries最多16项，paths最多12项，question最多200字，reason最多500字。
输出 {section,decision:draw|not_suitable|insufficient_evidence,kind:responsibility|modules|flow|reading,question,reason,queries:[检索实现用的英文符号],paths:[需补读的实际文件路径]}。
required_kind非空时，draw的kind必须与之相同。关键流程页必须回答一次具体输入如何经过处理到达输出，不可用类包含关系或模块组成代替流程；若原小节只提供组成信息，选择该模块源码支持的一次前向/请求/启动流程；没有流程证据则不画。阅读路线只表示建议阅读顺序。问题必须具体且单一。流程只能选择一种实际场景，不同时解释两个启动命令或两条独立调用路径；范围写入question。概览解释系统边界与主要协作；概览第一页的首节必须覆盖project_roles的核心协作，不能选择局部工具、辅助函数或某个可选功能代替项目总览。模块解释职责；流程选择一条具体业务路径；阅读路线只表示阅读顺序。
文字已经简单明确或只是问题清单时选择not_suitable。缺少源码不等于实现不存在。不要固定要求前后端；按项目实际类型组织。每图仅解释一个问题，概览只规划3至5项主要职责，禁止把启动、配置、鉴权、同步、恢复全部塞入同图。""",
                        payload,
                    )
                    # Retrieval hints may be truncated without changing any factual claim.
                    if isinstance(planning, dict):
                        planning["section"] = section["id"]
                        for field, limit in (("queries", 16), ("paths", 12)):
                            if isinstance(planning.get(field), list):
                                planning[field] = planning[field][:limit]
                    plan = Plan.model_validate(planning)
                    required_kind = {"flows": "flow", "reading": "reading"}.get(slug)
                    if plan.decision == "draw" and required_kind and plan.kind != required_kind:
                        raise ValueError("diagram_kind_mismatch")
                    # Paths here are retrieval hints, not published evidence.
                    # Ignore unavailable hints while preserving other grounded retrieval.
                    unavailable = [p for p in plan.paths if p not in source_paths]
                    plan.paths = [p for p in plan.paths if p in source_paths]
                    selected = select_sources(evidence, section, knowledge, plan)
                    outcome = {
                        "section": section["id"],
                        "status": plan.decision,
                        "reason": plan.reason,
                        "plan": plan.model_dump(),
                        "attempt": attempt,
                    }
                    if unavailable:
                        from ..rules import failure
                        outcome["warnings"] = [failure("diagram_read_hint_unavailable",
                            stage="diagram-plan", resource=p) for p in unavailable]
                        outcome["reason"] += " 部分补读路径不在快照中，已使用其余有效证据继续检索。"
                    if plan.decision == "draw" and not selected:
                        outcome.update(status="insufficient_evidence", reason="未找到支持该主题的快照源码。")
                    elif plan.decision == "draw":
                        progress("生成与源码审核", section["title"])
                        payload = {
                            "page": page["title"],
                            "section": {**section, "markdown": section["markdown"][:12000]},
                            "plan": plan.model_dump(),
                            "knowledge_context": context_refs(
                                {
                                    "project_summary": knowledge.get("model", {}).get("summary", {}),
                                    "project_roles": knowledge.get("model", {}).get("nodes", []) if slug == "overview" else [],
                                    "project_relations": knowledge.get("model", {}).get("relations", []) if slug == "overview" else [],
                                    "topic": next(
                                        (
                                            d
                                            for d in knowledge.get("domains", [])
                                            if d["title"] == section["title"]
                                        ),
                                        {},
                                    ),
                                }
                            ),
                            "sources": selected,
                            "static_relations": [
                                e
                                for e in graph.get("edges", [])
                                if any(p in json.dumps(e) for p in plan.paths)
                            ][:30],
                        }
                        if prior_rejection:
                            payload["previous_rejection"] = prior_rejection
                            payload["retry_policy"] = REPAIR_POLICY
                        instruction = """根据给出的固定快照证据生成一张图。以knowledge_context提供的研究线索和本节问题确定范围，再从sources重新建立角色与关系，不要重新堆满源码中的辅助类。仍须对照sources核验，不能盲信旧知识。输出 {graph:{section,title,caption,direction:RIGHT|DOWN,nodes:[{id,label,explanation,group,basis,evidence:[E编号]}],edges:[{source,target,label,explanation,basis,evidence:[E编号]}]}}。
只使用所给证据编号，basis为source/documented/inferred/reading。所有节点和边必须有直接证据。本图严格限制2至6节点、最多9边，label最多100字，explanation最多600字，group最多40字。
节点为具体职责/组件/函数/API，边用明确动词与条件。阅读顺序必须标reading。节点可用group标明前端/后端/仓库外等实际边界，不为单一程序虚构分层。
不能把import/依赖清单当调用证据，不能把彼此独立的操作连成时序。外部服务注明仓库外；客户端库不是服务器。不要把工厂函数画成它返回的运行时实例；生命周期启动实例不等于再次执行工厂。可选分支的写操作须匹配本图场景，先返回错误的场景不能画后面的写入。不同数据准备脚本的可选输出应注明对应实现或省略。同一函数内互斥分支不能合并为同一输入必经的步骤；分别核对每个动作的守卫条件，提前return或排除该输入类型的条件仍然有效。生成说明只写当前输入类型实际经过的操作。自环只用于源码存在的循环或递归，不用自环表示一个节点内的顺序步骤。每个节点必须对应一个边界一致的具体对象，不把“选择外部服务的内部UI”与“外部服务本身”合为同一节点。边的目标必须与label实际指代的对象一致。仓库内脚本即使单独运行也属于仓库内，独立进程不等于仓库外。可选外围能力与本图问题无关时省略。previous_rejection若存在是前次拒绝意见，结合源码解决或缩小范围，不得原样重复其中已证实的错误。
当plan.kind=flow时，节点是一次明确执行场景中的处理步骤或参与组件，箭头表达输入/输出/调用/条件。类包含、属性持有关系不应成为主干；必须能沿箭头解释实际从输入到输出的路径。禁止画“不调用/不依赖”这种不存在的关系，改在说明中交代边界。内存state/store/cache不是数据库，不能写成落库或持久化。概览与源码细节分层，复杂主题收窄到所选问题。不画无信息的标题串联。节点和边的explanation只用一句话说明该场景内的职责或关系（建议不超过40字），不要描述节点在其他场景下的完整能力，禁止拓展到图中未表达的实现细节。不要为了提供长解释添加额外断言。总览优先3至5个节点，不包含无关的内部辅助类。caption只说明本图场景及边界；只有确定矛盾时才指出，不要罗列未读到的疑点或断言某实现缺失。不确定关系省略，不用推断掩盖猜测。""" + EDGE_REVIEW
                        proposal = await call(prefix + "-generate", instruction, payload)
                        issues = []
                        approved = False
                        for revision in range(2):
                            try:
                                candidate = graph_check(proposal["graph"], section, selected)
                                if len(candidate["nodes"]) > 6:
                                    raise ValueError("diagram_focus_too_broad_reduce_to_6_nodes")
                            except (ValueError, KeyError, TypeError) as exc:
                                issues = ["结构校验失败：" + str(exc)]
                            else:
                                # Audit the diagram's actual evidence first. Unrelated
                                # generation context obscures the claim being checked;
                                # adjacent implementation remains available via read_more.
                                refs = [
                                    k
                                    for item in candidate["nodes"] + candidate["edges"]
                                    for k in item["evidence"]
                                ]
                                selected = pack(evidence, refs, 62000)
                                audit_payload = {
                                    "graph": candidate,
                                    "sources": selected,
                                    "question": plan.question,
                                    "diagram_kind": plan.kind,
                                    "required_checks": audit_objects(candidate),
                                    "edge_propositions": edge_propositions(candidate),
                                    "source_index": bounded(
                                        [
                                            {"id": k, "path": e["path"], "start": e["start"], "end": e["end"]}
                                            for k, e in evidence.items()
                                            if k not in selected
                                            and e["path"] in {p["path"] for p in selected.values()}
                                        ],
                                        14000,
                                    ),
                                }
                                audit_task = """你是独立源码审核步骤，不信任生成图或正文的断言。逐条核验节点与箭头的引用能否直接支持关系、方向、触发条件、内外边界，检查是否混淆import与调用、独立请求与时序、文件上传与其他SDK操作、阅读顺序与执行顺序。
输出 {approved:布尔,issues:[具体错误及依据],read_more:[需要补读的source_index证据ID],checks:[{object:required_checks中的ID,supported:布尔,reason:核对结果不超过60字,evidence:[支持该核对的E编号]}]}。批准时checks必须逐项覆盖required_checks，不能只写总评。每条边分别核对源节点是什么、目标节点是什么、动作是否真的发生在这两个对象之间；工厂函数、它返回的对象和对象的方法不是同一对象。标题/caption的条件适用于所有断言，例如某模式下接口先返回错误就不能声称该模式会执行其后面的写入。引用能证明相关代码存在不等于它支持该方向与条件。每个节点解释涉及多个动作时，逐个检查动作所在if/else与提前return，不能因它们出现在同一函数就全部套到同一输入；尤其if not/is not等否定条件会排除一类输入。checks.reason必须简述本图输入实际进入的分支与支持行号，不用“源码支持”代替核对。节点解释中的每项输出文件、读取位置也须核对：不同实现的可选输出不能合并为所有实现必有；一个函数读数据不代表同文件的元数据读取也由该函数完成。不能仅因引用存在就批准；关键实现缺失时不得批准。先检查图是否回答question且符合diagram_kind：flow必须解释明确输入到输出的处理/数据传递，不得仅用包含/持有关系替代；reading不得伪装运行顺序。检查是否把内存状态误称数据库或持久化，是否用负向不存在的关系画箭头。只报告会误导阅读者的事实错误或缺证据的关键关系，依赖协议、实现接口、包含组件是合法架构关系，不要求它们是运行时函数调用。禁止将“成立”“可接受”或补充知识写入issues。只把确实错误的原图断言列入issues；没有实质错误则approved=true且issues=[]。不因图省略与问题无关的细节、所有异常分支、所有方法或所有依赖而拒绝。源码一致的概括不是错误，职责图不要求展开成完整时序图。read_more最多16项，issues优先最多8项，合并同类问题，每项最多120字，只描述确实需要修改的错误；不要复制原图或源码，批准时issues为空。
审核精度要求：每个issue必须引用图中实际存在的原句或边ID，并说明引用源码中的相反事实或关键证据缺口。不得凭图未写出的含义构造错误。标题/caption中的场景限定适用于整张图，节点职责描述不自动表示执行先后。组件可概括其内部函数/回调的行为；无需为每个内部函数拆节点。图已明确写“作为参数传入”“返回应用交给服务器”时，是数据传递，不是调用；不能再次要求改成同义标签。一个对象的多条引用共同支持其描述，不要求每条引用单独证明全部描述。无直接矛盾且引用支持摘要时通过；真实缺少调用/数据流依据、反向关系、遗漏决定行为的条件仍须拒绝。"""
                                audit = audited(
                                    await call(prefix + f"-audit-{revision}", audit_task + EDGE_REVIEW, audit_payload),
                                    candidate,
                                    selected,
                                )
                                if audit.read_more:
                                    allowed = set(evidence)
                                    if any(k not in allowed for k in audit.read_more):
                                        raise ValueError("diagram_invalid_audit_read")
                                    refs = [
                                        k
                                        for item in candidate["nodes"] + candidate["edges"]
                                        for k in item["evidence"]
                                    ]
                                    selected = pack(evidence, refs + audit.read_more, 62000)
                                    audit_payload["sources"] = selected
                                    audit = audited(
                                        await call(
                                            prefix + f"-audit-expanded-{revision}",
                                            audit_task + EDGE_REVIEW + "本次补读已提供，仍不足则拒绝，不再请求补读。",
                                            audit_payload,
                                        ),
                                        candidate,
                                        selected,
                                    )
                                if not audit.approved or audit.issues:
                                    # Distinguish factual defects from a critic's requests for more detail.
                                    audit = audited(
                                        await call(
                                            prefix + f"-verdict-{revision}",
                                            "你是源码审核裁决步骤。逐项复核先前审查意见，并自行核对图中的关键关系。意见本身不是事实。"
                                            "只保留与源码矛盾或关键关系确实缺证据的问题，不把合理摘要、省略辅助细节、不同抽象层级视为错误。"
                                            "关系可能表示调用、数据传递、职责归属或静态依赖，应按图标题和标签判断，不能全部当函数调用。"
                                            "但diagram_kind=flow必须确实回答一次输入到输出的流程，纯模块组成不能通过。内存存储称落库/持久化、用不存在的关系画边也不能通过。"
                                            "输出 {approved:布尔,issues:[确认的实质错误],read_more:[],checks:[{object:required_checks中的ID,supported:布尔,reason:核对结果不超过60字,evidence:[E编号]}]}。批准时checks必须覆盖caption和每个节点/边，逐项核对端点角色、触发条件和动作；工厂、返回对象及其方法不能混为一个节点。场景内先返回错误就不会执行后续写操作，某实现的可选文件不能说成所有实现必有，不同函数读取的数据不能混为一个函数职责。"
                                            "每项描述的动作须检查各自if/else、否定条件和提前return。同函数中的不同分支不代表同一输入会经历全部动作；checks.reason须简述输入成立的条件及行号。只有不存在实质错误才approved=true且issues=[]；关键关系未核实必须拒绝。最多6条，每条150字。"
                                            "\n审核精度要求：每个issue必须引用图中实际存在的原句或边ID，并说明引用源码中的相反事实或关键证据缺口。不得凭图未写出的含义构造错误。标题/caption中的场景限定适用于整张图，节点职责描述不自动表示执行先后。组件可概括其内部函数/回调的行为；无需为每个内部函数拆节点。图已明确写“作为参数传入”“返回应用交给服务器”时，是数据传递，不是调用；不能再次要求改成同义标签。一个对象的多条引用共同支持其描述，不要求每条引用单独证明全部描述。无直接矛盾且引用支持摘要时通过；真实缺少调用/数据流依据、反向关系、遗漏决定行为的条件仍须拒绝。",
                                            {
                                                "graph": candidate,
                                                "diagram_kind": plan.kind,
                                                "required_checks": audit_objects(candidate),
                                                "edge_propositions": edge_propositions(candidate),
                                                "edge_review_rules": EDGE_REVIEW,
                                                "sources": selected,
                                                "previous_findings": audit.model_dump(),
                                                "question": plan.question,
                                            },
                                        ),
                                        candidate,
                                        selected,
                                    )
                                issues = audit.issues
                                approved = audit.approved and not issues and not audit.read_more
                                if approved:
                                    break
                            if revision == 0:
                                proposal = await call(
                                    prefix + "-revision",
                                    "本次任务是修复失败图，不是重新照着旧知识模型生成。review_issues是前一步源码审核结果。"
                                    "必须逐条对照源码修正这些错误，无法确认的关系直接删除。不要保留已被指出职责错误的节点解释或箭头。"
                                    "可减少细节，但必须继续回答原question；不能把总览替换成局部辅助函数或把普通流程替换成无关入口。caption准确描述剩余范围。输出完整修正后的graph。\n"
                                    + instruction,
                                    {
                                        "section": section["id"],
                                        "question": plan.question,
                                        "sources": selected,
                                        "previous": proposal,
                                        "review_issues": issues,
                                        "repair_policy": REPAIR_POLICY,
                                        "revision_rule": "修订图最多4个节点、4条边；每个解释最多一句40字，只描述本场景。优先删减无法补证的节点/边和多余细节，必须保持原问题与核心职责覆盖。若错误涉及合并职责或混合流程，重新收窄到一条已证实主线；可以只剩3个节点。只保留一个问题所需的最小正确图；条件集中说明，区分数据传递与函数调用。不要添加新的主题。",
                                    },
                                )
                        if approved:
                            used = {
                                k
                                for item in candidate["nodes"] + candidate["edges"]
                                for k in item["evidence"]
                            }
                            outcome.update(
                                status="ready",
                                graph=candidate,
                                evidence={k: evidence[k] for k in used},
                                reason="独立模型步骤已对照源码核对，仍可通过引用自行复核。",
                            )
                        else:
                            outcome.update(
                                status="review_failed",
                                reason="；".join(issues)[:1500] or "审核所需证据不足",
                                draft=proposal,
                            )
                except (ValidationError, KeyError, TypeError) as exc:
                    outcome = {
                        "section": section["id"],
                        "status": "review_failed",
                        "reason": "模型输出格式不符合配图协议：" + type(exc).__name__,
                        "attempt": attempt,
                    }
                except ValueError as exc:
                    if not str(exc).startswith("diagram_"):
                        raise
                    outcome = {
                        "section": section["id"],
                        "status": "review_failed",
                        "reason": str(exc),
                        "attempt": attempt,
                    }
                write_json(outcome_file, outcome)
            if outcome["status"] == "ready":
                result["pages"][slug].append(outcome["graph"])
                result["evidence"].update(outcome["evidence"])
            result["outcomes"][slug].append(
                {k: v for k, v in outcome.items() if k not in {"graph", "evidence", "draft"}}
            )
            done += 1
            progress("已核对小节", section["title"])
            write_json(work / "draft.json", result)
    if not matches(result, load(store, version), version):
        raise ValueError("diagram_knowledge_changed")
    result["counts"] = {
        s: sum(o["status"] == s for outcomes in result["outcomes"].values() for o in outcomes) for s in STATES
    }
    result["artifact_id"] = digest(result)
    write_json(target / (result["artifact_id"] + ".json"), result)
    write_json(target / "current.json", result)
    progress("完成", "")
    return {"version_id": version, "page_diagrams": True, "counts": result["counts"],
            "partial": bool(result["counts"]["review_failed"])}


def export_markdown(artifact, repo_url):
    lines = ["# 配图与源码依据", "", "模型生成的关系仍需结合源码判断。", ""]
    for slug, graphs in artifact["pages"].items():
        lines += ["## " + slug, ""]
        for g in graphs:
            lines += ["### " + g["title"], "", g["caption"], ""]
            nodes = {n["id"]: n["label"] for n in g["nodes"]}
            for node in g["nodes"]:
                proof_labels = []
                for key in node["evidence"]:
                    proof = artifact["evidence"][key]
                    url = (
                        repo_url
                        + "/blob/"
                        + artifact["commit"]
                        + "/"
                        + quote(proof["path"], safe="/")
                        + f"#L{proof['start']}-L{proof['end']}"
                    )
                    proof_labels.append(f"[{proof['path']}:{proof['start']}]({url})")
                lines += [
                    f"- 节点：{node['label']}（{node['basis']}）{node.get('explanation', '')} "
                    + " ".join(proof_labels)
                ]
            for e in g["edges"]:
                refs = []
                for key in e["evidence"]:
                    p = artifact["evidence"][key]
                    url = (
                        repo_url
                        + "/blob/"
                        + artifact["commit"]
                        + "/"
                        + quote(p["path"], safe="/")
                        + f"#L{p['start']}-L{p['end']}"
                    )
                    refs.append(f"[{p['path']}:{p['start']}–{p['end']}]({url})")
                lines += [
                    f"- {nodes[e['source']]} → {e['label']} → {nodes[e['target']]}（{e['basis']}）{e.get('explanation', '')} "
                    + " ".join(refs)
                ]
            lines += [""]
        for o in artifact.get("outcomes", {}).get(slug, []):
            if o["status"] != "ready":
                lines += [f"- 小节 {o['section']}：{LABELS[o['status']]}：{o['reason']}"]
    return "\n".join(lines)
