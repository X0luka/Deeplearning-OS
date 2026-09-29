"""Source-backed reading projections; independent of the original analysis artifact."""

import json
from collections import Counter
from pathlib import PurePosixPath

from .service import current


def groups(graph, scope=""):
    files = [
        n
        for n in graph["nodes"]
        if n.get("path")
        and not n["test"]
        and n["language"] != "unsupported"
        and n.get("lines", 1) > 0
        and (not scope or n["path"].startswith(scope + "/"))
    ]
    root = scope
    if not root:
        counts = Counter(n["path"].split("/")[0] for n in files if "/" in n["path"])
        if counts:
            dominant, count = counts.most_common(1)[0]
            if dominant in {"src", "aos", "lib", "app"} and count >= len(files) * 0.5:
                root = dominant
    result = {}
    for n in files:
        path = n["path"]
        relative = path[len(root) + 1 :] if root and path.startswith(root + "/") else path
        prefix = root + "/" if root and path.startswith(root + "/") else ""
        folder = prefix + relative.split("/")[0] if "/" in relative else None
        identity = "module:" + folder if folder else n["id"]
        node = result.setdefault(
            identity,
            {
                "id": identity,
                "path": folder or path,
                "name": PurePosixPath(folder or path).name,
                "kind": "module" if folder else "file",
                "files": [],
                "declarations": [],
            },
        )
        node["files"].append(path)
        node["declarations"].extend({"path": path, **d} for d in n.get("declarations", [])[:6])
    return sorted(result.values(), key=lambda n: (-len(n["files"]), n["path"]))


def semantic_path(store, version):
    return store.root / "wiki" / version / "architecture" / "reading.json"


def semantics(store, version):
    p = semantic_path(store, version)
    return json.loads(p.read_text()) if p.exists() else None


def reading(store, version, scope="", query="", focus="", direction="both", paths=None):
    graph = current(store, version)
    if not graph:
        return {"status": "missing", "nodes": [], "edges": []}
    from .knowledge import load as load_knowledge

    knowledge_ready = load_knowledge(store, version) is not None
    model = None if knowledge_ready else semantics(store, version)
    if model and model["analysis_id"] != graph["analysis_id"]:
        model = None
    descriptions = {n["id"]: n for n in model["modules"]} if model else {}
    scoped_graph = (
        {**graph, "nodes": [n for n in graph["nodes"] if n.get("path") in paths]}
        if paths is not None
        else graph
    )
    nodes = groups(scoped_graph, scope)
    mapping = {path: n["id"] for n in nodes for path in n["files"]}
    by_id = {n["id"]: n for n in nodes}
    # Cross-scope imports retain a boundary node instead of disappearing.
    edges = {}
    internal = Counter()
    excluded = {n["id"] for n in graph["nodes"] if n.get("test") or n.get("language") == "unsupported"}
    for edge in graph["edges"]:
        if edge["source"] in excluded or edge["target"] in excluded:
            continue
        source = mapping.get(edge["source"].removeprefix("file:"))
        target = mapping.get(edge["target"].removeprefix("file:"))
        if not source and not target:
            continue
        if (scope or paths is not None) and (not source or not target):
            missing = edge["source"] if not source else edge["target"]
            boundary_id = "boundary:" + missing
            if boundary_id not in by_id:
                by_id[boundary_id] = {
                    "id": boundary_id,
                    "path": missing.removeprefix("file:"),
                    "name": PurePosixPath(missing.removeprefix("file:")).name,
                    "kind": "boundary",
                    "files": [],
                    "declarations": [],
                    "external": not missing.startswith("file:"),
                }
            source, target = source or boundary_id, target or boundary_id
        if not source or not target:
            continue
        if source == target:
            internal[source] += 1
            continue
        key = (source, target)
        value = edges.setdefault(
            key,
            {
                "id": f"relation:{source}>{target}",
                "source": source,
                "target": target,
                "count": 0,
                "types": {},
                "evidence": [],
            },
        )
        value["count"] += 1
        value["types"][edge["kind"]] = value["types"].get(edge["kind"], 0) + 1
        value["evidence"].extend(edge["evidence"])
    all_edges = list(edges.values())
    for node in by_id.values():
        desc = descriptions.get(node["id"], {})
        node.update(
            title=desc.get("title", node["name"]),
            responsibility=desc.get(
                "responsibility",
                "静态文件分组；展开可查看声明与源码。"
                if node["kind"] != "boundary"
                else "当前范围之外的关联对象",
            ),
            role=desc.get("role", "other"),
            evidence=desc.get("evidence", []),
            semantic_state="inferred" if desc else "unknown",
            internal_relations=internal[node["id"]],
        )
        node["incoming"] = sum(e["target"] == node["id"] for e in all_edges)
        node["outgoing"] = sum(e["source"] == node["id"] for e in all_edges)
    selected = (
        {
            n["id"]
            for n in by_id.values()
            if query.lower() in (n["path"] + " " + n["title"] + " " + n["responsibility"]).lower()
        }
        if query
        else set()
    )
    if focus:
        if focus not in by_id:
            raise ValueError("reading_node_not_found")
        selected = {focus}
    retained = set(by_id)
    if query or focus:
        retained = set(selected)
        for e in all_edges:
            if e["source"] in selected and direction in {"both", "outgoing"}:
                retained.add(e["target"])
            if e["target"] in selected and direction in {"both", "incoming"}:
                retained.add(e["source"])
    visible = [n for n in by_id.values() if n["id"] in retained]
    for n in visible:
        n["matched"] = n["id"] in selected
    filtered_edges = [e for e in all_edges if e["source"] in retained and e["target"] in retained]
    return {
        "status": "ready",
        "analysis_id": graph["analysis_id"],
        "version_id": version,
        "commit": graph["commit"],
        "scope": scope,
        "nodes": visible,
        "edges": filtered_edges,
        "summary": model.get("summary", "") if model else "",
        "semantic_status": "independent" if knowledge_ready else ("ready" if model else "missing"),
        "coverage": graph["coverage"],
        "total_nodes": len(by_id),
        "matched_count": len(selected),
        "hidden_nodes": len(by_id) - len(visible),
        "limitations": [
            "箭头表示源码静态依赖，不表示调用先后或网络通信。",
            "模块按代码边界聚合；职责与分层是有引用的模型推断，并非已验证部署架构。",
        ],
    }
