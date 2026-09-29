"""Explicit opt-in, bounded, one-attempt provider gateway."""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class Gateway:
    def __init__(self, store, permit_path):
        self.store = store
        self.permit = json.loads(Path(permit_path).read_text())
        required = {
            "authorization",
            "base_url",
            "model",
            "key_env",
            "documents",
            "max_calls",
            "max_input_bytes",
            "max_output_tokens",
            "output_per_call",
        }
        if not required <= self.permit.keys() or self.permit["authorization"] != "user_approved":
            raise ValueError("explicit_model_authorization_required")
        if urlsplit(self.permit["base_url"]).scheme != "https":
            raise ValueError("provider_requires_https")
        for key in ("max_calls", "max_input_bytes", "max_output_tokens", "output_per_call"):
            if not isinstance(self.permit[key], int) or self.permit[key] <= 0:
                raise ValueError("invalid_budget")
        self.identity = hashlib.sha256(json.dumps(self.permit, sort_keys=True).encode()).hexdigest()

    def complete(self, document, messages):
        if document not in self.permit["documents"]:
            raise ValueError("document_not_authorized")
        key = os.getenv(self.permit["key_env"])
        if not key:
            raise ValueError("model_key_not_configured")
        # Reserve before network I/O, never refund on timeout/unknown provider outcome.
        input_bound = len(json.dumps(messages, ensure_ascii=False).encode()) + 4096
        output_bound = self.permit["output_per_call"]
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO budget VALUES(?,0,0,0)", (self.identity,))
            row = db.execute("SELECT * FROM budget WHERE id=?", (self.identity,)).fetchone()
            if (
                row["calls"] + 1 > self.permit["max_calls"]
                or row["input_bound"] + input_bound > self.permit["max_input_bytes"]
                or row["output_bound"] + output_bound > self.permit["max_output_tokens"]
            ):
                raise ValueError("model_budget_exhausted")
            db.execute(
                "UPDATE budget SET calls=calls+1,input_bound=input_bound+?,"
                "output_bound=output_bound+? WHERE id=?",
                (input_bound, output_bound, self.identity),
            )
        payload = {
            "model": self.permit["model"],
            "messages": messages,
            "max_tokens": output_bound,
            "response_format": {"type": "json_object"},
        }
        if self.permit.get("thinking_disabled"):
            payload["thinking"] = {"type": "disabled"}
        with httpx.Client(
            timeout=httpx.Timeout(120, connect=15),
            trust_env=False,
            proxy=self.permit.get("proxy"),
            follow_redirects=False,
        ) as client:
            response = client.post(
                self.permit["base_url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        choice = body["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("model_incomplete_output")
        return json.loads(choice["message"]["content"]), body.get("usage", {})
