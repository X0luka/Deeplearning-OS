"""Read-only, document-bound reading artifacts. Switching views never calls a model."""
import hashlib
import json
import re

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse


def install(app, store):
    def folder(document):
        if not re.fullmatch(r"[a-f0-9]{64}", document):
            raise HTTPException(404, "document_not_found")
        store.document(document)
        return store.root / "reading" / document

    @app.get("/api/documents/{document}/reading")
    def reading(document: str):
        root = folder(document)
        current = root / "current.json"
        if current.is_file():
            identity = json.loads(current.read_text()).get("job", "")
            if not re.fullmatch(r"[a-f0-9]{64}", identity):
                raise HTTPException(409, "reading_integrity_error")
            root = store.root / "reading-jobs" / identity
        path = root / "bundle.json"
        if not path.is_file():
            return {"available": False}
        manifest = json.loads((root / "manifest.json").read_text())
        if not manifest.get("reading_ready", True):
            return {"available": False}
        payload = path.read_bytes()
        if (manifest.get("document") != document
                or hashlib.sha256(payload).hexdigest() != manifest.get("bundle_sha256")):
            raise HTTPException(409, "reading_integrity_error")
        return {"available": True, **json.loads(payload)}

    @app.get("/api/documents/{document}/reading/markdown")
    def markdown(document: str, language: str = "zh"):
        from .paper_content import load
        from .paper_content import markdown as export
        content = load(store, document)
        if not content:
            raise HTTPException(409, "content_not_ready")
        return PlainTextResponse(export(content, reading(document)["translated"] if language == "zh" else None),
                                 media_type="text/markdown")

    @app.get("/reading-content/{revision}")
    def original_content(revision: str):
        if not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise HTTPException(404)
        root = store.root / "reading-jobs" / revision
        if not (root / "content.json").exists():
            raise HTTPException(404)
        manifest = json.loads((root / "manifest.json").read_text())
        raw = (root / "content.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest.get("content_sha256"):
            raise HTTPException(409, "reading_integrity_error")
        content = json.loads(raw)
        soup = __import__("bs4").BeautifulSoup(content["original"], "html.parser")
        for node in soup.select("[data-content-id]"):
            node["id"] = "content-" + node["data-content-id"]
        from .reading_structure import CSS
        return HTMLResponse('<!doctype html><meta charset="utf-8"><meta name="aos-appearance">'
            '<script src="/static/appearance.js"></script><link rel="stylesheet" href="/static/appearance.css">'
            '<style>' + CSS + 'body{background:var(--aos-bg);color:var(--aos-text)}:target{outline:2px solid var(--aos-muted)}</style><main>' + str(soup) + '</main>')

    @app.get("/api/documents/{document}/reading/assets/{name}")
    def asset(document: str, name: str):
        root = folder(document)
        if not re.fullmatch(r"image-[0-9]{3}\.(png|jpg|jpeg|webp)", name):
            raise HTTPException(404, "asset_not_found")
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise HTTPException(404, "asset_not_found")
        expected = json.loads(manifest_path.read_text()).get("assets", {}).get(name)
        path = root / name
        if not expected or not path.is_file():
            raise HTTPException(404, "asset_not_found")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise HTTPException(409, "reading_integrity_error")
        return FileResponse(path)
