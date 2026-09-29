import argparse
import asyncio
import fcntl
import io
import json
import os
import secrets
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from . import llm_client, llm_settings
from .appearance import appearance
from .architecture import service as architecture
from .engine import Engine, config, worker
from .model_config import api_key, source_proxy
from .readable import readable_excerpt
from .source import canonical, check_update
from .store import Store, uid


class RepoInput(BaseModel):
    url: str = Field(max_length=300)
    ref: str = Field(default="", max_length=200)


class DeleteInput(BaseModel):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(max_length=300)


class AskInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation: str = Field(default_factory=uid, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    analysis_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    node_id: str | None = Field(default=None, max_length=1000)
    knowledge_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    entity_id: str | None = Field(default=None, max_length=100)


class ChatInput(AskInput):
    request_key: str = Field(default="", pattern=r"^[a-zA-Z0-9_-]{0,64}$")
    page: str = Field(default="", max_length=100)
    selection: str = Field(default="", max_length=3000)


class ModelInput(BaseModel):
    enabled: bool = False
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=150)
    key_env: str = Field(default="WIKI_API_KEY", pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    max_calls: int | None = None


def create_app(root=None, engine_factory=Engine):
    store = Store(root or os.getenv("WIKI_DATA_DIR", "var"))
    engine = engine_factory(store)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        with (store.root / "worker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            task = asyncio.create_task(worker(engine))
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="GitHub Wiki", lifespan=lifespan)
    app.add_api_route("/api/v1/appearance", appearance, methods=["GET"])
    app.state.store, app.state.token = store, token
    from .pipeline import install as install_pipeline
    install_pipeline(app, store)
    llm_settings.install(app, store, {"chat", "structured", "knowledge"})
    from .assistant_tools import install as install_assistant_tools
    from .assistant_tools import ui_host
    install_assistant_tools(app, store)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    markdown = MarkdownIt("commonmark", {"html": False}).enable("table")
    # No remote images: reading untrusted repositories must not trigger browser exfiltration.
    markdown.disable("image")
    templates.env.filters["markdown"] = lambda text: markdown.render(readable_excerpt(text))

    @app.middleware("http")
    async def guard(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if store.meta("maintenance_lock") == "true":
                return Response("数据库空间整理中，请稍后重试", status_code=503)
            if not secrets.compare_digest(request.headers.get("x-wiki-token", ""), token):
                return Response("write_token_required", status_code=403)
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return Response("origin_rejected", status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 16384:
                    return Response("request_too_large", status_code=413)
            except ValueError:
                return Response("invalid_length", status_code=400)
        response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'",
            }
        )
        if ui_host(request):
            host=ui_host(request)
            response.headers['Content-Security-Policy']=response.headers['Content-Security-Policy'].replace("script-src 'self'", "script-src 'self' "+host)+"; frame-src "+host
        return response

    @app.exception_handler(ValueError)
    async def configuration_error(request, exc):
        from fastapi.responses import JSONResponse
        code = str(exc)
        return JSONResponse({'detail': code if code.replace('_','').isalnum() and len(code)<90
                             else 'invalid_configuration'}, status_code=409)

    from fastapi.responses import JSONResponse

    from .rules import RuleError

    @app.exception_handler(RuleError)
    async def rule_error(request: Request, exc: RuleError):
        return JSONResponse({"detail": str(exc), "failure": exc.failure}, status_code=409)

    def render(request, name, **context):
        return templates.TemplateResponse(
            request=request, name=name, context={"token": token, "config": config(store), "managed": llm_client.managed(), "unified": bool(ui_host(request)), "assistant_host": ui_host(request) or llm_client.host_url(), "chat_config": config(store, task="chat"), **context}
        )

    def require_version(identity):
        if store.meta("deleted-version:"+identity):
            raise HTTPException(410, "来源仓库已删除；已保留的问答与引用快照仍可阅读")
        v = store.version(identity)
        if v and store.meta("deletion:"+v["repo"]):
            raise HTTPException(409, "仓库数据清理中，请查看删除任务")
        if not v:
            raise HTTPException(404, "version_not_found")
        from .architecture.knowledge import load

        knowledge = load(store, identity)
        if knowledge:
            from .architecture.page_diagrams import decorate
            v["pages"] = decorate(store, identity, knowledge)
            v["knowledge_id"] = knowledge["knowledge_id"]
            v["knowledge_status"] = knowledge.get("status", "ready")
            v["mode"] = "model"
        return v

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        from .pipeline import recent
        return render(
            request,
            "home.html",
            repos=store.rows("SELECT * FROM repos ORDER BY rowid DESC"),
            jobs=recent(store),
        )

    @app.get("/health")
    async def health():
        settings = config(store)
        return {
            "status": "ok",
            "busy": bool(store.one(
                "SELECT id FROM jobs WHERE state IN ('queued','running') LIMIT 1"
            )),
            "checks": {
                "storage": "ok",
                "reading": "source_ready",
                "model": "configured" if settings["enabled"] and (settings.get("configured") if llm_client.managed() else api_key(settings["key_env"])) else "disabled",
            },
        }

    @app.get("/aos/v1/describe")
    async def describe():
        return {
            "product_id": "github-wiki",
            "model_configuration": "managed-v1" if llm_client.managed() else "standalone",
            "configuration_host": llm_client.host_url() if llm_client.managed() else None,
            "instance_id": store.meta("instance"),
            "contract_version": "1.0",
            "capabilities": ["health", "history_read", "history_changes"],
        }

    @app.get("/api/repos")
    async def repos():
        return {"items": store.rows("SELECT * FROM repos ORDER BY rowid DESC")}

    @app.post("/api/repos", status_code=201)
    async def add_repo(body: RepoInput):
        try:
            url = canonical(body.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if body.ref.startswith("-") or any(ord(c) < 32 for c in body.ref):
            raise HTTPException(422, "invalid_ref")
        with store.db() as c:
            old = c.execute("SELECT id FROM repos WHERE url=?", (url,)).fetchone()
            identity = old[0] if old else uid()
            if old and store.meta("deletion:"+old[0]):
                raise RuleError("repo_deleting", stage="import", resource=old[0])
            if old:
                c.execute("UPDATE repos SET ref=? WHERE id=?", (body.ref, identity))
            else:
                c.execute("INSERT INTO repos VALUES(?,?,?,NULL)", (identity, url, body.ref))
        return {"repo_id": identity}

    @app.get("/api/rules")
    async def rule_catalog():
        from .rules import RULES, VERSION
        from .source import MAX_ARCHIVE, MAX_ENTRIES, MAX_FILE, MAX_FILES, MAX_SCAN, MAX_TEXT
        return {"version": VERSION, "rules": [
            {"code": code, "rule_id": row[0], "scope": row[1], "message": row[2], "action": row[3]}
            for code, row in RULES.items()], "limits": {"archive_bytes": MAX_ARCHIVE,
            "text_bytes": MAX_TEXT, "file_bytes": MAX_FILE, "files": MAX_FILES,
            "scan_bytes": MAX_SCAN, "entries": MAX_ENTRIES}}

    @app.get("/api/repos/{identity}/deletion-preview")
    async def deletion_preview(identity: str):
        from .repository_data import preview
        return await asyncio.to_thread(preview, store, identity)

    @app.delete("/api/repos/{identity}", status_code=202)
    async def delete_repository(identity: str, body: DeleteInput):
        from .repository_data import request_delete
        return await asyncio.to_thread(request_delete, store, identity, body.preview_token, body.name)

    @app.post("/api/repos/{identity}/build", status_code=202)
    async def build(
        identity: str, force: bool = False, commit: str | None = Query(None, pattern=r"^[a-f0-9]{40}$")
    ):
        if not store.repo(identity):
            raise HTTPException(404)
        return {
            "job_id": store.enqueue(
                "build", {"repo": identity, "force": force, **({"commit": commit} if commit else {})}
            )
        }

    @app.get("/api/repos/{identity}/updates")
    async def repo_updates(identity: str):
        repo = store.repo(identity)
        if not repo:
            raise HTTPException(404)
        try:
            latest = await check_update(repo["url"], repo["ref"], proxy=source_proxy(store))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        current_version = store.version(repo["current"]) if repo["current"] else None
        return {
            **latest,
            "current_commit": current_version["sha"] if current_version else None,
            "available": not current_version or latest["commit"] != current_version["sha"],
        }

    @app.get("/jobs/{identity}", response_class=HTMLResponse)
    async def job_page(identity: str, request: Request):
        if not store.job(identity):
            raise HTTPException(404)
        return render(request, "job.html", job=store.job(identity))

    @app.get("/api/jobs/{identity}")
    async def get_job(identity: str):
        row = store.job(identity)
        if not row:
            raise HTTPException(404)
        return row

    @app.post("/api/jobs/{identity}/cancel")
    async def cancel(identity: str):
        with store.db() as c:
            c.execute(
                "UPDATE jobs SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' "
                "ELSE state END WHERE id=? AND state IN ('queued','running')",
                (identity,),
            )
        return {"job_id": identity}

    @app.post("/api/jobs/{identity}/retry", status_code=202)
    async def retry(identity: str):
        row = store.job(identity)
        partial = row and row["state"] == "succeeded" and (row.get("result") or {}).get("partial")
        if not row or (row["state"] not in {"failed", "cancelled", "interrupted"} and not partial):
            raise HTTPException(409, "job_not_retryable")
        args = dict(row["args"])
        if partial and row["kind"] == "knowledge":
            args["refresh"] = True
        return {"job_id": store.enqueue(row["kind"], args)}

    @app.get("/versions/{identity}", response_class=HTMLResponse)
    async def version_page(identity: str, request: Request, page: str = "overview"):
        v = require_version(identity)
        current = next((p for p in v["pages"] if p["slug"] == page), None)
        if not current:
            raise HTTPException(404)
        return render(
            request,
            "version.html",
            version=v,
            repo=store.repo(v["repo"]),
            page=current,
            versions=store.rows(
                "SELECT id,sha,mode,created FROM versions WHERE repo=? ORDER BY created DESC", (v["repo"],)
            ),
        )

    @app.get("/versions/{identity}/architecture", response_class=HTMLResponse)
    async def architecture_page(identity: str, request: Request):
        v = require_version(identity)
        return render(request, "architecture.html", version=v, repo=store.repo(v["repo"]))

    @app.post("/api/versions/{identity}/architecture", status_code=202)
    async def architecture_build(identity: str):
        require_version(identity)
        return {"job_id": store.enqueue("architecture", {"version": identity})}

    @app.get("/api/versions/{identity}/architecture")
    async def architecture_data(
        identity: str,
        scope: str = "",
        view: Literal["overview", "dependencies"] = "overview",
        query: str = "",
        tests: bool = False,
        external: bool = False,
        types: bool = True,
        node_id: str | None = None,
    ):
        require_version(identity)
        try:
            return architecture.status(
                store,
                identity,
                scope=scope,
                view=view,
                query=query,
                tests=tests,
                external=external,
                types=types,
                node_id=node_id,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/versions/{identity}/architecture/export")
    async def architecture_export(
        identity: str,
        format: Literal["json", "mermaid"] = "json",
        scope: str = "",
        view: Literal["overview", "dependencies"] = "overview",
        query: str = "",
        tests: bool = False,
        external: bool = False,
        types: bool = True,
    ):
        require_version(identity)
        try:
            graph = architecture.load(store, identity)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        if format == "json":
            return Response(
                json.dumps(graph, ensure_ascii=False),
                media_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="architecture.json"'},
            )
        projection = architecture.project(graph, scope, view, query, tests, external, types)
        if projection["too_large"]:
            raise HTTPException(422, "architecture_view_too_large")
        return Response(
            architecture.mermaid(projection),
            media_type="text/plain",
            headers={"Content-Disposition": 'attachment; filename="architecture.mmd"'},
        )

    @app.get("/api/versions/{identity}/page-diagrams")
    async def page_diagram_status(identity: str):
        require_version(identity)
        from .architecture.auto_diagrams import status

        return status(store, identity)

    @app.post("/api/versions/{identity}/page-diagrams", status_code=202)
    async def page_diagram_generate(identity: str):
        require_version(identity)
        from .architecture.auto_diagrams import enqueue

        if not config(store, task="knowledge")["enabled"]:
            raise HTTPException(422, "model_not_configured")
        return {"job_id": enqueue(store, identity)}

    @app.get("/api/versions/{identity}/knowledge")
    async def knowledge_view(identity: str):
        require_version(identity)
        from .architecture.knowledge import load

        value = load(store, identity)
        return value or {"status": "missing"}

    @app.post("/api/versions/{identity}/knowledge", status_code=202)
    async def knowledge_generate(identity: str):
        require_version(identity)
        if not config(store)["enabled"]:
            raise HTTPException(422, "model_not_configured")
        return {"job_id": store.enqueue("knowledge", {"version": identity})}

    @app.get("/api/versions/{identity}/reading")
    async def reading_view(
        identity: str,
        scope: str = "",
        query: str = "",
        focus: str = "",
        direction: Literal["both", "incoming", "outgoing"] = "both",
        knowledge_id: str | None = Query(None, pattern=r"^[a-f0-9]{64}$"),
        entity_id: str | None = None,
    ):
        require_version(identity)
        from .architecture.reading import reading

        try:
            if bool(knowledge_id) != bool(entity_id):
                raise ValueError("knowledge_context_pair_required")
            paths = None
            if entity_id:
                from .architecture.knowledge import selection

                _, _, proofs = selection(store, identity, knowledge_id, entity_id)
                paths = {p["path"] for p in proofs.values()}
            return reading(store, identity, scope, query, focus, direction, paths=paths)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/versions/{identity}/reading")
    async def reading_generate(identity: str):
        require_version(identity)
        if not architecture.current(store, identity):
            raise HTTPException(409, "architecture_not_generated")
        if not config(store)["enabled"]:
            raise HTTPException(422, "model_not_configured")
        return {"job_id": store.enqueue("semantic", {"version": identity})}

    @app.get("/api/versions/{identity}")
    async def version_data(identity: str):
        return require_version(identity)

    @app.get("/versions/{identity}/source", response_class=HTMLResponse)
    async def source_view(
        identity: str, request: Request, path: str, start: int = Query(1, ge=1), end: int = Query(1, ge=1)
    ):
        v = require_version(identity)
        if path not in {f["path"] for f in v["manifest"]["files"]}:
            raise HTTPException(404)
        content = (store.root / "raw" / identity / path).read_text()
        return render(
            request, "source.html", version=v, path=path, lines=content.splitlines(), start=start, end=end
        )

    @app.get("/api/versions/{identity}/source")
    async def source_page(identity: str, path: str):
        v = require_version(identity)
        if path not in {f["path"] for f in v["manifest"]["files"]}:
            raise HTTPException(404)
        content = (store.root / "raw" / identity / path).read_text()
        return Response(content, media_type="text/plain; charset=utf-8")

    @app.post("/api/versions/{identity}/ask", status_code=202)
    async def ask(identity: str, body: AskInput):
        require_version(identity)
        if bool(body.knowledge_id) != bool(body.entity_id) or (body.entity_id and body.node_id):
            raise HTTPException(422, "invalid_knowledge_context")
        if body.entity_id:
            from .architecture.knowledge import selection

            try:
                selection(store, identity, body.knowledge_id, body.entity_id)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        if bool(body.node_id) != bool(body.analysis_id):
            raise HTTPException(422, "architecture_context_requires_node_and_analysis")
        if body.node_id:
            try:
                graph = architecture.load(store, identity, body.analysis_id)
                architecture.selection(graph, body.node_id)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        if not body.question.strip():
            raise HTTPException(422, "empty_question")
        return {
            "job_id": store.enqueue(
                "ask",
                {
                    "version": identity,
                    "question": body.question.strip(),
                    "conversation": body.conversation,
                    "analysis_id": body.analysis_id,
                    "node_id": body.node_id,
                    "knowledge_id": body.knowledge_id,
                    "entity_id": body.entity_id,
                },
            )
        }


    @app.post("/api/versions/{identity}/chat", status_code=202)
    async def chat(identity: str, body: ChatInput):
        v = require_version(identity)
        if not config(store, task="chat", session=body.conversation)["enabled"]:
            raise HTTPException(422, "model_not_configured")
        if not body.question.strip():
            raise HTTPException(422, "empty_question")
        if body.page and body.page not in {p["slug"] for p in v["pages"]}:
            raise HTTPException(422, "page_not_found")
        if bool(body.knowledge_id) != bool(body.entity_id) or bool(body.analysis_id) != bool(body.node_id):
            raise HTTPException(422, "invalid_context")
        if body.entity_id and body.node_id:
            raise HTTPException(422, "invalid_context")
        try:
            if body.entity_id:
                from .architecture.knowledge import selection
                selection(store, identity, body.knowledge_id, body.entity_id)
            if body.node_id:
                graph = architecture.load(store, identity, body.analysis_id)
                architecture.selection(graph, body.node_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"job_id": store.enqueue("chat", {"version": identity, **body.model_dump()})}

    @app.get("/api/versions/{identity}/conversations")
    async def conversations(identity: str):
        require_version(identity)
        rows = store.rows(
            "SELECT json_extract(args,'$.conversation') AS id, "
            "json_extract(args,'$.question') AS title, max(created) AS updated "
            "FROM jobs WHERE kind='chat' AND json_extract(args,'$.version')=? "
            "GROUP BY json_extract(args,'$.conversation') ORDER BY updated DESC LIMIT 100",
            (identity,),
        )
        return {"items": rows}

    @app.get("/api/versions/{identity}/conversations/{conversation}")
    async def conversation_read(identity: str, conversation: str):
        require_version(identity)
        from .chat import history
        items = history(store, identity, conversation)
        for item in items:
            item["html"] = markdown.render(item["answer"])
        rows = store.rows(
            "SELECT id FROM jobs WHERE kind='chat' AND json_extract(args,'$.version')=? "
            "AND json_extract(args,'$.conversation')=? ORDER BY created DESC LIMIT 100",
            (identity, conversation),
        )
        return {"items": items, "jobs": [store.job(r["id"]) for r in reversed(rows)],
                "history_policy": "模型参考最近最多8轮；此处显示最近100条记录",
                "model_enabled": config(store, task="chat", session=conversation)["enabled"]}

    @app.get("/api/chat/jobs/{identity}/events")
    async def chat_events(identity: str, request: Request):
        row = store.job(identity)
        if not row or row["kind"] != "chat":
            raise HTTPException(404, "chat_job_not_found")
        async def events():
            while not await request.is_disconnected():
                row = store.job(identity)
                data = {"state": row["state"], "error": row["error"]}
                if row["state"] == "succeeded":
                    item = store.one("SELECT payload FROM interactions WHERE id=?",
                                     (row["result"]["interaction_id"],))
                    data["item"] = json.loads(item["payload"])
                    data["item"]["html"] = markdown.render(data["item"]["answer"])
                yield "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                if row["state"] not in {"queued", "running"}:
                    break
                await asyncio.sleep(1)
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.get("/api/history")
    async def history(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        rows = store.rows(
            "SELECT payload FROM interactions ORDER BY id DESC LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        )
        return {
            "items": [json.loads(r["payload"]) for r in rows],
            "total": store.one("SELECT count(*) AS n FROM interactions")["n"],
            "page": page,
        }

    @app.get("/api/history/{identity}")
    async def detail(identity: int):
        row = store.one("SELECT payload FROM interactions WHERE id=?", (identity,))
        if not row:
            raise HTTPException(404)
        return json.loads(row["payload"])

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request):
        return render(request, "history.html", items=(await history(1, 100))["items"])

    @app.get("/history/{identity}", response_class=HTMLResponse)
    async def answer_page(identity: int, request: Request):
        item = await detail(identity)
        version = (json.loads(store.meta("deleted-version:"+item["version_id"]) or "{}")
                   if item.get("source_deleted") else require_version(item["version_id"]))
        return render(request, "answer.html", item=item, version=version)

    @app.get("/aos/v1/changes")
    async def changes(cursor: str | None = None, limit: int = Query(100, ge=1, le=100)):
        try:
            return store.changes(cursor, limit)
        except ValueError:
            raise HTTPException(409, "cursor_expired") from None

    @app.get("/api/versions/{identity}/export")
    async def export(identity: str):
        v = require_version(identity)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for page in v["pages"]:
                z.writestr(page["slug"] + ".md", page["markdown"])
            z.writestr(
                "index.md", "\\n".join("- [" + p["title"] + "](" + p["slug"] + ".md)" for p in v["pages"])
            )
            from .architecture.knowledge import load

            knowledge = load(store, identity)
            if knowledge:
                z.writestr("knowledge.json", json.dumps(knowledge, ensure_ascii=False, indent=2))
            from .architecture.auto_diagrams import export_markdown, published

            diagrams = published(store, identity, knowledge) if knowledge else None
            if diagrams:
                z.writestr("page-diagrams.json", json.dumps(diagrams, ensure_ascii=False, indent=2))
                z.writestr("page-diagrams.md", export_markdown(diagrams, store.repo(v["repo"])["url"]))
            architecture_dir = architecture.directory(store, identity)
            if architecture_dir.exists():
                for artifact in architecture_dir.glob("*.json"):
                    z.write(artifact, "architecture/" + artifact.name)
            z.writestr("manifest.json", json.dumps(v["manifest"], ensure_ascii=False, indent=2))
        return Response(
            out.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="wiki-{v["sha"][:12]}.zip"'},
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return render(request, "settings.html", key_present=bool(api_key(config(store)["key_env"])))

    @app.post("/api/settings")
    async def settings_save(body: ModelInput):
        if llm_client.managed():
            raise HTTPException(409, 'use_platform_model_settings')
        parsed = urlsplit(body.base_url)
        if body.base_url and (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(422, "invalid_model_base_url")
        if body.enabled and (not body.base_url or not body.model or not api_key(body.key_env)):
            raise HTTPException(422, "model_environment_not_ready")
        store.set_meta("model_config", json.dumps(body.model_dump()))
        return {"saved": True}

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8601)
    parser.add_argument("--data-dir", default=os.getenv("WIKI_DATA_DIR", "var"))
    args = parser.parse_args()
    uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
