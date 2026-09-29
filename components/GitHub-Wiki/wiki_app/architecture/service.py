"""Analysis lifecycle separate from Wiki publication and model calls."""

import asyncio
import json
import sys

from ..store import now, uid
from .graph import ANALYZER_VERSION, project


def directory(store, version):
    return store.root / "wiki" / version / "architecture"


def current(store, version):
    pointer = directory(store, version) / "current.json"
    if not pointer.exists():
        return None
    return json.loads(pointer.read_text())


def load(store, version, identity=None):
    value = current(store, version)
    if identity:
        if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("invalid_analysis_id")
        path = directory(store, version) / (identity + ".json")
        value = json.loads(path.read_text()) if path.exists() else None
    if not value:
        raise ValueError("architecture_not_ready")
    return value


async def build(store, version):
    old = current(store, version)
    if old and old["analyzer_version"] == ANALYZER_VERSION:
        return {
            "version_id": version,
            "analysis_id": old["analysis_id"],
            "architecture": True,
            "cached": True,
            "partial": bool(old["coverage"].get("issues")),
            "issues": old["coverage"].get("issues", []),
        }
    out = directory(store, version)
    out.mkdir(parents=True, exist_ok=True)
    temporary = out / (uid() + ".tmp")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "wiki_app.architecture.worker",
        str(store.root),
        version,
        str(temporary),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        from ..rules import RuleError
        try:
            async with asyncio.timeout(600):
                _, diagnostics = await process.communicate()
        except TimeoutError:
            raise RuleError("analysis_timeout", stage="analysis", limit=600) from None
        if process.returncode:
            # Only fixed error codes may leave the analysis subprocess.
            code = ("snapshot_hash_mismatch" if b"ValueError: snapshot_hash_mismatch" in diagnostics
                    else "architecture_analysis_failed")
            raise RuleError(code, stage="analysis")
        graph = json.loads(temporary.read_text())
        v = store.version(version)
        if (
            graph["version_id"] != version
            or graph["commit"] != v["sha"]
            or graph["analyzer_version"] != ANALYZER_VERSION
        ):
            raise ValueError("architecture_version_mismatch")
        paths = {f["path"]: f.get("lines") for f in v["manifest"]["files"]}
        ids = {n["id"] for n in graph["nodes"]}
        for edge in graph["edges"]:
            if edge["source"] not in ids or edge["target"] not in ids:
                raise ValueError("invalid_architecture_edge")
            for proof in edge["evidence"]:
                if (
                    proof["path"] not in paths
                    or not 1 <= proof["start"] <= proof["end"]
                    or paths[proof["path"]] is not None
                    and proof["end"] > paths[proof["path"]]
                ):
                    raise ValueError("invalid_architecture_evidence")
        archive = out / (graph["analysis_id"] + ".json")
        if not archive.exists():
            archive.write_bytes(temporary.read_bytes())
        with (store.root / "wiki" / "log.md").open("a") as log:
            log.write(f"\n## {now()} architecture prepare | {version} / {graph['analysis_id']}\n")
        temporary.replace(out / "current.json")
        return {"version_id": version, "analysis_id": graph["analysis_id"], "architecture": True,
                "partial": bool(graph["coverage"].get("issues")), "issues": graph["coverage"].get("issues", [])}
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        temporary.unlink(missing_ok=True)


def status(store, version, **filters):
    node_id = filters.pop("node_id", None)
    graph = current(store, version)
    jobs = store.rows("SELECT id,args,state,error FROM jobs WHERE kind='architecture' ORDER BY created DESC")
    job = next((j for j in jobs if json.loads(j["args"])["version"] == version), None)
    result = {"status": "ready" if graph else job["state"] if job else "missing", "job": job}
    if graph:
        result.update(project(graph, **filters))
        if node_id:
            paths = selection(graph, node_id)
            ids = {"file:" + path for path in paths}
            nodes = {n["id"]: n for n in graph["nodes"]}
            relations = []
            for edge in graph["edges"]:
                if edge["source"] in ids or edge["target"] in ids:
                    relations.append(
                        {
                            **edge,
                            "source_label": nodes[edge["source"]].get("path", nodes[edge["source"]]["label"]),
                            "target_label": nodes[edge["target"]].get("path", nodes[edge["target"]]["label"]),
                        }
                    )
            result["detail"] = {
                "files": len(paths),
                "relations": relations,
                "declarations": nodes.get(node_id, {}).get("declarations", []),
            }
    return result


def mermaid(view):
    nodes = {n["id"]: f"N{i}" for i, n in enumerate(view["nodes"])}
    lines = ["flowchart LR"]
    for node in view["nodes"]:
        # Encode every label character so repository strings cannot inject syntax.
        label = "".join(f"#{ord(c)};" for c in node["label"])
        lines.append(f'  {nodes[node["id"]]}["{label}"]')
    for edge in view["edges"]:
        lines.append(f"  {nodes[edge['source']]} --> {nodes[edge['target']]}")
    return "\n".join(lines)


def selection(graph, node_id):
    paths = {
        n["path"]
        for n in graph["nodes"]
        if n.get("path")
        and (n["id"] == node_id or node_id.startswith("module:") and n["path"].startswith(node_id[7:] + "/"))
    }
    if not paths:
        raise ValueError("architecture_node_not_found")
    return paths
