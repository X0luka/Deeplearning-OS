"""SQLite transactions and immutable version artifacts."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("raw", "wiki", "notes"):
            (self.root / name).mkdir(exist_ok=True)
        with self.db() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS repos(id TEXT PRIMARY KEY,url TEXT UNIQUE,ref TEXT,current TEXT);
            CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY,repo TEXT,sha TEXT,mode TEXT,
                created TEXT,manifest TEXT,pages TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT,args TEXT,state TEXT,
                created TEXT,result TEXT,error TEXT,cancel INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS interactions(id INTEGER PRIMARY KEY,uid TEXT UNIQUE,
                payload TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,payload TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(version UNINDEXED,path UNINDEXED,
                start UNINDEXED,stop UNINDEXED,text,tokenize='unicode61');
            """)
            for key in ("instance", "epoch"):
                c.execute("INSERT OR IGNORE INTO meta VALUES(?,?)", (key, uid()))

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.root / "wiki.db", timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def rows(self, sql, args=()):
        with self.db() as c:
            return [dict(x) for x in c.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def meta(self, key):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    def set_meta(self, key, value):
        with self.db() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))

    def repo(self, identity):
        return self.one("SELECT * FROM repos WHERE id=?", (identity,))

    def version(self, identity):
        v = self.one("SELECT * FROM versions WHERE id=?", (identity,))
        if v:
            for key in ("manifest", "pages"):
                v[key] = json.loads(v[key])
        return v

    def enqueue(self, kind, args):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            repo_id = args.get("repo")
            if not repo_id and args.get("version"):
                v = c.execute("SELECT repo FROM versions WHERE id=?", (args["version"],)).fetchone()
                repo_id = v[0] if v else None
            lock = c.execute("SELECT value FROM meta WHERE key=?", ("deletion:"+str(repo_id),)).fetchone()
            if lock and kind != "delete_repo":
                raise ValueError("repo_deleting")
            encoded = json.dumps(args, sort_keys=True)
            if kind == "chat" and args.get("request_key"):
                previous = c.execute(
                    "SELECT id,args FROM jobs WHERE kind='chat' AND json_extract(args,'$.request_key')=?",
                    (args["request_key"],),
                ).fetchone()
                if previous:
                    if previous[1] != encoded:
                        raise ValueError("request_key_conflict")
                    return previous[0]
            old = c.execute(
                "SELECT id FROM jobs WHERE kind=? AND args=? AND state IN ('queued','running')",
                (kind, encoded),
            ).fetchone()
            if old:
                return old[0]
            identity = uid()
            c.execute(
                "INSERT INTO jobs(id,kind,args,state,created) VALUES(?,?,?,'queued',?)",
                (identity, kind, encoded, now()),
            )
            return identity

    def job(self, identity):
        row = self.one("SELECT * FROM jobs WHERE id=?", (identity,))
        if row:
            row["args"] = json.loads(row["args"])
            row["failure"] = json.loads(self.meta("failure:"+identity) or "null")
            if row["error"] and not row["failure"]:
                from .rules import failure
                row["failure"] = failure(row["error"], stage=row["kind"])
            row["execution"] = json.loads(self.meta("execution:"+identity) or "{}")
            row["result"] = json.loads(row["result"]) if row["result"] else None
        return row

    def recover(self):
        with self.db() as c:
            c.execute("UPDATE jobs SET state='interrupted',error='process_restarted' WHERE state='running'")

    def claim(self):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                c.execute("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
                return dict(row)

    def finish(self, identity, state, result=None, error=None):
        with self.db() as c:
            c.execute(
                "UPDATE jobs SET state=?,result=?,error=? WHERE id=?",
                (state, json.dumps(result, ensure_ascii=False), error, identity),
            )

    def cancelled(self, identity):
        row = self.one("SELECT cancel FROM jobs WHERE id=?", (identity,))
        return bool(row and row["cancel"])

    def publish(self, identity, repo, sha, mode, manifest, pages, chunks):
        # Artifacts are complete before current pointer and search index become visible.
        out = self.root / "wiki" / identity
        out.mkdir(exist_ok=False)
        for page in pages:
            (out / (page["slug"] + ".md")).write_text(page["markdown"])
        (out / "index.md").write_text("\n".join(f"- [{p['title']}]({p['slug']}.md)" for p in pages) + "\n")
        with self.db() as c:
            c.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?)",
                (
                    identity,
                    repo,
                    sha,
                    mode,
                    now(),
                    json.dumps(manifest, ensure_ascii=False),
                    json.dumps(pages, ensure_ascii=False),
                ),
            )
            from itertools import islice
            iterator = iter(chunks)
            while batch := list(islice(iterator, 200)):
                c.executemany("INSERT INTO chunks VALUES(?,?,?,?,?)", batch)
            c.execute("UPDATE repos SET current=? WHERE id=?", (identity, repo))
            # A failed log write must roll back the current-version pointer.
            # "prepare" is an action receipt; the DB is authoritative for publication.
            with (self.root / "wiki" / "log.md").open("a") as log:
                log.write(f"\n## {now()} prepare | {identity}\ncommit {sha}; mode {mode}\n")

    def record(
        self, question, answer, citations, version, conversation, mode, status="completed", context=None
    ):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            identity = c.execute("INSERT INTO interactions(uid,payload) VALUES(?,'{}')", (uid(),)).lastrowid
            record_uid = c.execute("SELECT uid FROM interactions WHERE id=?", (identity,)).fetchone()[0]
            item = {
                "record_uid": record_uid,
                "source_interaction_id": identity,
                "revision": 1,
                "source_channel": "product_ui",
                "conversation_id": conversation,
                "created_at": now(),
                "updated_at": now(),
                "status": status,
                "query": question,
                "answer": answer,
                "citations": citations,
                "version_id": version,
                "model": mode,
            }
            if context:
                item["architecture_context"] = context
            encoded = json.dumps(item, ensure_ascii=False)
            c.execute("UPDATE interactions SET payload=? WHERE id=?", (encoded, identity))
            c.execute("INSERT INTO events(payload) VALUES(?)", (encoded,))
        return item

    def changes(self, cursor=None, limit=100):
        epoch = self.meta("epoch")
        position = 0
        if cursor:
            parts = cursor.split(":")
            if len(parts) != 3 or parts[0] != self.meta("instance") or parts[1] != epoch:
                raise ValueError("cursor_expired")
            try:
                position = int(parts[2])
                if position < 0:
                    raise ValueError()
            except ValueError:
                raise ValueError("cursor_expired") from None
        rows = self.rows("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (position, limit + 1))
        events = [
            {
                "event_id": r["id"],
                "revision": 1,
                "operation": "upsert",
                "interaction": json.loads(r["payload"]),
            }
            for r in rows[:limit]
        ]
        end = events[-1]["event_id"] if events else position
        return {
            "contract_version": "1.0",
            "instance_id": self.meta("instance"),
            "export_epoch": epoch,
            "events": events,
            "next_cursor": f"{self.meta('instance')}:{epoch}:{end}",
            "has_more": len(rows) > limit,
        }
