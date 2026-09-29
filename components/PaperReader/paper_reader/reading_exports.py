"""Verified, portable full-reading downloads; incomplete jobs cannot export."""
import base64
import hashlib
import io
import json
import re
import zipfile
from urllib.parse import quote

from bs4 import BeautifulSoup
from fastapi import HTTPException
from fastapi.responses import Response

from . import paper_content


def verified(service, identity):
    if not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise HTTPException(404)
    task = service.get(identity)
    if task["state"] not in {"completed", "awaiting_review"} or task["done"] != task["total"]:
        raise HTTPException(409, "reading_not_complete")
    folder = service.root / identity
    try:
        manifest = json.loads((folder / "manifest.json").read_text())
        values = {}
        for name in ("bundle", "content"):
            raw = (folder / (name + ".json")).read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest[name + "_sha256"]:
                raise ValueError("checksum")
            values[name] = json.loads(raw)
        images = {}
        for name, expected in manifest["assets"].items():
            if not re.fullmatch(r"image-[0-9]{4}\.(png|jpg|webp)", name):
                raise ValueError("asset")
            data = (folder / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("checksum")
            images[name] = data
    except (OSError, ValueError, KeyError):
        raise HTTPException(409, "reading_integrity_error") from None
    return task, values["bundle"], values["content"], images


def install(app, store, service):
    @app.get("/api/documents/{document}/reading-task")
    def latest(document: str):
        store.document(document)
        with store.db() as db:
            row = db.execute("SELECT id FROM reading_jobs WHERE document=? ORDER BY created DESC, rowid DESC LIMIT 1", (document,)).fetchone()
        return {"task": service.get(row[0]) if row else None}

    @app.get("/api/reading-jobs/{identity}/preview")
    def preview(identity: str):
        task, bundle, _, _ = verified(service, identity)
        return {**bundle, "available": True, "draft": task["state"] == "awaiting_review"}

    @app.get("/api/reading-jobs/{identity}/files/{kind}")
    def download(identity: str, kind: str):
        if kind not in {"html", "markdown"}:
            raise HTTPException(404)
        task, bundle, content, images = verified(service, identity)
        title = store.document(task["document"])["name"]
        label = "中文处理稿（待验收）" if task["state"] == "awaiting_review" else "中文通读"
        quality = bundle.get('quality', {})
        count = quality.get('issues', 0)
        if count:
            label += '（' + str(count) + '处需核对）'
        status_text = ('已处理 ' + str(task['done']) + '/' + str(task['total']) + ' 单元；'
            + str(quality.get('translated', task['done'])) + ' 单元已有译文，'
            + str(quality.get('original', 0)) + ' 单元保留原文，' + str(count) + ' 处需核对。')
        parse_quality = bundle.get('parse_quality', {})
        if parse_quality.get('source_images'):
            status_text += ' 另有 ' + str(parse_quality['source_images']) + ' 处解析内容保留为原文图像，请核对红色提示。'
        filename = re.sub(r'[\\/:*?"<>|\r\n]', '_', basename(title))[:100] + "-" + label
        prefix = "/api/reading-jobs/" + identity + "/assets/"
        if kind == "html":
            soup = BeautifulSoup(bundle["translated"], "html.parser")
            for img in soup.select("img[src]"):
                src = img["src"]
                name = src.removeprefix(prefix)
                if not src.startswith(prefix) or name not in images:
                    raise HTTPException(409, "reading_integrity_error")
                mime = {"png": "png", "jpg": "jpeg", "webp": "webp"}[name.rsplit('.', 1)[1]]
                img["src"] = "data:image/" + mime + ";base64," + base64.b64encode(images[name]).decode()
            import html
            result = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">"
                '<title>' + html.escape(title + ' · ' + label) + '</title><style>'
                + bundle["css"].replace('</style', '') + 'main{max-width:1100px;margin:auto;padding:24px}img{max-width:100%;height:auto}table{display:block;overflow:auto}p,td{overflow-wrap:anywhere}</style>'
                '<main><p>' + label + ' · ' + status_text
                + ' 全文语义尚未逐段验收。</p>' + str(soup) + '</main></html>')
            payload, media, extension = result.encode(), "text/html; charset=utf-8", ".html"
        else:
            result = '# ' + title + ' · ' + label + '\n\n' + status_text + '\n\n' + paper_content.markdown(content, bundle["translated"])
            for name in images:
                result = result.replace(prefix + name, 'images/' + name)
            missing = [name for name in images if 'images/' + name not in result]
            if missing:
                result += '\n\n## 原文图片附件\n\n' + '\n\n'.join('![' + name + '](images/' + name + ')' for name in missing)
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('中文通读.md', result)
                archive.writestr('核对清单.json', json.dumps(bundle.get('issues', {}), ensure_ascii=False, indent=2))
                archive.writestr('解析核对清单.json', json.dumps(bundle.get('parse_quality', {}), ensure_ascii=False, indent=2))
                archive.writestr('说明.txt', label + '。解压后打开 Markdown，images 文件夹需保留。HTML 下载更完整地保留公式和版式。全文语义尚未逐段验收。')
                for name, data in images.items():
                    archive.writestr('images/' + name, data)
            payload, media, extension = output.getvalue(), 'application/zip', '.zip'
        return Response(payload, media_type=media, headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename + extension)})


def basename(title):
    return title[:-4] if title.lower().endswith('.pdf') else title
