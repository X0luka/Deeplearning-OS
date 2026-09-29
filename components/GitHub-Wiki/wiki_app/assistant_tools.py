import json
import os
import re
import secrets

from fastapi import HTTPException, Query, Request


def authorize(request):
    expected = os.getenv("AOS_LLM_TOKEN", "")
    if not expected or not secrets.compare_digest(
        request.headers.get("authorization", ""), "Bearer " + expected
    ):
        raise HTTPException(403, "product_token_required")



def ui_host(request):
    """Authenticated preview rendering; ordinary product URLs keep their current UI."""
    from . import llm_client
    if os.getenv("AOS_UNIFIED_ASSISTANT") == "1":
        return llm_client.host_url()
    from urllib.parse import urlsplit

    host = request.headers.get("x-aos-preview-host", os.getenv("AOS_ASSISTANT_PREVIEW_HOST", ""))
    address = urlsplit(host)
    if address.scheme != "http" or address.hostname not in {"127.0.0.1", "localhost"} or address.username or address.path not in {"", "/"} or address.query or address.fragment:
        return ""
    token = os.getenv("AOS_LLM_TOKEN", "")
    if host and token and secrets.compare_digest(request.headers.get("x-aos-preview-token", ""), token):
        return host
    return ""

def install(app, store):
    from .chat import source_chunks
    from .llm_settings import local_override

    def resource(identity):
        v = store.version(identity)
        if not v:
            raise HTTPException(404, "version_not_found")
        r = store.repo(v["repo"])
        return {
            "id": identity,
            "title": r["url"].rstrip("/").split("/")[-1] + " · " + v["sha"][:8],
            "version": v["sha"],
            "repository_url": r["url"],
            "kind": "repository",
            "ready": True,
            "file_listing": True,
        }

    @app.get("/aos/assistant/v1/resources")
    def resources(request: Request, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        authorize(request)
        rows = store.rows(
            "SELECT id FROM versions ORDER BY created DESC LIMIT ? OFFSET ?", (limit + 1, offset)
        )
        return {
            "items": [resource(r["id"]) for r in rows[:limit]],
            "next": offset + limit if len(rows) > limit else None,
        }

    @app.get("/aos/assistant/v1/tree")
    def tree(request: Request, resource_id: str, version: str = "", prefix: str = "",
             offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        authorize(request)
        r = resource(resource_id)
        if version and r["version"] != version:
            raise HTTPException(409, "source_version_changed")
        if ".." in prefix.split("/") or prefix.startswith("/"):
            raise HTTPException(400, "invalid_path_prefix")
        rows = store.rows("SELECT path,MAX(CAST(stop AS INTEGER)) lines FROM chunks "
                          "WHERE version=? AND substr(path,1,length(?))=? GROUP BY path ORDER BY path LIMIT ? OFFSET ?",
                          (resource_id, prefix, prefix, limit + 1, offset))
        top = store.rows("SELECT DISTINCT path FROM chunks WHERE version=?", (resource_id,))
        directories = sorted({v["path"].split("/")[0] + "/" for v in top if "/" in v["path"]})
        return {"items": [{"resource": resource_id, "version": r["version"],
            "block": "directory:" + prefix + ":" + str(offset), "title": "固定快照源码目录",
            "quality": "source_index", "text": json.dumps({"files": rows[:limit], "top_level_directories": directories,
                "next_offset": offset + limit if len(rows) > limit else None,
                "prefix": prefix, "coverage": "indexed_snapshot_only"}, ensure_ascii=False),
            "location": {}, "url": str(request.base_url).rstrip("/") + "/versions/" + resource_id}]}

    @app.get("/aos/assistant/v1/search")
    def lookup(request: Request, resource_id: str, query: str = "", block_id: str = "", version: str = "", view: str = "{}"):
        authorize(request)
        r = resource(resource_id)
        if version and r["version"] != version:
            raise HTTPException(409, "source_version_changed")
        if block_id.startswith("knowledge:"):
            from .architecture.knowledge import load

            pages = (load(store, resource_id) or {}).get("pages", [])
            page = next((p for p in pages if "knowledge:" + p["slug"] == block_id), None)
            if page is None:
                raise HTTPException(404, "block_not_found")
            return {
                "items": [
                    {
                        "resource": resource_id,
                        "version": r["version"],
                        "block": block_id,
                        "title": "生成知识页（非原始源码）：" + page.get("title", page["slug"]),
                        "text": page.get("markdown", "")[:10000],
                        "quality": "generated_knowledge",
                        "location": {},
                        "url": str(request.base_url).rstrip("/") + "/versions/" + resource_id,
                    }
                ]
            }
        path, start = block_id.rsplit(":", 1) if ":" in block_id else (block_id, "1")
        if not start.isdigit():
            raise HTTPException(400, "invalid_block_id")
        from urllib.parse import urlencode

        items = []
        try:
            current_view = json.loads(view)
        except ValueError:
            raise HTTPException(400, "invalid_view_context") from None
        if not isinstance(current_view, dict):
            raise HTTPException(400, "invalid_view_context")
        if not block_id:
            from .chat import assemble
            args = {k: v for k, v in current_view.items() if k in {
                "page", "selection", "entity_id", "knowledge_id", "node_id", "analysis_id"}}
            args.update(version=resource_id, conversation="unified-context", question=query)
            _, _, sources, prepared = assemble(store, args)
            for source in sources:
                items.append({"resource": resource_id, "version": r["version"],
                    "block": source["source"] + ":" + str(source["start"]),
                    "title": source["source"], "text": source["text_preview"],
                    "location": {"version": resource_id, "path": source["source"],
                                 "start": source["start"], "stop": source["end"]},
                    "url": str(request.base_url).rstrip("/") + "/versions/" + resource_id +
                           "/source?" + urlencode({"path": source["source"], "start": source["start"], "end": source["end"]})})
            items.append({"resource": resource_id, "version": r["version"],
                "block": "view-context", "title": "当前页面与架构线索（非原始源码）",
                "text": json.dumps(prepared["current_view"], ensure_ascii=False),
                "quality": "generated_knowledge", "location": {},
                "url": str(request.base_url).rstrip("/") + "/versions/" + resource_id})
        chunks = source_chunks(store, resource_id, query, path, int(start), 8)
        if not block_id:
            named = []
            for row in store.rows("SELECT DISTINCT path FROM chunks WHERE version=?", (resource_id,)):
                if re.search(r"(?<![\w/.-])" + re.escape(row["path"]) + r"(?![\w/.-])", query):
                    named.extend(source_chunks(store, resource_id, path=row["path"], limit=2))
            if not named:
                for row in store.rows("SELECT DISTINCT path FROM chunks WHERE version=?", (resource_id,)):
                    if row["path"].lower() in {"readme.md", "readme.rst", "readme.txt"}:
                        named.extend(source_chunks(store, resource_id, path=row["path"], limit=3))
                        break
            seen = set()
            chunks = [c for c in named + chunks if not ((c[1], c[2]) in seen or seen.add((c[1], c[2])))][:8]
        for v, p, a, b, text in chunks:
            url = (
                str(request.base_url).rstrip("/")
                + "/versions/"
                + resource_id
                + "/source?"
                + urlencode({"path": p, "start": a, "end": b})
            )
            items.append(
                {
                    "resource": resource_id,
                    "version": r["version"],
                    "block": p + ":" + str(a),
                    "title": p,
                    "text": text,
                    "location": {"version": resource_id, "path": p, "start": a, "stop": b},
                    "url": url,
                }
            )
        from .architecture.knowledge import load

        if not block_id:
            knowledge = load(store, resource_id)
            pages = (knowledge or {}).get("pages", [])
            words = query.lower().split()
            pages = sorted(
                pages, key=lambda p: sum(w in p.get("markdown", "").lower() for w in words), reverse=True
            )
            for page in pages[:2]:
                items.append(
                    {
                        "resource": resource_id,
                        "version": r["version"],
                        "block": "knowledge:" + page["slug"],
                        "title": "生成知识页（非原始源码）：" + page.get("title", page["slug"]),
                        "text": page.get("markdown", "")[:6000],
                        "quality": "generated_knowledge",
                        "location": {},
                        "url": str(request.base_url).rstrip("/") + "/versions/" + resource_id,
                    }
                )
        if path and chunks:
            last = max(c[3] for c in chunks)
            more = store.rows("SELECT start FROM chunks WHERE version=? AND path=? AND CAST(start AS INTEGER)>? ORDER BY CAST(start AS INTEGER) LIMIT 1",
                              (resource_id, path, last))
            if more:
                items[-1]["continuation_block"] = path + ":" + str(more[0]["start"])
        return {"items": items}

    @app.get("/aos/assistant/v1/history")
    def history(request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=20)):
        authorize(request)
        groups = store.rows(
            "SELECT DISTINCT json_extract(payload,'$.version_id') version,json_extract(payload,'$.conversation_id') conversation FROM interactions ORDER BY version,conversation LIMIT ? OFFSET ?",
            (limit + 1, offset),
        )
        items = []
        for g in groups[:limit]:
            if not g["version"]:
                continue
            conversation = g["conversation"] or "legacy"
            turns = []
            rows = store.rows(
                "SELECT uid,payload FROM interactions WHERE json_extract(payload,'$.version_id')=? AND coalesce(json_extract(payload,'$.conversation_id'),'legacy')=? ORDER BY id",
                (g["version"], conversation),
            )
            for row in rows:
                v = json.loads(row["payload"])
                ev = []
                for i, c in enumerate(v.get("citations", [])):
                    from urllib.parse import urlencode

                    url = (
                        str(request.base_url).rstrip("/")
                        + "/versions/"
                        + g["version"]
                        + "/source?"
                        + urlencode(
                            {"path": c.get("source", ""), "start": c.get("start", 1), "end": c.get("stop", 1)}
                        )
                    )
                    ev.append(
                        {
                            "id": c.get("id", "E" + str(i + 1)),
                            "title": c.get("source", "来源"),
                            "text": c.get("quote", ""),
                            "location": {},
                            "url": url,
                            "resource": g["version"],
                        }
                    )
                turns.append(
                    {
                        "id": row["uid"],
                        "question": v.get("query", ""),
                        "answer": v.get("answer", ""),
                        "state": v.get("status", "completed"),
                        "created": v.get("created_at") or v.get("created"),
                        "evidence": ev,
                        "usage": v.get("usage", {}),
                    }
                )
            items.append(
                {
                    "id": g["version"] + ":" + conversation,
                    "title": turns[0]["question"][:80] if turns else "历史讨论",
                    "resources": [resource(g["version"])],
                    "settings": local_override(store, "chat", conversation),
                    "turns": turns,
                }
            )
        return {
            "instance": store.meta("instance"),
            "items": items,
            "next": offset + limit if len(groups) > limit else None,
        }
