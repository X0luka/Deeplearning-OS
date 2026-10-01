"""Local read-only workbench plus authenticated sync requests."""

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, BeforeValidator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from aos.data_center import DataCenter, safe_source_url
from aos.domain import Product, ProductError
from aos.host.appearance import Appearance

OptionalDate = Annotated[date | None, BeforeValidator(lambda value: None if value == "" else value)]


class AppearanceInput(BaseModel):
    mode: Literal["light", "dark", "system"]


def create_app(
    product, adapter, archive, allow_query=False, runtime=None, registry=None, autostart=(),
    startup_errors=None, clients=(), model_store=None, model_tasks=None, assistant_providers=None,
    sync_services=None, network_channels=None
) -> FastAPI:
    if registry is None:
        registry = {product.id: (product, adapter, runtime)} if product else {}
    startup_errors = startup_errors or {}

    def registered(identity):
        if identity not in registry:
            raise HTTPException(404)
        return registry[identity]

    @asynccontextmanager
    async def lifespan(app):
        for _, _, item in registry.values():
            if item and item.activity:
                item.request_pause()
                await item.pause_task
        if runtime:
            await runtime.start()
        for identity in autostart:
            selected_runtime = registered(identity)[2]
            if selected_runtime and not selected_runtime.activity:
                selected_runtime.request_start()
        try:
            yield
        finally:
            await app.state.manual_sync.close()
            if getattr(app.state, "assistant", None):
                await app.state.assistant.close()
            for _, _, item_runtime in registry.values():
                if item_runtime:
                    await item_runtime.close()
            if runtime and all(runtime is not e[2] for e in registry.values()):
                await runtime.close()
            for client in clients:
                await client.aclose()

    app = FastAPI(title="AgentOS K", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
    )
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    center = DataCenter(archive)
    templates.env.filters["safe_source_url"] = safe_source_url
    appearance = Appearance(archive.path.parent / "appearance.json")
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    templates.env.globals["appearance_mode"] = lambda: appearance.read()["mode"]
    token = secrets.token_urlsafe(32)
    templates.env.globals["appearance_token"] = token
    app.state.write_token = token

    def check_write(request):
        if not secrets.compare_digest(request.headers.get("x-aos-token", ""), token):
            raise HTTPException(403, "write_token_required")
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "origin_rejected")

    from aos.application import SyncService
    from aos.host.center_routes import install_center
    if sync_services is None:
        sync_services = {p: SyncService(p, e[1], archive, e[2].activity if e[2] else None)
                         for p, e in registry.items()}
    install_center(app, archive, registry, sync_services, check_write, templates)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path == '/assistant':
            permitted=' '.join(v['url'] for v in (assistant_providers or {}).values())
            response.headers['Content-Security-Policy'] = "frame-ancestors 'self' "+permitted+"; object-src 'none'; base-uri 'none'"
        else:
            response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(ProductError)
    async def product_error(request, exc):
        return JSONResponse({"error": {"code": exc.code}},
                            status_code=409 if exc.code in {"product_disabled", "product_stopping"}
                            else 502)

    def activity_for(identity):
        item = registered(identity)[2]
        return item.activity if item else None

    async def product_health(identity):
        selected_adapter = registered(identity)[1]
        activity = activity_for(identity)
        if activity:
            state = activity.snapshot()["state"]
            if state != "ready":
                return {"status": state, "reachable": False,
                        "reason": "未启用时不检查、不同步；旧档案仍可查看。"}
            try:
                return await activity.run(lambda _: selected_adapter.health())
            except ProductError as exc:
                if exc.code != "product_disabled":
                    raise
                return {"status": activity.snapshot()["state"], "reachable": False,
                        "reason": "接入已暂停"}
        return await selected_adapter.health()

    @app.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        source: str = Query("product_ui", pattern="^(product_ui|legacy_unknown)$"),
        page: int = Query(1, ge=1),
        thread: str | None = Query(None, max_length=64),
        product_id: str | None = None,
        q: str | None = Query(None, max_length=200),
        date_from: OptionalDate = None,
        date_to: OptionalDate = None,
    ):
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "开始日期不能晚于结束日期")
        selected = registry[product_id][0] if product_id in registry else product
        result, jobs, healths = await asyncio.gather(
            asyncio.to_thread(archive.items, source, page, thread, product_id, q,
                              str(date_from) if date_from else None,
                              str(date_to) if date_to else None),
            asyncio.to_thread(archive.jobs),
            asyncio.gather(*(product_health(p) for p in registry)),
        )
        cards = [
            {"product": entry[0], "health": health,
             "manual": bool(entry[2] and entry[2].activity),
             "runtime": entry[2].snapshot() if entry[2] else None}
            for entry, health in zip(registry.values(), healths)
        ]
        health = next((c["health"] for c in cards if selected and
                       c["product"].id == selected.id), None)
        archive_products = {p.id: p for p, _, _ in registry.values()}
        for identity in await asyncio.to_thread(archive.product_ids):
            archive_products.setdefault(identity, Product(identity, identity, "", ""))
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "product": selected,
                "products": [e[0] for e in registry.values()],
                "cards": cards,
                "desktop": getattr(app.state, "desktop", False),
                "archive_products": list(archive_products.values()),
                "sync_products": [p for p in registry if not activity_for(p) or
                                  activity_for(p).snapshot()["state"] == "ready"],
                "product_id": product_id or "",
                "result": result,
                "jobs": jobs,
                "health": health,
                "source": source,
                "thread": thread or "",
                "q": q or "",
                "date_from": date_from.isoformat() if date_from else "",
                "date_to": date_to.isoformat() if date_to else "",
                "token": token,
                "runtime": runtime.snapshot() if runtime else None,
            },
        )

    async def center_listing(source, page, product_id, q, thread):
        result = await asyncio.to_thread(center.records, source, page, product_id, q, thread)
        sources = {row["product"]: row for row in result["sources"]}
        for identity in registry:
            sources.setdefault(identity, {"product": identity, "observed_at": None,
                                          "sync_state": None, "job_id": None})
        for identity, row in sources.items():
            row["name"] = registry[identity][0].name if identity in registry else identity
            row["registered"] = identity in registry
        result["sources"] = list(sources.values())
        return result

    @app.get("/data", response_class=HTMLResponse)
    async def data_home(
        request: Request,
        source: str = Query("all", pattern="^(all|product_ui|legacy_unknown)$"),
        page: int = Query(1, ge=1),
        product_id: str | None = Query(None, max_length=200),
        q: str | None = Query(None, max_length=200),
        thread: str | None = Query(None, max_length=64),
    ):
        return templates.TemplateResponse(request=request, name="knowledge.html", context={
            "token": token, "q": q or "", "products": [e[0] for e in registry.values()],
        })

    @app.get("/api/v1/data/records")
    async def data_records(
        source: str = Query("all", pattern="^(all|product_ui|legacy_unknown)$"),
        page: int = Query(1, ge=1),
        product_id: str | None = Query(None, max_length=200),
        q: str | None = Query(None, max_length=200),
        thread: str | None = Query(None, max_length=64),
    ):
        return await center_listing(source, page, product_id, q, thread)

    async def center_detail(identity):
        item = await asyncio.to_thread(center.detail, identity)
        if item is None:
            raise HTTPException(404)
        return item

    @app.get("/data/{identity}", response_class=HTMLResponse)
    async def data_detail(request: Request, identity: int):
        item = await center_detail(identity)
        selected = registry.get(item["product"])
        return templates.TemplateResponse(request=request, name="data_detail.html", context={
            "item": item, "products": [e[0] for e in registry.values()],
            "source_product": selected[0] if selected else None,
        })

    @app.get("/api/v1/data/records/{identity}")
    async def data_record(identity: int):
        return await center_detail(identity)

    @app.get("/api/v1/data/records/{identity}/export")
    async def data_export(identity: int):
        item = await center_detail(identity)
        return Response(json.dumps({"format": "agentos-record-v1", "record": item},
                                   ensure_ascii=False, indent=2),
                        media_type="application/json",
                        headers={"Content-Disposition":
                                 f'attachment; filename="agentos-record-{identity}.json"'})

    @app.get("/products/{product_id}/open", response_class=HTMLResponse)
    async def open_product(product_id: str, request: Request):
        selected, _selected_adapter, _selected_runtime = registered(product_id)
        return templates.TemplateResponse(
            request=request,
            name="opening.html",
            context={
                "product": selected,
                "products": [e[0] for e in registry.values()],
                "manual_activation": activity_for(product_id) is not None,
                "startup_errors": startup_errors.get(product_id, {}),
                "token": token,
            },
        )

    @app.get("/api/v1/apps/{product_id}/runtime")
    async def runtime_status(product_id: str):
        selected, _selected_adapter, selected_runtime = registered(product_id)
        if selected_runtime is None:
            raise HTTPException(503, "runtime_unavailable")
        return {**selected_runtime.snapshot(), "ui_url": selected.ui_url}

    @app.post("/api/v1/apps/{product_id}/start", status_code=202)
    async def start_product(product_id: str, request: Request):
        check_write(request)
        _selected, _selected_adapter, selected_runtime = registered(product_id)
        if selected_runtime is None:
            raise HTTPException(503, "runtime_unavailable")
        return selected_runtime.request_start()

    @app.post("/api/v1/apps/{product_id}/pause", status_code=202)
    async def pause_product(product_id: str, request: Request):
        check_write(request)
        selected_runtime = registered(product_id)[2]
        if selected_runtime is None:
            raise HTTPException(503, "runtime_unavailable")
        return selected_runtime.request_pause()

    @app.get("/history/{identity}", response_class=HTMLResponse)
    async def detail(request: Request, identity: int):
        item = await asyncio.to_thread(archive.detail, identity)
        if not item:
            raise HTTPException(404)
        return templates.TemplateResponse(
            request=request,
            name="detail.html",
            context={
                "item": item,
                "product": registry.get(item["product"],
                                        (Product(item["product"], item["product"], "", ""),))[0],
                "products": [e[0] for e in registry.values()],
            },
        )

    @app.get("/api/v1/appearance")
    async def get_appearance():
        return await asyncio.to_thread(appearance.read)

    @app.post("/api/v1/appearance")
    async def set_appearance(request: Request, value: AppearanceInput):
        check_write(request)
        return await asyncio.to_thread(appearance.write, value.mode)

    @app.get("/api/v1/identity")
    async def host_identity():
        return {"product_id": "agentos-k", "contract_version": "1.0", "products": list(registry)}

    @app.get("/api/v1/apps")
    async def apps():
        return [{"id": p.id, "name": p.name, "ui_url": p.ui_url,
                 "activity": rt.snapshot() if rt else None} for p, _, rt in registry.values()]

    @app.get("/api/v1/apps/{product_id}/health")
    async def health(product_id: str):
        return await product_health(product_id)

    @app.post("/api/v1/apps/{product_id}/sync", status_code=202)
    async def sync(product_id: str, request: Request):
        check_write(request)
        selected, _selected_adapter, _selected_runtime = registered(product_id)
        return {"job_id": app.state.manual_sync.start([selected.id])[0]}

    @app.get("/api/v1/history")
    async def history(
        source: str = Query("product_ui", pattern="^(product_ui|legacy_unknown)$"),
        page: int = Query(1, ge=1),
        thread: str | None = Query(None, max_length=64),
        product_id: str | None = None,
        q: str | None = Query(None, max_length=200),
        date_from: OptionalDate = None,
        date_to: OptionalDate = None,
    ):
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "开始日期不能晚于结束日期")
        return await asyncio.to_thread(archive.items, source, page, thread, product_id, q,
                              str(date_from) if date_from else None,
                              str(date_to) if date_to else None)

    @app.get("/api/v1/jobs/{identity}")
    async def job(identity: int):
        item = await asyncio.to_thread(archive.job, identity)
        if item:
            return item
        raise HTTPException(404)

    @app.post("/api/v1/apps/{product_id}/invoke")
    async def invoke(product_id: str, request: Request):
        check_write(request)
        if not allow_query:
            raise HTTPException(403, "real_queries_disabled")
        _selected, selected_adapter, _selected_runtime = registered(product_id)
        body = await request.json()
        q = body.get("query") if isinstance(body, dict) else None
        if not isinstance(q, str) or not 1 <= len(q) <= 2000:
            raise HTTPException(422)
        activity = activity_for(product_id)
        if activity:
            return await activity.run(lambda _: selected_adapter.query(q))
        return await selected_adapter.query(q)

    if model_store is not None:
        from aos.llm.routes import install
        install(app, model_store, model_tasks or {}, templates, check_write,
                [e[0] for e in registry.values()], network_channels=network_channels)
    if model_store:
        from aos.assistant.routes import install as install_assistant
        install_assistant(app, model_store, assistant_providers or {}, archive, check_write, token)
    return app
