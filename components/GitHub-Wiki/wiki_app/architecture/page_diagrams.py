"""Evidence-backed diagrams embedded below their matching Wiki sections."""

import hashlib
import json
import re

from ..model_config import config
from .knowledge import directory, load, request, sources_for, write_json


def sections(markdown):
    parts = re.split(r"(?m)(?=^## )", markdown)
    return [
        {"id": str(i), "title": part.splitlines()[0].lstrip("# "), "markdown": part}
        for i, part in enumerate(parts)
        if "\n" in part.strip()
    ]


def fingerprint(knowledge):
    return hashlib.sha256(json.dumps(knowledge["pages"], sort_keys=True).encode()).hexdigest()


def validate(diagrams, page, evidence):
    expected = {s["id"] for s in sections(page["markdown"])}
    if not isinstance(diagrams, list) or {d["section"] for d in diagrams} != expected:
        raise ValueError("diagram_sections_mismatch")
    if len(diagrams) != len(expected):
        raise ValueError("diagram_duplicate_section")
    for d in diagrams:
        if not isinstance(d.get("title"), str) or not 1 <= len(d["title"]) <= 100:
            raise ValueError("diagram_invalid_title")
        if not isinstance(d.get("caption"), str) or len(d["caption"]) > 800:
            raise ValueError("diagram_invalid_caption")
        if not 2 <= len(d["nodes"]) <= 16 or len(d["edges"]) > 24:
            raise ValueError("diagram_size")
        ids = [n["id"] for n in d["nodes"]]
        if len(ids) != len(set(ids)) or any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", x) for x in ids):
            raise ValueError("diagram_node_id")
        for item in d["nodes"] + d["edges"]:
            if not isinstance(item.get("label"), str) or not 1 <= len(item["label"]) <= 100:
                raise ValueError("diagram_label")
            if item.get("basis") not in {"source", "documented", "inferred", "reading"}:
                raise ValueError("diagram_basis")
            refs = item.get("evidence", [])
            if not refs or any(k not in evidence for k in refs):
                raise ValueError("diagram_evidence")
        for e in d["edges"]:
            if e["source"] not in ids or e["target"] not in ids:
                raise ValueError("diagram_endpoint")
    return diagrams


def decorate(store, version, knowledge):
    """Malformed, stale, or unreviewed artifacts never break source reading."""
    from .auto_diagrams import LABELS, graph_check, published

    artifact = published(store, version, knowledge)
    if not artifact:
        return knowledge["pages"]
    pages = []
    try:
        for page in knowledge["pages"]:
            graphs = artifact["pages"][page["slug"]]
            by_section = {d["section"]: d for d in graphs}
            outcomes = {o["section"]: o for o in artifact.get("outcomes", {}).get(page["slug"], [])}
            blocks = []
            for section in sections(page["markdown"]):
                d = by_section.get(section["id"])
                outcome = outcomes.get(section["id"], {})
                if d:
                    graph_check(d, section, artifact["evidence"])
                    refs = list(dict.fromkeys(k for x in d["nodes"] + d["edges"] for k in x["evidence"]))
                    proofs = [{"id": k, **artifact["evidence"][k]} for k in refs]
                    graph = {**d, "proofs": proofs, "version": version}
                else:
                    graph, proofs = None, []
                blocks.append({**section, "graph": graph, "proofs": proofs,
                               "diagram_status": LABELS.get(outcome.get("status"), "源码审核记录已保留"),
                               "diagram_reason": outcome.get("reason", "")})
            pages.append({**page, "visual_sections": blocks})
    except (KeyError, TypeError, ValueError):
        return knowledge["pages"]
    return pages


