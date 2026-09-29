import argparse
import json
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import html_reading, library, llm_client, llm_settings, reading_jobs
from .appearance import appearance
from .assistant_config import Preferences, catalog, preferences, save_preferences
from .assistant_config import status as reading_status
from .evidence import build_context
from .model import Gateway
from .reader_store import ReaderStore
from .reading_assistant import ReadingAssistant
from .runner import run_job, worker_lock
from .translation_routes import install as install_translations
from .translations import TranslationStore
from .types import Question, Selection, StrictModel


class Ask(StrictModel):
    session: str
    request_key: str = Field(min_length=16, max_length=80)
    parse_sha256: str
    question: Question


class Note(StrictModel):
    session: str
    body: str = Field(max_length=12000)
    explanation: str = Field(default="", max_length=30000)
    selection: Selection | None = None
    parse_sha256: str | None = None
    revision: int | None = None


class Position(StrictModel):
    page: int = Field(ge=1)
    zoom: float = Field(ge=0.1, le=3)


class SessionInput(StrictModel):
    document: str = Field(pattern=r"^[a-f0-9]{64}$")


class Consent(SessionInput):
    accept: bool


def create_app(data=Path("var/reader"), gateway_factory=Gateway):
    store = ReaderStore(data)
    translations = TranslationStore(store)
    closing = threading.Event()
    cancellations = {}
    modern = gateway_factory is Gateway
    assets = Path(__file__).parent
    templates = Jinja2Templates(directory=assets / "templates")
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-question")

    @asynccontextmanager
    async def lifespan(app):
        with worker_lock(store):
            store.recover()
            translations.recover()
            reading_service.recover()
            with store.db() as db:
                ids = [r[0] for r in db.execute("SELECT id FROM jobs WHERE state='queued'")]
            for identity in ids:
                store.set_job(identity, "interrupted", error="server_restarted_no_retry")
        try:
            yield
        finally:
            closing.set()
            pool.shutdown(wait=True)
            reading_service.close()

    app = FastAPI(title="PaperReader", lifespan=lifespan)
    app.add_api_route("/api/v1/appearance", appearance, methods=["GET"])
    app.state.store = store
    app.state.translations = translations
    html_reading.install(app, store)
    reading_service = reading_jobs.install(app, store)
    library.install(app, store)
    install_translations(app, store, translations, pool, closing, templates)
    llm_settings.install(app, store, {"chat", "reading"})
    from .assistant_tools import install as install_assistant_tools
    from .assistant_tools import ui_host
    install_assistant_tools(app, store)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    app.mount("/static", StaticFiles(directory=assets / "static"), name="static")

    @app.middleware("http")
    async def local_writes(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "origin_rejected"}, status_code=403)
            if request.headers.get("x-paper-token") != store.meta("csrf"):
                return JSONResponse({"detail": "write_token_required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = (
            "no-store" if not request.url.path.startswith("/static") else "public,max-age=3600"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; worker-src 'self' blob:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'"
        )
        if ui_host(request):
            host=ui_host(request)
            response.headers['Content-Security-Policy']=response.headers['Content-Security-Policy'].replace("script-src 'self'", "script-src 'self' "+host).replace("frame-src 'self'", "frame-src 'self' "+host)
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        code = str(exc)
        return JSONResponse(
            {"detail": code if len(code) < 100 else "invalid_input"}, status_code=409
        )

    @app.get("/")
    def home(request: Request):
        return templates.TemplateResponse(
            request=request, name="reader.html", context={"csrf": store.meta("csrf"), "managed": llm_client.managed(), "unified": bool(ui_host(request)), "assistant_host": ui_host(request) or llm_client.host_url()}
        )

    def permit_path(document):
        return store.root / "permits" / (document + ".json")

    def model_status(document=None, session=""):
        if modern:
            return reading_status(store, document, session)
        result = {
            "configured": bool(os.getenv("DEEPSEEK_API_KEY")),
            "model": "deepseek-v4-flash",
            "enabled": False,
            "remaining": 0,
            "max_calls": 20,
        }
        if document and permit_path(document).exists():
            gateway = gateway_factory(store, permit_path(document))
            with store.db() as db:
                row = db.execute(
                    "SELECT calls FROM budget WHERE id=?", (gateway.identity,)
                ).fetchone()
            result.update(enabled=True, remaining=20 - (row[0] if row else 0))
        return result

    @app.get("/api/models")
    def models():
        if llm_client.managed():
            return {"managed": True, "items": [], "settings": {}}
        return {"items": catalog(), "settings": preferences(store)}

    @app.put("/api/settings")
    def settings(value: Preferences):
        return save_preferences(store, value.model_dump())

    @app.post("/api/questions/{identity}/cancel")
    def cancel(identity: str):
        job = store.job(identity)
        if job["state"] in {"queued", "running"}:
            event = cancellations.get(identity)
            if event:
                event.set()
            return {"cancellation_requested": bool(event)}
        return {"cancellation_requested": False}

    @app.get("/api/state")
    def state():
        files = library.listing(store)
        visible = {f["document"] for f in files["items"]}
        return {
            "documents": store.documents(),
            "sessions": [s for s in store.sessions() if s["document"] in visible],
            "library": files,
            "model": model_status(),
        }

    @app.post("/api/documents", status_code=201)
    def upload(file: Annotated[UploadFile, File()]):
        content = file.file.read(30 * 1024 * 1024 + 1)
        if len(content) > 30 * 1024 * 1024:
            raise HTTPException(413, "pdf_limit_30MiB")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / (Path(file.filename or "paper.pdf").name[:180])
            path.write_bytes(content)
            doc = store.register_pdf(path)
        return {"document": doc, "session": store.create_session(doc["id"])}

    @app.get("/api/documents/{identity}")
    def document(identity: str):
        return store.document(identity)

    @app.get("/api/documents/{identity}/pdf")
    def original(identity: str):
        doc = store.document(identity)
        return FileResponse(store.root / "raw" / (doc["id"] + ".pdf"), media_type="application/pdf")

    @app.post("/api/sessions", status_code=201)
    def new_session(value: SessionInput):
        return store.create_session(value.document)

    @app.get("/api/sessions/{identity}")
    def session(identity: str):
        value = store.session(identity)
        return {
            "session": value,
            "jobs": store.session_history(identity),
            "notes": store.notes(identity),
            "model": model_status(value["document"], identity),
        }

    @app.put("/api/sessions/{identity}/position")
    def position(identity: str, value: Position):
        store.position(identity, value.page, value.zoom)
        return {"saved": True}

    @app.post("/api/assistant/enable")
    def enable(value: Consent):
        if modern:
            store.document(value.document)
            return model_status(value.document)
        store.document(value.document)
        if not value.accept:
            raise ValueError("consent_required")
        if not os.getenv("DEEPSEEK_API_KEY"):
            raise ValueError("model_key_not_configured")
        path = permit_path(value.document)
        path.parent.mkdir(exist_ok=True)
        if path.exists() and model_status(value.document)["remaining"] > 0:
            return model_status(value.document)
        permit = {
            "authorization": "user_approved",
            "scope": "reader explicit button consent " + uuid.uuid4().hex,
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "key_env": "DEEPSEEK_API_KEY",
            "documents": [value.document],
            "max_calls": 20,
            "max_input_bytes": 3000000,
            "max_output_tokens": 60000,
            "output_per_call": 3000,
            "thinking_disabled": True,
        }
        if os.getenv("PAPER_MODEL_PROXY"):
            permit["proxy"] = os.environ["PAPER_MODEL_PROXY"]
        path.write_text(json.dumps(permit))
        path.chmod(0o600)
        return model_status(value.document)

    def execute_reading(identity, settings, cancelled):
        def update(stage, text):
            store.set_job(
                identity, "running", answer={"markdown": text, "stage": stage, "partial": True}
            )

        try:
            with worker_lock(store):
                if cancelled.is_set():
                    store.set_job(identity, "interrupted", error="user_cancelled")
                    return
                store.claim(identity)
                ReadingAssistant(store, settings).run(identity, cancelled, update)
        except Exception as exc:  # noqa: BLE001 -- durable job failure boundary
            code = (
                str(exc)
                if isinstance(exc, ValueError) and len(str(exc)) < 100
                else type(exc).__name__
            )
            store.set_job(identity, "interrupted" if cancelled.is_set() else "failed", error=code)
        finally:
            cancellations.pop(identity, None)

    def execute(identity, gateway):
        try:
            run_job(store, identity, gateway)
        except Exception as exc:  # noqa: BLE001 -- persist failures outside worker boundary
            # A lock acquisition failure must not leave a paid request looking queued forever.
            store.set_job(identity, "failed", error=type(exc).__name__)

    @app.post("/api/questions", status_code=202)
    def ask(value: Ask):
        doc = store.document(value.question.document)
        if not doc["parsed"] or value.parse_sha256 != doc["parsed"]["parse_sha256"]:
            raise ValueError("document_version_changed")
        settings = llm_settings.effective(store, session=value.session, execution=True) if llm_client.managed() else preferences(store)
        current = model_status(value.question.document)
        if llm_client.managed():
            current['configured'] = settings['configured']
        if not current["configured"] or not current["enabled"] or current["remaining"] <= 0:
            raise ValueError("assistant_enable_required")
        if not doc.get("content_ready"):
            raise ValueError("paper_content_requires_processing")
        build_context(store, value.question)
        gateway = None if modern else gateway_factory(store, permit_path(value.question.document))
        identity, fresh = store.enqueue_session(value.question, value.session, value.request_key)
        if fresh:
            if modern:
                event = threading.Event()
                cancellations[identity] = event
                pool.submit(execute_reading, identity, settings, event)
            else:
                pool.submit(execute, identity, gateway)
        return {"id": identity, "created": fresh}

    @app.get("/api/questions/{identity}")
    def question(identity: str):
        return store.job(identity)

    def note_input(value):
        if value.selection:
            session = store.session(value.session)
            build_context(
                store,
                Question(
                    document=session["document"],
                    mode="selection",
                    question="保存选段",
                    selection=value.selection,
                ),
            )
        return value.selection.model_dump() if value.selection else None

    @app.post("/api/notes", status_code=201)
    def create_note(value: Note):
        return {
            "id": store.save_note(
                value.session, value.body, note_input(value), value.explanation, value.parse_sha256
            )
        }

    @app.put("/api/notes/{identity}")
    def edit_note(identity: str, value: Note):
        return {
            "id": store.save_note(
                value.session,
                value.body,
                parse_sha256=value.parse_sha256,
                identity=identity,
                revision=value.revision,
            )
        }

    @app.get("/api/notes")
    def list_notes(session: str):
        return {"items": store.notes(session)}

    @app.get("/health")
    def health():
        with store.db() as db:
            db.execute("SELECT 1")
            busy = bool(db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1"
            ).fetchone())
        return {
            "status": "ok",
            "busy": busy or translations.busy() or reading_service.busy(),
            "checks": {
                "storage": "ok",
                "reader": "ok",
                "model": "configured" if model_status()["configured"] else "not_configured",
                "semantic_quality": "not_accepted",
            },
        }

    @app.get("/aos/v1/describe")
    def describe():
        return {
            "product_id": "paper",
            "instance_id": store.meta("instance_id"),
            "contract_version": "1.0",
            "capabilities": ["health", "history_read", "history_changes"],
            "quality": "experience_preview",
            "model_configuration": "managed-v1" if llm_client.managed() else "standalone",
            "configuration_host": llm_client.host_url() if llm_client.managed() else None,
        }

    @app.get("/aos/v1/changes")
    def changes(cursor: str | None = None, limit: int = 50):
        return store.changes(cursor, limit)

    @app.get("/api/history")
    def history(page: int = 1, page_size: int = 20):
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("invalid_pagination")
        with store.db() as db:
            rows = db.execute(
                "SELECT payload FROM reader_records ORDER BY id DESC LIMIT ? OFFSET ?",
                (page_size, (page - 1) * page_size),
            ).fetchall()
            total = db.execute("SELECT count(*) FROM reader_records").fetchone()[0]
        return {"items": [json.loads(r[0]) for r in rows], "total": total}

    @app.get("/api/history/{identity}")
    def detail(identity: int):
        with store.db() as db:
            row = db.execute(
                "SELECT payload FROM reader_records WHERE id=?", (identity,)
            ).fetchone()
        if not row:
            raise ValueError("history_not_found")
        return json.loads(row[0])

    return app


def main():
    from .settings import load_existing_environment

    if not llm_client.managed():
        load_existing_environment()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8602)
    parser.add_argument("--data-dir", type=Path, default=Path("var/reader"))
    args = parser.parse_args()
    provider_file = args.data_dir / "provider.json"
    if provider_file.exists():
        proxy = json.loads(provider_file.read_text()).get("proxy")
        if proxy and not os.getenv("PAPER_MODEL_PROXY"):
            os.environ["PAPER_MODEL_PROXY"] = proxy
    uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
