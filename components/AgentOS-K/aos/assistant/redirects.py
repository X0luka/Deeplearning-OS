"""Legacy preview addresses: navigation only, owned by the desktop session."""
import json

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse


def redirect_app(origin):
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PATCH", "DELETE", "PUT", "OPTIONS"])
    async def redirect(request: Request, path: str):
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"detail":"preview_retired","url":origin},status_code=410)
        target = origin + request.url.path + ("?"+request.url.query if request.url.query else "")
        # Fragment never reaches HTTP; retain paper/note anchors in the browser.
        encoded = json.dumps(target).replace("<", chr(92) + "u003c")
        return HTMLResponse('<!doctype html><meta charset="utf-8"><title>转到正式服务</title><p>正在转到正式服务…</p><script>location.replace('+encoded+'+location.hash)</script>', headers={"Cache-Control":"no-store"})
    return app
