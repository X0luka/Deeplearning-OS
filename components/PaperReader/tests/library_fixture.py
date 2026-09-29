"""Ephemeral local UI fixture: real note persistence, synthetic papers, no model routes."""
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
for key in list(os.environ):
    if key.startswith("AOS_"):
        del os.environ[key]

import uvicorn
from conftest import tiny_pdf

from paper_reader.store import Store
from paper_reader.web import create_app

with tempfile.TemporaryDirectory(prefix="reader-notes-ui-") as folder:
    data = Path(folder)
    app = create_app(data / "reader")
    store = app.state.store
    for i in range(2):
        doc = Store.import_pdf(store, tiny_pdf(data / f"fixture-{i}.pdf", [
            [(50, 700, f"Selected evidence from synthetic paper {i}."),
             (50, 670, "Another paragraph for note validation.")]
        ]))
        store.create_session(doc["id"])
        if i == 0:
            store.create_session(doc["id"])
    # No endpoints can generate or import external content in this test process.
    app.router.routes = [route for route in app.router.routes if not (
        (getattr(route, "methods", set()) & {"POST", "PUT", "DELETE"}
         and not (getattr(route, "path", "").startswith("/api/notes")
                  or getattr(route, "path", "").endswith("/position")
                  or getattr(route, "path", "").startswith("/api/library/")))
        or getattr(route, "path", "") == "/api/v1/appearance"
    )]

    @app.get("/api/v1/appearance")
    def appearance():
        return {"mode": "light", "resolved": "light"}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    print(f"FIXTURE=http://127.0.0.1:{sock.getsockname()[1]}", flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[sock])
