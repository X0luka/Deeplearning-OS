import json

import httpx

from wiki_app.main import create_app
from wiki_app.model_config import api_key, config
from wiki_app.readable import readable_excerpt
from wiki_app.store import Store


def test_default_deepseek_from_wsl_literal(tmp_path):
    (tmp_path / ".bashrc").write_text('export DEEPSEEK_API_KEY="test-local-key" # comment\n')
    settings = config(Store(tmp_path / "data"))
    assert settings["enabled"] and settings["model"] == "deepseek-v4-flash"
    assert settings["base_url"] == "https://api.deepseek.com"
    assert api_key(settings["key_env"]) == "test-local-key"
    assert "test-local-key" not in json.dumps(settings)


def test_no_key_and_shell_expression_not_executed(tmp_path):
    shell = tmp_path / ".bashrc"
    shell.write_text('export DEEPSEEK_API_KEY="$(touch marker)"\n')
    assert not api_key("DEEPSEEK_API_KEY")
    assert not config(Store(tmp_path / "data"))["enabled"]
    assert not (tmp_path / "marker").exists()


def test_saved_disable_and_custom_settings_take_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-default")
    monkeypatch.setenv("CUSTOM_KEY", "test-custom")
    store = Store(tmp_path / "data")
    store.set_meta("model_config", json.dumps({"enabled": False}))
    assert not config(store)["enabled"]
    store.set_meta(
        "model_config",
        json.dumps(
            {
                "enabled": True,
                "base_url": "http://localhost:9999/v1",
                "model": "custom",
                "key_env": "CUSTOM_KEY",
            }
        ),
    )
    assert config(store)["model"] == "custom"
    assert api_key(config(store)["key_env"]) == "test-custom"


def test_readme_html_numbers_chinese_and_code_preserved():
    text = '> <div><h1>项目 123</h1><img src="https://bad.test/x"><p>用途 &amp; 版本 2.0</p></div>\n> <script>evil()</script>'
    cleaned = readable_excerpt(text)
    assert "项目 123" in cleaned and "用途 &amp; 版本 2.0" in cleaned
    assert "<div" not in cleaned and "https://" not in cleaned and "evil()" not in cleaned
    code = "~~~html\n> <div>源码 123</div>\n~~~"
    assert readable_excerpt(code) == code


async def test_settings_detects_wsl_key_without_leaking(tmp_path):
    (tmp_path / ".bashrc").write_text("export DEEPSEEK_API_KEY=test-never-expose\n")
    app = create_app(tmp_path / "data")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver") as client:
        response = await client.get("/settings")
        assert "已检测到密钥" in response.text
        assert "deepseek-v4-flash" in response.text
        assert "test-never-expose" not in response.text
        assert (await client.get("/health")).json()["checks"]["model"] == "configured"


def test_generation_anchors_readme_and_diversifies_evidence():
    from wiki_app.engine import chunks_for, section_chunks

    files = {
        "README.md": "# 项目用途\n一个代理工作台。数字 123。",
        "package.json": '{"scripts":{"start":"node server.js"}}',
        "docs/architecture.md": "architecture module component class",
        "__tests__/overview.test.ts": ("overview purpose project " * 20 + "\n") * 600,
    }
    selected = section_chunks(chunks_for("v", files), "overview", "README purpose overview project")
    assert selected[0][1] == "README.md"
    assert any(c[1] == "package.json" for c in selected)
    assert sum(c[1] == "__tests__/overview.test.ts" for c in selected) <= 2


async def test_deepseek_json_mode_and_truncation(monkeypatch):
    import pytest

    from wiki_app.engine import model_answer

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-default")
    original = httpx.AsyncClient

    def handler(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 1800
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"claims":['},
                    }
                ]
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError, match="model_output_truncated"):
        await model_answer(
            {
                "key_env": "DEEPSEEK_API_KEY",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            },
            "用途",
            [],
        )


async def test_named_package_file_wins_over_keyword_matches(tmp_path):
    from wiki_app.engine import Engine, chunks_for

    store = Store(tmp_path / "data")
    files = {
        "package.json": '{"version":"1.17.0","engines":{"node":">=24"}}',
        "other.js": ("package json Node version engines " * 20 + "\n") * 600,
    }
    with store.db() as db:
        db.execute("INSERT INTO repos VALUES('r','https://github.com/e/r','',NULL)")
    manifest = {"files": [{"path": p} for p in files], "included": 2, "skipped": []}
    store.publish("v", "r", "a" * 40, "source", manifest, [], chunks_for("v", files))
    store.set_meta("model_config", json.dumps({"enabled": True}))

    async def capture(settings, topic, sources):
        assert sources[0]["source"] == "package.json"
        assert '"node":">=24"' in sources[0]["text_preview"]
        return "版本1.17.0，Node >=24", sources[:1]

    result = await Engine(store, model=capture).ask(
        {"version": "v", "question": "package.json 的版本和 Node 最低版本是多少？", "conversation": "c"},
        "none",
    )
    assert result["interaction_id"] == 1
