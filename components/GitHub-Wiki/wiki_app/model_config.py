"""Local model defaults; never execute shell configuration or persist credentials."""

import json
import os
import re
import shlex
from pathlib import Path

from . import llm_client, llm_settings


def api_key(name):
    value = os.getenv(name, "")
    if value or name != "DEEPSEEK_API_KEY":
        return value
    # WSL GUI services do not inherit interactive bash exports. Read only one
    # literal assignment, never source the file or expand shell expressions.
    try:
        lines = (Path.home() / ".bashrc").read_text().splitlines()
    except (OSError, UnicodeError):
        return ""
    found = ""
    for line in lines:
        if not re.match(r"^\s*(?:export\s+)?DEEPSEEK_API_KEY=", line):
            continue
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            continue
        if parts and parts[0] == "export":
            parts = parts[1:]
        if len(parts) != 1:
            continue
        candidate = parts[0].split("=", 1)[1]
        if candidate and not any(c in candidate for c in ("$", chr(96), "\\n", "\\r")):
            found = candidate
    return found


def config(store, task='structured', execution=False, session=''):
    if llm_client.managed():
        try:
            if task in {"knowledge", "structured"}:
                result = llm_client.resolve(task, {**llm_settings.local_override(store, task, session), "web_search": False}, execution)
            else:
                result = llm_settings.effective(store, task, session, execution)
            return {**result, 'enabled': result['configured'], 'key_env': '', 'max_calls':None}
        except ValueError as exc:
            if execution:
                raise
            return {'enabled':False, 'configured':False, 'source':'agentos', 'error':str(exc),
                    'key_env':'', 'base_url':'', 'model':'未配置', 'proxy':None, 'max_calls':None}
    saved = json.loads(store.meta("model_config") or "{}")
    explicit = bool(saved)
    wiki_custom = bool(os.getenv("WIKI_MODEL_BASE_URL") or os.getenv("WIKI_MODEL"))
    deepseek_default = not explicit and not wiki_custom
    key_env = "DEEPSEEK_API_KEY" if deepseek_default else "WIKI_API_KEY"
    return {
        "enabled": bool(saved.get("enabled", bool(api_key(key_env)) if deepseek_default else False)),
        "base_url": saved.get(
            "base_url",
            "https://api.deepseek.com" if deepseek_default else os.getenv("WIKI_MODEL_BASE_URL", ""),
        ),
        "model": saved.get("model", "deepseek-v4-flash" if deepseek_default else os.getenv("WIKI_MODEL", "")),
        "max_calls": None,
        "key_env": saved.get("key_env", key_env),
        "source": "deepseek_default" if deepseek_default else "custom",
        "proxy": os.getenv("WIKI_HTTPS_PROXY") or store.meta("network_proxy") or None,
    }


def source_proxy(store):
    if llm_client.managed():
        return llm_client.network()["proxy"]
    return os.getenv("WIKI_HTTPS_PROXY") or store.meta("network_proxy") or None