async def generate(store, version, feedback=None, settings=None):
    """Generate a draft; explicit engineering review precedes publication."""
    knowledge = load(store, version)
    if not knowledge:
        raise ValueError("knowledge_required")
    settings = settings or config(store, task="knowledge", execution=True)
    evidence = sources_for(store, version)
    target = directory(store, version) / "page-diagrams"
    target.mkdir(parents=True, exist_ok=True)
    result = {
        "version": version,
        "commit": knowledge["commit"],
        "knowledge_id": knowledge["knowledge_id"],
        "pages_hash": fingerprint(knowledge),
        "model": settings["model"],
        "pages": {},
        "evidence": {},
        "review": {"status": "draft"},
    }
    for original_page in knowledge["pages"]:
        result["pages"][original_page["slug"]] = []
        for section in sections(original_page["markdown"]):
            page = {**original_page, "markdown": section["markdown"]}
            section_id = section["id"]
            cache = target / (page["slug"] + "-section-" + section_id + "-draft.json")
            if cache.exists() and not feedback:
                cached = json.loads(cache.read_text())
                if cached.get("pages_hash") == result["pages_hash"]:
                    result["pages"][page["slug"]].extend(cached["diagrams"])
                    result["evidence"].update(cached["evidence"])
                    continue
            # Use the actual prose citations, including ranges outside the old model's shortlist.
            from urllib.parse import unquote

            citations = [
                (unquote(p), int(start), int(end))
                for p, start, end in re.findall(
                    r"[?&]path=([^&\s)]+)&start=(\d+)&end=(\d+)", page["markdown"]
                )
            ]
            paths = list(dict.fromkeys(p for p, _, _ in citations))
            domain = next((d for d in knowledge["domains"] if d["title"] == section["title"]), None)
            if domain:
                for claim in domain["claims"] + domain["steps"]:
                    for key in claim["evidence"]:
                        proof = knowledge["evidence"][key]
                        citations.append((proof["path"], proof["start"], proof["end"]))
                        if proof["path"] not in paths:
                            paths.append(proof["path"])
            if not paths:
                for d in knowledge["domains"]:
                    for claim in d["claims"][:2]:
                        for key in claim["evidence"]:
                            proof = knowledge["evidence"][key]
                            citations.append((proof["path"], proof["start"], proof["end"]))
                            if proof["path"] not in paths:
                                paths.append(proof["path"])
            candidates = [(k, e) for k, e in evidence.items() if e["path"] in paths]
            selected, size = {}, 0
            # Fair per-file sampling, favor cited chunks, then implementation continuations.
            candidates.sort(
                key=lambda pair: (
                    not any(
                        pair[1]["path"] == p and pair[1]["start"] <= end and pair[1]["end"] >= start
                        for p, start, end in citations
                    ),
                    pair[1]["start"],
                    paths.index(pair[1]["path"]),
                )
            )
            for key, proof in candidates:
                length = len(json.dumps(proof, ensure_ascii=False))
                if size + length <= 62000:
                    selected[key] = proof
                    size += length
            task = """为已有Wiki正文的每个section生成一张具体、有源码依据的中文图，图将放在该段文字下方。
只返回 {diagrams:[{section:原section id,title,caption,nodes:[{id,label,basis,evidence:[E编号]}],edges:[{source,target,label,basis,evidence:[E编号]}]}]}。
每节2至16节点、最多24边。节点label不超过50字，边label不超过30字。节点使用具体组件、函数、API、数据名称，避免空泛的“核心模块”。
basis只可source/documented/inferred/reading。源码支持source、文档声明documented、推断inferred、建议阅读顺序reading。所有节点和边必须有提供的证据编号。
根据段落用途画结构、数据流、启动步骤或阅读路径；禁止把建议阅读顺序画成运行时调用；未知事项画为待验证边界，不能捏造实现。
架构与模块画主题内部协作，关键流程画具体输入、调用、返回和条件分支。箭头必须有具体动词。不要仅把正文小标题连起来。
如果正文存在源码不支持的说法，caption指出差异，按证据画。不同阶段的请求不要串成一次调用。用户操作、API调用、事件回传方向必须准确。
仓库外服务标注“仓库外”，不能假装读过其实现。不要生成HTML、代码或布局坐标。
"""
            task += "\n本次只有一个section，diagrams必须恰好一张图。禁止依据package.json中存在依赖就认定具体功能使用它。证据不足时省略关系，不用inferred掩盖猜测。优先3至9个节点。"
            payload = {
                "page": page["title"],
                "sections": sections(page["markdown"]),
                "sources": selected,
                "review_feedback": feedback or "",
            }
            print("GENERATE", page["slug"], section_id, len(selected), flush=True)
            answer = await request(settings, task, payload)
            write_json(target / (page["slug"] + "-" + section_id + "-raw.json"), answer)
            try:
                diagrams = validate(answer["diagrams"], page, selected)
            except (ValueError, KeyError, TypeError) as exc:
                answer = await request(
                    settings,
                    task,
                    {
                        **payload,
                        "previous": answer,
                        "validation_error": str(exc),
                        "instruction": "修正格式和证据；只生成一张图。",
                    },
                )
                write_json(target / (page["slug"] + "-" + section_id + "-repair.json"), answer)
                diagrams = validate(answer["diagrams"], page, selected)
            for diagram in diagrams:
                diagram["section"] = section_id
            used = {k for d in diagrams for item in d["nodes"] + d["edges"] for k in item["evidence"]}
            proofs = {k: selected[k] for k in used}
            write_json(cache, {"pages_hash": result["pages_hash"], "diagrams": diagrams, "evidence": proofs})
            result["pages"][page["slug"]].extend(diagrams)
            result["evidence"].update(proofs)
            write_json(target / "draft.json", result)
    write_json(target / "draft.json", result)
    return result
