import json
import os
import secrets

from fastapi import HTTPException, Query, Request


def authorize(request):
    expected = os.getenv("AOS_LLM_TOKEN", "")
    if not expected or not secrets.compare_digest(
        request.headers.get("authorization", ""), "Bearer " + expected
    ):
        raise HTTPException(403, "product_token_required")



def note_content(row):
    """Match the reader-note-source/v1 UI contract; never confuse excerpt and user text."""
    selection = json.loads(row["selection"] or "null")
    explanation = row["explanation"] or ""
    prefix = "reader-note-source/v1\n"
    if not selection and explanation.startswith(prefix):
        try:
            value = json.loads(explanation[len(prefix):])
            source = value.get("source")
            if (isinstance(source, dict) and source.get("kind") == "html"
                    and isinstance(source.get("text"), str) and 0 < len(source["text"]) <= 12000
                    and source.get("view") in {"zh", "bilingual"}
                    and isinstance(value.get("explanation"), str)
                    and all(isinstance(source.get(k), str) for k in ("start_unit", "end_unit"))
                    and all(type(source.get(k)) is int and source[k] >= 0 for k in ("start_offset", "end_offset"))):
                selection, explanation = source, value["explanation"]
        except (ValueError, AttributeError, TypeError):
            pass
    return {"用户填写的笔记正文": row["body"], "原文摘录（不是用户笔记正文）": selection,
            "保存的解释（不是用户笔记正文）": explanation}


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
    from .llm_settings import local_override
    from .paper_content import load, search

    def resource(identity):
        doc = store.document(identity)
        return {
            "id": identity,
            "title": doc.get("display_title") or doc["name"],
            "aliases": doc.get("aliases", [doc["name"]]),
            "version": doc.get("content_version") or "",
            "ready": bool(doc.get("content_ready")),
            "context_mode": "full_text",
        }

    def note_record(identity):
        with store.db() as db:
            row = db.execute("SELECT * FROM notes WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise HTTPException(404, "note_not_found")
            return dict(row)

    def note_resource(row):
        return {"id": "note:" + row["id"], "title": "笔记 · " + store.document(row["document"])["name"],
                "kind": "note", "document": row["document"],
                "version": str(row["revision"]), "ready": True}

    @app.get("/aos/assistant/v1/resources")
    def resources(
        request: Request, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)
    ):
        authorize(request)
        with store.db() as db:
            deleted = {r[0] for r in db.execute("SELECT document FROM library_state WHERE deleted_at IS NOT NULL")}
            notes = [dict(r) for r in db.execute("SELECT * FROM notes ORDER BY created DESC,id")]
        docs = [d for d in store.documents() if d["id"] not in deleted]
        items = [resource(d["id"]) for d in docs]
        items.extend(note_resource(n) for n in notes if n["document"] not in deleted)
        return {"items": items[offset:offset + limit],
                "next": offset + limit if offset + limit < len(items) else None}

    @app.get("/aos/assistant/v1/search")
    def lookup(
        request: Request, resource_id: str, query: str = "", block_id: str = "", version: str = "",
        full_text: bool = False, offset: int = Query(0, ge=0)
    ):
        authorize(request)
        if resource_id.startswith("note:"):
            row = note_record(resource_id[5:])
            with store.db() as db:
                deleted = db.execute("SELECT deleted_at FROM library_state WHERE document=?", (row["document"],)).fetchone()
            if deleted and deleted[0]:
                raise HTTPException(404, "resource_unavailable")
            if version and version != str(row["revision"]):
                raise HTTPException(409, "source_version_changed")
            return {"items": [{"resource": resource_id, "version": str(row["revision"]),
                "block": row["id"], "title": note_resource(row)["title"], "quality": "user_note",
                "text": json.dumps(note_content(row), ensure_ascii=False),
                "location": {"document": row["document"], "note": row["id"], "note_revision": row["revision"]},
                "url": str(request.base_url).rstrip("/") + "/?assistant_document=" + row["document"]}]}
        content = load(store, resource_id)
        if not content:
            raise HTTPException(409, "paper_content_requires_processing")
        if version and version != content["parse_sha256"]:
            raise HTTPException(409, "source_version_changed")
        ids = ([b["id"] for b in content["blocks"][offset:offset + 64]] if full_text
               else [block_id] if block_id else search(content, query, 8))
        if not ids and not block_id and not full_text:
            ids = [b["id"] for b in content["blocks"][:8]]
        by_id = {b["id"]: b for b in content["blocks"]}
        blocks = [by_id[i] for i in ids if i in by_id]
        title = store.document(resource_id)["name"]
        items = []
        for b in blocks:
            anchor = {
                "document": resource_id,
                "parse_sha256": content["parse_sha256"],
                "page": b.get("page"),
                "bbox": b.get("bbox"),
                "target": b.get("target"),
                "quote": b["text"][:400],
            }
            items.append(
                {
                    "resource": resource_id,
                    "version": content["parse_sha256"],
                    "block": b["id"],
                    "title": title,
                    "text": b["text"] if full_text else b["text"][:10000],
                    "quality": b.get("quality"),
                    "location": anchor,
                    "url": str(request.base_url).rstrip("/")
                    + "/?assistant_document="
                    + resource_id,
                }
            )
        return {"items": items, "total_blocks": len(content["blocks"]),
                "next": offset + 64 if full_text and offset + 64 < len(content["blocks"]) else None}

    @app.get("/aos/assistant/v1/history")
    def history(
        request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=20)
    ):
        authorize(request)
        sessions = store.sessions()
        items = []
        for s in sessions[offset : offset + limit]:
            turns = []
            for j in store.session_history(s["id"]):
                a = j.get("answer") or {}
                anchors = []
                for i, v in enumerate(a.get("anchors", [])):
                    anchors.append(
                        {
                            "id": "E" + str(i + 1),
                            "resource": s["document"],
                            "version": v.get("parse_sha256", ""),
                            "title": s["title"],
                            "text": v.get("quote", ""),
                            "location": v,
                            "url": str(request.base_url).rstrip("/")
                            + "/?assistant_document="
                            + s["document"],
                        }
                    )
                answer = a.get("markdown") or "\n\n".join(
                    c.get("text", "") for c in a.get("claims", [])
                )
                import re

                answer = re.sub(r"〔来源(\d+)〕", lambda m: "[[E" + m[1] + "]]", answer)
                turns.append(
                    {
                        "id": j["id"],
                        "question": j["request"]["question"],
                        "answer": answer,
                        "evidence": anchors,
                        "state": j["state"],
                        "error": j.get("error"),
                        "created": j.get("created"),
                        "usage": a.get("usage", {}),
                    }
                )
            items.append(
                {
                    "id": s["id"],
                    "title": s["title"],
                    "updated": s["updated"],
                    "resources": [resource(s["document"])],
                    "settings": local_override(store, "chat", s["id"]),
                    "turns": turns,
                }
            )
        return {
            "instance": store.meta("instance_id"),
            "items": items,
            "next": offset + limit if offset + limit < len(sessions) else None,
        }
