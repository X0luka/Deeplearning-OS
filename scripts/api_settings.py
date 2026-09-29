"""Configuration-only app factory; no listener or launch command."""
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse


def deny_outbound(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise PermissionError("configuration_only_no_outbound")


def create_settings_app(directory):
    from aos.adapters.sqlite import SQLiteArchive
    from aos.host.web import create_app
    from aos.llm.store import ModelStore
    app = create_app(None, None, SQLiteArchive(directory / "archive.db"),
                     model_store=ModelStore(directory / "llm"))

    @app.middleware("http")
    async def configuration_only(request, call_next):
        path = request.url.path
        if path == "/":
            return RedirectResponse("/settings/models")
        allowed = (path == "/settings/models" and request.method == "GET"
                   or path.startswith("/static/") and request.method == "GET"
                   or path == "/api/v1/appearance"
                   or path == "/api/v1/llm"
                   or path == "/api/v1/llm/network"
                   or path.startswith("/api/v1/llm/connections")
                   or path.startswith("/api/v1/llm/settings/"))
        if not allowed or path.rstrip("/").endswith("/refresh"):
            return JSONResponse({"detail": "测试配置入口不开放联网或生成，请手动添加模型。"},
                                status_code=403, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        if path == "/settings/models" and response.status_code == 200:
            body = b"".join([chunk async for chunk in response.body_iterator]).decode()
            banner = ('<p role="note" style="padding:12px;border:2px solid #b7791f">'
                      '<strong>独立测试配置 · DeepSeek</strong><br>'
                      '本页只保存测试配置，不开放联网或生成。'
                      '你只需在 API Key 中粘贴 DeepSeek 密钥，再点“保存连接”。'
                      '其他设置稍后处理。不要点击“获取模型”。入口启动 10 分钟后自动关闭，已保存配置保留。</p>')
            body = body.replace('<p id="model-notice"', banner + '<p id="model-notice"', 1)
            body = body.replace('<title>', '<title>独立测试配置 · ', 1)
            script = """<script>
const setupForm=document.getElementById('connection-form');
setupForm.elements.name.value='DeepSeek 测试';
setupForm.elements.base_url.value='https://api.deepseek.com';
document.getElementById('manual-id').value='deepseek-flash';
</script>"""
            body = body.replace("</body>", script + "</body>")
            headers = {k: v for k, v in response.headers.items()
                       if k.lower() not in {"content-length", "content-type"}}
            return HTMLResponse(body, headers=headers)
        return response
    return app
