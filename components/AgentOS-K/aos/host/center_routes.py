"""Versioned center API; original archive routes remain compatible."""
import asyncio

from fastapi import HTTPException, Query, Request

from aos.center import Center
from aos.manual_sync import ManualSync


def install_center(app, archive, registry, services, check_write, templates):
    center = Center(archive)
    manual = ManualSync(archive, services)
    app.state.center = center
    app.state.manual_sync = manual

    @app.get("/api/v1/data/items")
    async def items(page: int = Query(1, ge=1), q: str = Query("", max_length=200),
                    kind: str = Query("all", pattern="^(all|note|question)$"),
                    thread: str = Query("", max_length=300), marked: bool = True,
                    topic: str = Query("", max_length=100)):
        keys = None
        if topic:
            graph = await asyncio.to_thread(center.map)
            if topic == "pending":
                keys = graph["pending"]
            elif topic == "uncertain":
                keys = [k for k, v in graph["records"].items() if v.get("warning")]
            else:
                match = next((t for t in graph["topics"] if t["id"] == topic), None)
                if match is None:
                    raise HTTPException(404, "topic_not_found")
                keys = match["keys"]
        return await asyncio.to_thread(center.items, page, q, kind, thread, marked, keys)

    @app.get("/api/v1/data/items/{key}")
    async def detail(key: str):
        item = await asyncio.to_thread(center.detail, key)
        if item is None:
            raise HTTPException(404)
        return item

    @app.get("/data/item/{key}")
    async def detail_page(request: Request, key: str):
        item = await detail(key)
        return templates.TemplateResponse(request=request, name="center_detail.html", context={
            "item": item, "products": [e[0] for e in registry.values()]})

    @app.get("/api/v1/data/map")
    async def graph():
        return await asyncio.to_thread(center.map)

    @app.get("/api/v1/data/status")
    async def status():
        result = await asyncio.to_thread(center.status)
        result["products"] = [{"id": p, "name": v[0].name,
                               "ready": archive.activity(p)["state"] == "ready"}
                              for p, v in registry.items()]
        return result

    @app.post("/api/v1/data/sync", status_code=202)
    async def sync(request: Request):
        check_write(request)
        products = [p for p in services if archive.activity(p)["state"] == "ready"]
        return {"job_ids": manual.start(products)}

    return center
