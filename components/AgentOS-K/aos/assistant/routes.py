import asyncio
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from aos.llm.store import validate_settings

from .service import Service
from .store import Store, now


def install(app, models, providers, archive, check_write, token):
    store = Store(archive.path.parent / "assistant" / "assistant.db")
    from aos.adapters.sqlite import ReadOnlySQLiteArchive
    service = Service(store, models, providers, ReadOnlySQLiteArchive(archive.path))
    app.state.assistant = service
    prefix = "/api/v1/assistant"
    origins = {v["url"].rstrip("/") for v in providers.values()}

    @app.middleware("http")
    async def protect_assistant(request, call_next):
        if request.url.path.startswith("/api/v1/assistant/"):
            from fastapi.responses import JSONResponse

            try:
                check_write(request)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return await call_next(request)

    async def body(request):
        check_write(request)
        raw = await request.body()
        if len(raw) > 50000:
            raise HTTPException(413, "request_too_large")
        try:
            return json.loads(raw)
        except ValueError:
            raise HTTPException(422, "invalid_json") from None

    async def resources(values):
        if not isinstance(values, list) or len(values) > 100:
            raise HTTPException(422, "invalid_resources")
        catalog = await service.catalog()
        mapping = {(r["provider"], r["id"]): r for r in catalog["items"]}
        result = []
        for r in values:
            key = (r.get("provider"), r.get("id"))
            if key not in mapping:
                raise HTTPException(409, "resource_unavailable")
            if mapping[key] not in result:
                result.append(mapping[key])
        return result

    def ui_revision():
        root = Path(__file__).parent.parent / "host" / "static"
        files = [Path(__file__).parent / "ui.html", *sorted(root.glob("assistant*.js")),
                 *sorted(root.glob("assistant*.mjs")), root / "assistant.css"]
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()[:20]

    @app.middleware("http")
    async def assistant_cache_policy(request, call_next):
        response = await call_next(request)
        if request.url.path in ("/assistant", "/assistant/version"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/assistant"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/assistant/version")
    def page_version():
        return {"revision": ui_revision()}

    @app.get("/assistant", response_class=HTMLResponse)
    def page(request: Request):
        parent = request.query_params.get("parent", "")
        if parent and parent not in origins:
            raise HTTPException(403, "embed_origin_rejected")
        text = (Path(__file__).parent / "ui.html").read_text()
        config = json.dumps(
            {
                "token": token,
                "revision": ui_revision(),
                "parent": parent,
                "providers": [
                    {"id": k, "name": v["name"], "section": v["section"]}
                    for k, v in providers.items()
                ],
            },
            ensure_ascii=False,
        ).replace("<", chr(92) + "u003c")
        return HTMLResponse(text.replace("__CONFIG__", config))

    @app.get(prefix + "/resources")
    async def catalog():
        return await service.catalog()

    @app.get(prefix + "/conversations")
    def conversations():
        return {
            "items": [
                store.conversation(r["id"])
                for r in store.rows("SELECT id FROM conversations WHERE id NOT IN (SELECT id FROM deleted_conversations) ORDER BY updated DESC")
            ]
        }

    @app.post(prefix + "/conversations")
    async def create(request: Request):
        v = await body(request)
        rs = await resources(v.get("resources", []))
        sections = {r["section"] for r in rs}
        section = next(iter(sections)) if len(sections) == 1 else "general"
        title = str(v.get("title") or (rs[0]["title"] if len(rs) == 1 else "新对话"))
        scope = (
            rs[0]["provider"] if len(rs) == 1 and rs[0]["provider"] in providers else "assistant"
        )
        return store.create(title, section, rs, model_scope=scope)

    @app.get(prefix + "/conversations/{identity}")
    def read(identity: str):
        return {"conversation": store.conversation(identity), "turns": store.turns(identity)}

    @app.delete(prefix + "/conversations/{identity}")
    def delete_conversation(identity: str, request: Request):
        check_write(request)
        try:
            store.delete_conversation(identity)
        except ValueError as exc:
            raise HTTPException(409 if str(exc) == "conversation_busy" else 404, str(exc)) from None
        return {"deleted": True, "id": identity}

    @app.patch(prefix + "/conversations/{identity}")
    async def patch(identity: str, request: Request):
        v = await body(request)
        c = store.conversation(identity)
        if any(t["state"] in ("queued", "running") for t in store.turns(identity)):
            raise HTTPException(409, "conversation_busy")
        if "title" in v:
            c["title"] = str(v["title"]).strip()[:180] or c["title"]
        if "resources" in v:
            c["resources"] = await resources(v["resources"])
        if "scope" in v:
            if v["scope"] not in ("current", "selected", "all"):
                raise HTTPException(422, "invalid_scope")
            c["scope"] = v["scope"]
        if "settings" in v:
            c["settings"] = validate_settings(v["settings"], True)
            if c["settings"].get("document_input") == "pdf":
                raise HTTPException(409, "unified_raw_pdf_not_supported")
            models.resolve(c["model_scope"], "chat", c["settings"])
        sections = {r["section"] for r in c["resources"]}
        c["section"] = next(iter(sections)) if len(sections) == 1 else "general"
        with store.db() as db:
            db.execute(
                "UPDATE conversations SET title=?,resources=?,scope=?,settings=?,section=?,updated=? WHERE id=?",
                (
                    c["title"],
                    json.dumps(c["resources"]),
                    c["scope"],
                    json.dumps(c["settings"]),
                    c["section"],
                    now(),
                    identity,
                ),
            )
        return store.conversation(identity)

    @app.get(prefix + "/settings")
    def settings(conversation: str = ""):
        c = (
            store.conversation(conversation)
            if conversation
            else {"model_scope": "assistant", "settings": {}}
        )
        public = models.public()
        error = None
        try:
            effective = models.resolve(c["model_scope"], "chat", c["settings"])
        except ValueError as exc:
            effective = {}
            error = str(exc)
        effective = {
            k: v for k, v in effective.items() if k not in ("api_key", "base_url", "proxy")
        }
        return {
            "connections": [
                {"id": v["id"], "name": v["name"], "enabled": v["enabled"], "models": v["models"]}
                for v in public["connections"]
            ],
            "effective": effective,
            "override": c["settings"],
            "error": error,
        }

    @app.post(prefix + "/conversations/{identity}/turns", status_code=202)
    async def submit(identity: str, request: Request):
        v = await body(request)
        c = store.conversation(identity)
        question = str(v.get("question", "")).strip()
        key = v.get("request_key", "")
        if (
            not question
            or len(question) > 16000
            or not isinstance(key, str)
            or not 8 <= len(key) <= 100
        ):
            raise HTTPException(422, "invalid_question")
        old = store.rows("SELECT id,conversation,question FROM turns WHERE request_key=?", (key,))
        if old:
            if old[0]["conversation"] != identity or old[0]["question"] != question:
                raise HTTPException(409, "request_key_reused")
            return store.turn(old[0]["id"])
        resolved = models.resolve(c["model_scope"], "chat", c["settings"], True)
        if resolved.get("document_input") == "pdf":
            raise HTTPException(409, "unified_raw_pdf_not_supported")
        catalog = await service.catalog()
        mapping = {(r["provider"], r["id"]): r for r in catalog["items"]}
        rs = []
        for resource in c["resources"]:
            found = mapping.get((resource["provider"], resource["id"]))
            if found is None:
                raise HTTPException(409, "resource_unavailable")
            rs.append(found)
        accessible = rs if c["scope"] == "selected" else catalog["items"]
        view = v.get("view") or {}
        if not isinstance(view, dict):
            raise HTTPException(422, "invalid_view_context")
        view = {k: str(value)[:2000] for k, value in view.items()
                if k in {"provider", "id", "page", "entity_id", "knowledge_id", "node_id", "analysis_id"}}
        if not any(r["provider"] == view.get("provider") and r["id"] == view.get("id") for r in rs):
            view = {}
        snapshot = {
            "view": view,
            "resources": rs,
            "accessible_resources": accessible,
            "catalog_errors": catalog["errors"],
            "scope": c["scope"],
            "settings": {
                **{
                    k: resolved[k]
                    for k in (
                        "max_output_tokens",
                        "thinking",
                        "temperature",
                        "tools",
                        "web_search",
                        "document_input",
                    )
                    if k in resolved
                },
                "selection": {"connection": resolved["connection"], "model": resolved["model"]},
            },
            "model_scope": c["model_scope"],
            "selection": str(v.get("selection", ""))[:10000],
        }
        turn, fresh = store.enqueue(identity, key, question, snapshot)
        if fresh:
            service.start(turn)
        return store.turn(turn)

    @app.get(prefix + "/turns/{identity}")
    def turn(identity: str):
        return store.turn(identity)

    @app.post(prefix + "/turns/{identity}/cancel")
    async def cancel(identity: str, request: Request):
        await body(request)
        store.turn(identity)
        task = service.tasks.get(identity)
        if task:
            task.cancel()
        return {"requested": bool(task)}

    @app.get(prefix + "/turns/{identity}/events")
    async def events(identity: str, request: Request):
        store.turn(identity)

        async def generate():
            previous = ""
            while not await request.is_disconnected():
                turn = store.turn(identity)
                encoded = json.dumps(turn, ensure_ascii=False)
                if encoded != previous:
                    yield "data: " + encoded + "\n\n"
                    previous = encoded
                if turn["state"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.3)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post(prefix + "/migrate")
    async def migrate(request: Request):
        await body(request)
        return {"items": await service.migrate()}

    @app.post(prefix + "/diagnostics")
    async def diagnostic(request: Request):
        v = await body(request)
        safe = {k: str(v.get(k, ""))[:500] for k in ("code", "conversation", "turn", "component")}
        with store.db() as db:
            db.execute(
                "INSERT INTO diagnostics(created,payload) VALUES(?,?)", (now(), json.dumps(safe))
            )
        return {"saved": True}

    @app.exception_handler(ValueError)
    async def error(request, exc):
        import re

        from fastapi.responses import JSONResponse

        code = str(exc)
        code = code if re.fullmatch("[a-zA-Z0-9_]{1,100}", code) else "invalid_request"
        return JSONResponse({"detail": code}, status_code=409)

    @app.get("/api/v1/assistant/records")
    def records():
        return {
            "items": [
                {
                    "conversation": store.conversation(row["id"]),
                    "messages": len(store.turns(row["id"])),
                }
                for row in store.rows("SELECT id FROM conversations WHERE id NOT IN (SELECT id FROM deleted_conversations) ORDER BY updated DESC")
            ]
        }
