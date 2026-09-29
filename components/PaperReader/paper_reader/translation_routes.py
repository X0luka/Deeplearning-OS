"""User-initiated translation routes sharing the reader execution queue."""
from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import Field

from .types import StrictModel


class StartTranslation(StrictModel):
    session: str = Field(min_length=1, max_length=100)
    token: str = Field(pattern=r"^[a-f0-9]{64}$")
    retry: bool = False


def install(app, store, translations, pool, closing, templates):
    @app.get("/translations")
    def page(request: Request):
        return templates.TemplateResponse(request=request, name="translations.html",
                                          context={"csrf": store.meta("csrf")})

    @app.get("/api/translations/preview")
    def preview(session: str):
        selected = store.session(session)
        doc = store.document(selected["document"])
        result = {"document": doc["id"], "name": doc["name"],
                  "parse_sha256": (doc.get("parsed") or {}).get("parse_sha256"),
                  "tasks": translations.list(doc["id"]), "plan": None, "error": None,
                  "pages": (doc.get("parsed") or {}).get("pages", [])}
        result["error"] = "此页保留历史译稿；新中文通读请使用阅读器中的入口。"
        return result

    @app.post("/api/translations", status_code=202)
    def start(value: StartTranslation):
        raise HTTPException(410, "legacy_translation_retired_use_reading_jobs")

    def require(identity):
        task = translations.get(identity)
        if not task:
            raise HTTPException(404, "translation_not_found")
        return task

    @app.get("/api/translations/{identity}")
    def detail(identity: str):
        return require(identity)

    @app.post("/api/translations/{identity}/stop")
    def stop(identity: str):
        require(identity)
        translations.stop(identity)
        return {"stop_requested": True}

    @app.get("/api/translations/{identity}/export")
    def export(identity: str):
        task = require(identity)
        lines = ["# " + task["name"] + " · 中文通读", "", "解析版本：" + task["parse_sha256"],
                 f"完成段落：{task['completed']}/{task['total']}；状态：{task['state']}",
                 "译文未经人工语义核验；图表与公式请对照原件。", ""]
        for s in task["segments"]:
            lines.extend([f"## 第 {s['page']} 页 · 段 {s['seq'] + 1} · {s['state']}", "",
                          "### 原文", "", s["source"], "", "### 译文", "",
                          s["translated"] or "（尚无译文）", ""])
        return Response("\n".join(lines), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition":
                                 f'attachment; filename="translation-{identity[:12]}.md"'})
