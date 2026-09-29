import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime


def now():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, title TEXT, section TEXT,
              resources TEXT, scope TEXT, settings TEXT, model_scope TEXT, updated TEXT, legacy TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY, conversation TEXT, request_key TEXT UNIQUE,
              question TEXT, answer TEXT, state TEXT, error TEXT, snapshot TEXT, evidence TEXT,
              usage TEXT, created TEXT, updated TEXT, legacy TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, turn TEXT, kind TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS turn_evidence(turn TEXT, id TEXT, payload TEXT, PRIMARY KEY(turn,id));
            CREATE TABLE IF NOT EXISTS deleted_conversations(id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS diagnostics(id INTEGER PRIMARY KEY, created TEXT, payload TEXT);
            """)
            db.execute(
                "UPDATE turns SET state='interrupted',error='service_restarted' WHERE state IN ('queued','running')"
            )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.db() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def conversation(self, identity):
        rows = self.rows("SELECT * FROM conversations WHERE id=? AND id NOT IN (SELECT id FROM deleted_conversations)", (identity,))
        if not rows:
            raise ValueError("conversation_not_found")
        row = rows[0]
        for k in ("resources", "settings"):
            row[k] = json.loads(row[k])
        return row

    def delete_conversation(self, identity):
        # Retain usage and evidence for audit; never delete source material.
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM conversations WHERE id=?", (identity,)).fetchone():
                raise ValueError("conversation_not_found")
            if db.execute("SELECT 1 FROM turns WHERE conversation=? AND state IN ('queued','running')", (identity,)).fetchone():
                raise ValueError("conversation_busy")
            db.execute("INSERT OR IGNORE INTO deleted_conversations VALUES(?,?)", (identity, now()))

    def create(
        self, title, section, resources, settings=None, model_scope="assistant", legacy=None
    ):
        identity = uuid.uuid4().hex
        with self.db() as db:
            if legacy:
                row = db.execute(
                    "SELECT id FROM conversations WHERE legacy=?", (legacy,)
                ).fetchone()
                if row:
                    return self.conversation(row["id"])
            db.execute(
                "INSERT INTO conversations VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    title[:180],
                    section,
                    json.dumps(resources),
                    "current",
                    json.dumps(settings or {}),
                    model_scope,
                    now(),
                    legacy,
                ),
            )
        return self.conversation(identity)

    def turn(self, identity):
        rows = self.rows("SELECT * FROM turns WHERE id=?", (identity,))
        if not rows:
            raise ValueError("turn_not_found")
        row = rows[0]
        for k in ("snapshot", "evidence", "usage"):
            row[k] = json.loads(row[k])
        return row

    def turns(self, conversation):
        return [
            self.turn(r["id"])
            for r in self.rows(
                "SELECT id FROM turns WHERE conversation=? ORDER BY created,id", (conversation,)
            )
        ]

    def enqueue(self, conversation, key, question, snapshot):
        identity = uuid.uuid4().hex
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM conversations WHERE id=? AND id NOT IN (SELECT id FROM deleted_conversations)", (conversation,)).fetchone():
                raise ValueError("conversation_not_found")
            old = db.execute(
                "SELECT id,conversation,question FROM turns WHERE request_key=?", (key,)
            ).fetchone()
            if old:
                if old["conversation"] != conversation or old["question"] != question:
                    raise ValueError("request_key_reused")
                return old["id"], False
            if db.execute(
                "SELECT 1 FROM turns WHERE conversation=? AND state IN ('queued','running')",
                (conversation,),
            ).fetchone():
                raise ValueError("conversation_busy")
            if not db.execute(
                "SELECT 1 FROM turns WHERE conversation=?", (conversation,)
            ).fetchone():
                db.execute(
                    "UPDATE conversations SET title=? WHERE id=?", (question[:70], conversation)
                )
            db.execute(
                "INSERT INTO turns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    conversation,
                    key,
                    question,
                    "",
                    "queued",
                    None,
                    json.dumps(snapshot),
                    "[]",
                    "{}",
                    now(),
                    now(),
                    None,
                ),
            )
        return identity, True

    def save_evidence(self, turn, evidence):
        with self.db() as db:
            db.executemany("INSERT OR REPLACE INTO turn_evidence VALUES(?,?,?)",
                           [(turn, e["id"], json.dumps(e, ensure_ascii=False)) for e in evidence])

    def update(self, identity, **values):
        allowed = {"answer", "state", "error", "evidence", "usage"}
        if not values.keys() <= allowed:
            raise ValueError("invalid_turn_update")
        encoded = {
            k: json.dumps(v, ensure_ascii=False) if k in ("evidence", "usage") else v
            for k, v in values.items()
        }
        encoded["updated"] = now()
        with self.db() as db:
            db.execute(
                "UPDATE turns SET " + ",".join(k + "=?" for k in encoded) + " WHERE id=?",
                (*encoded.values(), identity),
            )
            db.execute(
                "UPDATE conversations SET updated=? WHERE id=(SELECT conversation FROM turns WHERE id=?)",
                (now(), identity),
            )
            db.execute(
                "INSERT INTO events(turn,kind,payload) VALUES(?,?,?)",
                (
                    identity,
                    "update",
                    json.dumps(
                        {k: v for k, v in values.items() if k not in ("answer", "evidence")},
                        ensure_ascii=False,
                    ),
                ),
            )

    def import_turn(self, conversation, item, legacy):
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO turns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    conversation,
                    "legacy:" + legacy,
                    item["question"],
                    item.get("answer", ""),
                    item.get("state", "completed")
                    if item.get("state") not in ("queued", "running")
                    else "interrupted",
                    item.get("error"),
                    json.dumps({"legacy": True}),
                    json.dumps(item.get("evidence", [])),
                    json.dumps(item.get("usage", {})),
                    item.get("created") or now(),
                    now(),
                    legacy,
                ),
            )
