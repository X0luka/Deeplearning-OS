"""Provider capabilities and durable settings; never contains credentials."""

import hashlib
import json
import os
from typing import Literal

from pydantic import Field

from . import llm_client, llm_settings
from .types import StrictModel

MODELS = {
    "deepseek-flash": {
        "provider": "deepseek",
        "key": "DEEPSEEK_API_KEY",
        "pdf": False,
        "search": False,
        "context": 100000,
        "thinking": ["off", "auto"],
    },
    "gemini-2.5-flash": {
        "provider": "gemini",
        "key": "GEMINI_API_KEY",
        "pdf": True,
        "search": True,
        "context": 180000,
        "thinking": ["off", "auto"],
    },
    "gemini-2.5-pro": {
        "provider": "gemini",
        "key": "GEMINI_API_KEY",
        "pdf": True,
        "search": True,
        "context": 180000,
        "thinking": ["auto"],
    },
}


class Preferences(StrictModel):
    model: str = "deepseek-flash"
    max_output_tokens: int = Field(default=3000, ge=512, le=12000)
    thinking: Literal["off", "auto"] = "off"
    web_search: bool = False
    document_input: Literal["text", "pdf"] = "text"


def validate_preferences(value):
    p = Preferences.model_validate(value)
    cap = MODELS.get(p.model)
    if not cap:
        raise ValueError("unsupported_model")
    if p.thinking not in cap["thinking"]:
        raise ValueError("unsupported_thinking")
    if p.web_search and not cap["search"]:
        raise ValueError("unsupported_web_search")
    if p.document_input == "pdf" and not cap["pdf"]:
        raise ValueError("unsupported_pdf_input")
    return p.model_dump()


def preferences(store):
    if llm_client.managed():
        return llm_settings.effective(store)
    with store.db() as db:
        row = db.execute("SELECT value FROM reader_meta WHERE key='model_preferences'").fetchone()
    return validate_preferences(json.loads(row[0]) if row else {})


def save_preferences(store, value):
    if llm_client.managed():
        raise ValueError('use_platform_model_settings')
    result = validate_preferences(value)
    with store.db() as db:
        db.execute(
            "INSERT OR REPLACE INTO reader_meta VALUES('model_preferences',?)",
            (json.dumps(result),),
        )
    return result


def ledger(store, document):
    # Preserve the exact previous ledger identity, including consumed reservations.
    name = "reader_budget:" + document
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT value FROM reader_meta WHERE key=?", (name,)).fetchone()
        if row:
            return row[0]
        path = store.root / "permits" / (document + ".json")
        identity = (
            hashlib.sha256(
                json.dumps(json.loads(path.read_text()), sort_keys=True).encode()
            ).hexdigest()
            if path.exists()
            else name
        )
        db.execute("INSERT INTO reader_meta VALUES(?,?)", (name, identity))
    return identity


def status(store, document=None, session=""):
    try:
        p = llm_settings.effective(store, session=session) if llm_client.managed() else preferences(store)
    except ValueError as exc:
        p = {'model': '未配置', 'configured': False, 'error': str(exc)}
    row = None
    if document:
        key = ledger(store, document)
        with store.db() as db:
            row = db.execute("SELECT * FROM budget WHERE id=?", (key,)).fetchone()
    return {
        **p,
        "configured": p.get("configured", False) if llm_client.managed() else bool(os.getenv(MODELS[p["model"]]["key"])),
        "enabled": True,
        "remaining": max(0, 20 - (row["calls"] if row else 0)),
        "max_calls": 20,
        "reserved_input_bytes": row["input_bound"] if row else 0,
        "reserved_output_tokens": row["output_bound"] if row else 0,
        "input_limit": 3000000,
        "output_limit": 60000,
    }


def catalog():
    return [
        {
            "id": key,
            **{k: v for k, v in value.items() if k != "key"},
            "configured": bool(os.getenv(value["key"])),
            "availability": "not_verified",
        }
        for key, value in MODELS.items()
    ]
