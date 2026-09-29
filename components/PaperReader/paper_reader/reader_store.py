"""Reader state and transactionally exported interactions; evaluation data is separate."""

import json
import uuid
from datetime import UTC, datetime

from .store import Store


def stamp():
    return datetime.now(UTC).isoformat()


class ReaderStore(Store):
    def register_pdf(self, path):
        import hashlib
        from pathlib import Path
        path = Path(path)
        if path.stat().st_size > 30 * 1024 * 1024:
            raise ValueError("pdf_limit_30MiB")
        raw = path.read_bytes()
        if not raw.startswith(b"%PDF-"):
            raise ValueError("invalid_pdf_header")
        identity = hashlib.sha256(raw).hexdigest()
        target = self.root / "raw" / (identity + ".pdf")
        if target.exists() and target.read_bytes() != raw:
            raise ValueError("raw_integrity_error")
        if not target.exists():
            target.write_bytes(raw)
        with self.db() as db:
            exists = db.execute("SELECT 1 FROM documents WHERE id=?", (identity,)).fetchone()
        if exists:
            return self.document(identity)
        # Metadata only, never extract a second copy of the paper text.
        import io

        import pdfplumber
        parsed, state, error = None, "registered", None
        try:
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                parsed = {"version": "pdf-metadata-1", "blocks": [], "pages": [
                    {"page": n+1, "width": page.width, "height": page.height, "issues": []}
                    for n, page in enumerate(pdf.pages)]}
            parsed["parse_sha256"] = hashlib.sha256(json.dumps(parsed, sort_keys=True).encode()).hexdigest()
        except Exception:  # noqa: BLE001 -- malformed originals remain available
            state, error = "failed", "invalid_pdf_structure"
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?)", (identity, path.name, state,
                json.dumps(parsed) if parsed else None, error))
        return self.document(identity)

    def evidence_document(self, identity):
        from .paper_content import load
        content = load(self, identity)
        if content:
            return {"id": identity, "state": "ready", "parsed": content}
        return super().document(identity)

    def evidence_search(self, identity, query, limit=24):
        from .paper_content import load, search
        content = load(self, identity)
        return search(content, query, limit) if content else super().search(identity, query, limit)

    def document(self, identity):
        item = super().document(identity)
        from .paper_content import load
        content = load(self, identity)
        if content:
            item["content_version"] = content["parse_sha256"]
            item["content_ready"] = True
            item["state"] = "html" if item["state"] == "html" else "ready"
            # Keep historical PDF selection geometry for existing notes. It is not model evidence.
            if not item["parsed"]:
                item["parsed"] = content
                item["state"] = "html" if item["state"] == "html" else "ready"
        return item

    def job(self, identity):
        value = super().job(identity)
        # Read-time compatibility: recover old syntactically valid references without rewriting history.
        if not (value.get("answer") or {}).get("anchors") and (value.get("answer") or {}).get("markdown") and "[[" in value["answer"]["markdown"] and value.get("context"):
            from .reading_assistant import render_answer
            repaired = render_answer(value["answer"]["markdown"], value["context"])
            value["answer"] = {**value["answer"], **repaired}
        return value

    def set_meta(self, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO reader_meta(key,value) VALUES(?,?)", (key,value))

    def __init__(self, root):
        super().__init__(root)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reader_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS library_state(document TEXT PRIMARY KEY,pinned INTEGER NOT NULL DEFAULT 0,deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,document TEXT NOT NULL,
                  title TEXT NOT NULL,page INTEGER NOT NULL DEFAULT 1,zoom REAL NOT NULL DEFAULT 1,
                  updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS session_jobs(job TEXT PRIMARY KEY,session TEXT NOT NULL,
                  request_key TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,session TEXT NOT NULL,
                  document TEXT NOT NULL,parse_sha256 TEXT,selection TEXT,explanation TEXT,
                  body TEXT NOT NULL,revision INTEGER NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reader_records(id INTEGER PRIMARY KEY AUTOINCREMENT,
                  uid TEXT UNIQUE NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reader_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                  payload TEXT NOT NULL);
            """)
            for key in ("instance_id", "export_epoch", "csrf"):
                db.execute("INSERT OR IGNORE INTO reader_meta VALUES(?,?)", (key, uuid.uuid4().hex))

        with self.db() as db:
            rows = db.execute("SELECT uid,payload FROM reader_records").fetchall()
            for row in rows:
                item = json.loads(row["payload"])
                if item.get("source_channel") == "paper_ui":
                    item["updated_at"] = stamp()
                    self.publish(db, row["uid"], item)

    def meta(self, key):
        with self.db() as db:
            row = db.execute("SELECT value FROM reader_meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def documents(self):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute("SELECT id,name,state,error FROM documents ORDER BY rowid DESC")
            ]

    def session(self, identity):
        with self.db() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ValueError("session_not_found")
        return dict(row)

    def sessions(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY updated DESC")]

    def create_session(self, document):
        doc = self.document(document)
        identity = uuid.uuid4().hex
        with self.db() as db:
            db.execute(
                "INSERT INTO sessions(id,document,title,updated) VALUES(?,?,?,?)",
                (identity, document, doc["name"], stamp()),
            )
            db.execute("UPDATE library_state SET deleted_at=NULL WHERE document=?", (document,))
        return self.session(identity)

    def position(self, identity, page, zoom):
        session = self.session(identity)
        doc = self.document(session["document"])
        maximum = len(doc["parsed"]["pages"]) if doc["parsed"] else 100
        if not 1 <= page <= maximum or not 0.1 <= zoom <= 3:
            raise ValueError("invalid_reading_position")
        with self.db() as db:
            db.execute(
                "UPDATE sessions SET page=?,zoom=?,updated=? WHERE id=?",
                (page, zoom, stamp(), identity),
            )

    def enqueue_session(self, request, session_id, request_key):
        session = self.session(session_id)
        if session["document"] != request.document:
            raise ValueError("session_document_mismatch")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT job,session FROM session_jobs WHERE request_key=?", (request_key,)
            ).fetchone()
            if row:
                old = db.execute("SELECT request FROM jobs WHERE id=?", (row["job"],)).fetchone()[0]
                if row["session"] != session_id or json.loads(old) != request.model_dump():
                    raise ValueError("request_key_reused")
                return row["job"], False
            if request.previous:
                previous = db.execute(
                    "SELECT j.state,s.session FROM jobs j JOIN session_jobs s ON s.job=j.id WHERE j.id=?",
                    (request.previous,),
                ).fetchone()
                if (
                    not previous
                    or previous["state"] != "completed"
                    or previous["session"] != session_id
                ):
                    raise ValueError("invalid_previous_question")
            identity = uuid.uuid4().hex
            db.execute(
                "INSERT INTO jobs(id,request,state) VALUES(?,?,'queued')",
                (identity, request.model_dump_json()),
            )
            db.execute(
                "INSERT INTO session_jobs VALUES(?,?,?)", (identity, session_id, request_key)
            )
            db.execute("UPDATE sessions SET updated=? WHERE id=?", (stamp(), session_id))
            self.record_job_event(db, identity)
        return identity, True

    def session_history(self, identity):
        self.session(identity)
        with self.db() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT j.id FROM jobs j JOIN session_jobs s ON s.job=j.id WHERE s.session=? ORDER BY j.rowid",
                    (identity,),
                )
            ]
        return [self.job(i) for i in ids]

    def publish(self, db, uid, item):
        row = db.execute("SELECT id,revision FROM reader_records WHERE uid=?", (uid,)).fetchone()
        if row:
            number, revision = row["id"], row["revision"] + 1
        else:
            number = db.execute(
                "INSERT INTO reader_records(uid,revision,payload) VALUES(?,0,'{}')", (uid,)
            ).lastrowid
            revision = 1
        item.update(
            record_uid=uid,
            source_interaction_id=number,
            revision=revision,
            source_channel="product_ui",
        )
        encoded = json.dumps(item, ensure_ascii=False)
        db.execute(
            "UPDATE reader_records SET revision=?,payload=? WHERE id=?", (revision, encoded, number)
        )
        db.execute("INSERT INTO reader_events(payload) VALUES(?)", (encoded,))

    def record_job_event(self, db, identity):
        row = db.execute(
            "SELECT j.*,s.session FROM jobs j JOIN session_jobs s ON s.job=j.id WHERE j.id=?",
            (identity,),
        ).fetchone()
        if row is None:
            return
        req = json.loads(row["request"])
        answer = json.loads(row["answer"]) if row["answer"] else {}
        text = answer.get("markdown") or "\n\n".join(c["text"] for c in answer.get("claims", []))
        text += "\n" + "\n".join(answer.get("limitations", [])) if answer.get("limitations") else ""
        if row["error"]:
            text += "\n回答未完成：" + row["error"]
        self.publish(
            db,
            identity,
            {
                "conversation_id": row["session"],
                "created_at": row["created"],
                "updated_at": stamp(),
                "status": row["state"],
                "query": req["question"],
                "answer": text,
                "citations": answer.get("anchors", []),
                "document": req["document"],
                "selection": req.get("selection"),
                "kind": "question",
            },
        )

    def save_note(
        self,
        session_id,
        body,
        selection=None,
        explanation="",
        parse_sha256=None,
        identity=None,
        revision=None,
    ):
        session = self.session(session_id)
        doc = self.document(session["document"])
        actual = doc["parsed"]["parse_sha256"] if doc["parsed"] else None
        if parse_sha256 != actual:
            raise ValueError("document_version_changed")
        now = stamp()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if identity:
                row = db.execute(
                    "SELECT * FROM notes WHERE id=? AND document=?", (identity, session["document"])
                ).fetchone()
                if not row:
                    raise ValueError("note_not_found")
                if row["revision"] != revision:
                    raise ValueError("note_changed_reload")
                # Editing user prose never overwrites saved selection or explanation.
                db.execute(
                    "UPDATE notes SET body=?,revision=revision+1,updated=? WHERE id=?",
                    (body, now, identity),
                )
            else:
                identity = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO notes VALUES(?,?,?,?,?,?,?,1,?,?)",
                    (
                        identity,
                        session_id,
                        session["document"],
                        actual,
                        json.dumps(selection, ensure_ascii=False),
                        explanation,
                        body,
                        now,
                        now,
                    ),
                )
            row = dict(db.execute("SELECT * FROM notes WHERE id=?", (identity,)).fetchone())
            self.publish(
                db,
                identity,
                {
                    "conversation_id": session_id,
                    "created_at": row["created"],
                    "updated_at": now,
                    "status": "saved",
                    "query": "阅读笔记",
                    "answer": row["body"],
                    "citations": [],
                    "kind": "note",
                    "document": row["document"],
                    "selection": json.loads(row["selection"]),
                    "explanation": row["explanation"],
                    "parse_sha256": row["parse_sha256"],
                },
            )
        return identity

    def notes(self, session_id):
        session = self.session(session_id)
        with self.db() as db:
            result = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM notes WHERE document=? ORDER BY created DESC",
                    (session["document"],),
                )
            ]
        for row in result:
            row["selection"] = json.loads(row["selection"])
        return result

    def changes(self, cursor=None, limit=50):
        if not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        instance, epoch = self.meta("instance_id"), self.meta("export_epoch")
        offset = 0
        if cursor:
            try:
                old_instance, old_epoch, value = cursor.split(":")
                offset = int(value)
                if old_instance != instance or old_epoch != epoch or offset < 0:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise ValueError("cursor_expired") from exc
        with self.db() as db:
            maximum = db.execute("SELECT coalesce(max(id),0) FROM reader_events").fetchone()[0]
            if offset > maximum:
                raise ValueError("cursor_expired")
            rows = db.execute(
                "SELECT * FROM reader_events WHERE id>? ORDER BY id LIMIT ?", (offset, limit)
            ).fetchall()
        events = []
        for row in rows:
            item = json.loads(row["payload"])
            events.append(
                {
                    "event_id": row["id"],
                    "revision": item["revision"],
                    "operation": "upsert",
                    "interaction": item,
                }
            )
        last = rows[-1]["id"] if rows else offset
        return {
            "contract_version": "1.0",
            "instance_id": instance,
            "export_epoch": epoch,
            "events": events,
            "next_cursor": f"{instance}:{epoch}:{last}",
            "has_more": last < maximum,
        }
