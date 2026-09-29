import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .parse import parse_pdf


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "raw").mkdir(exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents(
                  id TEXT PRIMARY KEY, name TEXT, state TEXT, parsed TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS jobs(
                  id TEXT PRIMARY KEY, request TEXT, state TEXT, context TEXT,
                  answer TEXT, error TEXT, usage TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE VIRTUAL TABLE IF NOT EXISTS block_search USING fts5(
                  document UNINDEXED, block_id UNINDEXED, text, section);
                CREATE TABLE IF NOT EXISTS budget(
                  id TEXT PRIMARY KEY, calls INTEGER, input_bound INTEGER, output_bound INTEGER);
            """)

    @contextmanager
    def db(self):
        with sqlite3.connect(self.root / "paper.db", timeout=15) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            yield db

    def import_pdf(self, path):
        path = Path(path)
        if path.stat().st_size > 30 * 1024 * 1024:
            raise ValueError("pdf_limit_30MiB")
        content = path.read_bytes()
        if not content.startswith(b"%PDF-"):
            raise ValueError("invalid_pdf_header")
        identity = hashlib.sha256(content).hexdigest()
        with self.db() as db:
            if db.execute("SELECT 1 FROM documents WHERE id=?", (identity,)).fetchone():
                return self.document(identity)
        target = self.root / "raw" / (identity + ".pdf")
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != identity:
                raise ValueError("raw_integrity_error")
        else:
            with target.open("xb") as f:
                f.write(content)
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO documents VALUES(?,?, 'parsing',NULL,NULL)",
                (identity, path.name),
            )
        try:
            parsed = parse_pdf(target)
            # Parse artifact identity is immutable and includes the extractor version.
            parsed["parse_sha256"] = hashlib.sha256(
                json.dumps(parsed, sort_keys=True).encode()
            ).hexdigest()
            with self.db() as db:
                db.execute(
                    "UPDATE documents SET state='ready',parsed=? WHERE id=?",
                    (json.dumps(parsed, ensure_ascii=False), identity),
                )
                db.execute("DELETE FROM block_search WHERE document=?", (identity,))
                db.executemany(
                    "INSERT INTO block_search VALUES(?,?,?,?)",
                    [(identity, b["id"], b["text"], b["section"]) for b in parsed["blocks"]],
                )
        except Exception as exc:  # noqa: BLE001 -- preserve failed import and its original
            with self.db() as db:
                db.execute(
                    "UPDATE documents SET state='failed',error=? WHERE id=?",
                    (type(exc).__name__, identity),
                )
        return self.document(identity)

    def document(self, identity):
        with self.db() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ValueError("document_not_found")
        item = dict(row)
        item["parsed"] = json.loads(item["parsed"]) if item["parsed"] else None
        return item

    def search(self, identity, query, limit=24):
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,40}", query)
        if not words:
            return []
        expression = " OR ".join('"' + w + '"' for w in dict.fromkeys(words))
        with self.db() as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT block_id FROM block_search WHERE block_search MATCH ? "
                    "AND document=? ORDER BY rank LIMIT ?",
                    (expression, identity, limit),
                )
            ]

    def enqueue(self, request):
        self.document(request.document)
        if request.previous:
            previous = self.job(request.previous)
            if (
                previous["request"]["document"] != request.document
                or previous["state"] != "completed"
            ):
                raise ValueError("invalid_previous_question")
        identity = uuid.uuid4().hex
        with self.db() as db:
            db.execute(
                "INSERT INTO jobs(id,request,state) VALUES(?,?,'queued')",
                (identity, request.model_dump_json()),
            )
        return identity

    def job(self, identity):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ValueError("job_not_found")
        result = dict(row)
        for field in ("request", "context", "answer", "usage"):
            if result[field]:
                result[field] = json.loads(result[field])
        return result

    def set_job(self, identity, state, **values):
        if not set(values) <= {"context", "answer", "error", "usage"}:
            raise ValueError("invalid_job_field")
        fields = ["state=?"] + [f"{key}=?" for key in values]
        args = (
            [state]
            + [
                json.dumps(v, ensure_ascii=False) if key != "error" else v
                for key, v in values.items()
            ]
            + [identity]
        )
        with self.db() as db:
            db.execute(f"UPDATE jobs SET {','.join(fields)} WHERE id=?", args)
            self.record_job_event(db, identity)

    def record_job_event(self, db, identity):
        """Optional product history hook, committed in the same transaction."""

    def claim(self, identity):
        with self.db() as db:
            changed = db.execute(
                "UPDATE jobs SET state='running' WHERE id=? AND state='queued'", (identity,)
            ).rowcount
        if not changed:
            raise ValueError("job_not_queued")

    def recover(self):
        # Caller holds the single-worker lock; never silently retries a paid request.
        with self.db() as db:
            interrupted = [r[0] for r in db.execute("SELECT id FROM jobs WHERE state='running'")]
            jobs = db.execute(
                "UPDATE jobs SET state='interrupted',error='worker_interrupted' "
                "WHERE state='running'"
            ).rowcount
            db.execute(
                "UPDATE documents SET state='failed',error='parse_interrupted' "
                "WHERE state='parsing'"
            )
            for identity in interrupted:
                self.record_job_event(db, identity)
        return jobs
