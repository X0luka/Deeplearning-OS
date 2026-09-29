"""Read-only, paged projection of archive and platform assistant records."""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager


class Center:
    def __init__(self, archive):
        self.archive = archive
        self.assistant = archive.path.parent / "assistant" / "assistant.db"
        self.map_path = archive.path.parent / "data-center" / "map.json"

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.archive.path.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            attached = self.assistant.exists()
            if attached:
                db.execute("ATTACH DATABASE ? AS assistant", (
                    self.assistant.resolve().as_uri() + "?mode=ro",))
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            yield db, attached
        finally:
            db.close()

    def cte(self, attached):
        # Stable import identity is provider:instance:record_uid, never matching question text.
        joins = """LEFT JOIN assistant.turns imported
            ON imported.legacy=r.product||':'||r.instance||':'||r.uid
            LEFT JOIN assistant.conversations imported_c ON imported_c.id=imported.conversation
        """ if attached else ""
        exclude = """AND NOT EXISTS (SELECT 1 FROM assistant.deleted_conversations d
            WHERE d.id=imported.conversation)""" if attached else ""
        identity = "coalesce('t-'||imported.id,'a-'||r.id)" if attached else "'a-'||r.id"
        thread = ("coalesce(imported_c.id,r.product||':'||r.instance||':'||coalesce(r.thread,''))"
                  if attached else "r.product||':'||r.instance||':'||coalesce(r.thread,'')")
        assistant = """ UNION ALL SELECT 't-'||t.id, 'assistant', t.id,
            coalesce(nullif(c.model_scope,'assistant'),'assistant'),
            c.id, c.title, t.question, t.answer,
            t.state, t.created, t.updated, 'question', t.legacy,
            coalesce(c.legacy,'')||' '||t.question
            FROM assistant.turns t JOIN assistant.conversations c ON c.id=t.conversation
            WHERE NOT EXISTS (SELECT 1 FROM assistant.deleted_conversations d WHERE d.id=c.id)
            AND NOT EXISTS (SELECT 1 FROM records r
                WHERE r.product||':'||r.instance||':'||r.uid=t.legacy
                AND r.source IN ('product_ui','legacy_unknown'))
        """ if attached else ""
        return """WITH items(key,store,local_id,product,thread,title,query,answer,status,
            created,updated,kind,legacy,marker) AS (
            SELECT """ + identity + ", 'archive', r.id, r.product," + thread + """,
            json_extract(r.payload,'$.query'),
            coalesce(json_extract(r.payload,'$.query'),''),
            coalesce(json_extract(r.payload,'$.answer'),''),
            json_extract(r.payload,'$.status'), json_extract(r.payload,'$.created_at'),
            json_extract(r.payload,'$.updated_at'),
            coalesce(json_extract(r.payload,'$.kind'),'question'),
            r.product||':'||r.instance||':'||r.uid,
            coalesce(r.thread,'')||' '||coalesce(json_extract(r.payload,'$.query'),'')
            FROM records r """ + joins + "WHERE r.source IN ('product_ui','legacy_unknown') " + exclude + assistant + ") "

    @staticmethod
    def filters(q="", kind="all", thread="", marked=True, keys=None):
        where, args = ["1=1"], []
        if q:
            where.append("(instr(lower(query),lower(?))>0 OR instr(lower(answer),lower(?))>0)")
            args += [q, q]
        if kind != "all":
            where.append("kind=?")
            args.append(kind)
        if thread:
            where.append("thread=?")
            args.append(thread)
        if not marked:
            where.append("marker NOT LIKE '%eval%' AND marker NOT LIKE '%dw006%' "
                         "AND marker NOT LIKE '%已有真实回答回放%'")
        if keys is not None:
            where.append("key IN (" + ",".join("?" for _ in keys) + ")" if keys else "0=1")
            args += keys
        return " AND ".join(where), args

    def items(self, page=1, q="", kind="all", thread="", marked=True, keys=None):
        where, args = self.filters(q, kind, thread, marked, keys)
        with self.connection() as (db, attached):
            cte = self.cte(attached)
            total = db.execute(cte + "SELECT count(*) FROM items WHERE " + where, args).fetchone()[0]
            rows = db.execute(cte + """SELECT key,store,product,thread,substr(title,1,180) title,
                substr(query,1,220) query,substr(answer,1,180) preview,status,created,updated,kind,
                (marker LIKE '%eval%' OR marker LIKE '%dw006%' OR
                 marker LIKE '%已有真实回答回放%') marked FROM items WHERE """ + where +
                " ORDER BY julianday(updated) DESC,key DESC LIMIT 20 OFFSET ?",
                [*args, (page-1)*20]).fetchall()
            return {"total": total, "page": page, "items": [dict(r) for r in rows]}

    def resolve(self, db, key, attached):
        if not re.fullmatch(r"(?:a-[0-9]+|t-[a-f0-9]{32})", key):
            return None
        if attached and key.startswith("a-"):
            imported = db.execute("""SELECT t.id FROM records r JOIN assistant.turns t
                ON t.legacy=r.product||':'||r.instance||':'||r.uid WHERE r.id=?""",
                (key[2:],)).fetchone()
            if imported:
                return "t-" + imported[0]
        return key

    def detail(self, key):
        with self.connection() as (db, attached):
            key = self.resolve(db, key, attached)
            if not key:
                return None
            row = db.execute(self.cte(attached) + "SELECT * FROM items WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            if item["store"] == "archive":
                raw = db.execute("SELECT payload FROM records WHERE id=?", (item["local_id"],)).fetchone()
                payload = json.loads(raw[0])
                item.update(selection=payload.get("selection"), citations=payload.get("citations", []),
                            material=payload.get("document") or payload.get("version_id"),
                            explanation=payload.get("explanation"),
                            architecture_context=payload.get("architecture_context"))
                item["source_url"] = "/data/" + str(item["local_id"])
            else:
                raw = db.execute("SELECT evidence,snapshot,conversation FROM assistant.turns WHERE id=?",
                                 (item["local_id"],)).fetchone()
                evidence = json.loads(raw[0] or "[]")
                item.update(citations=evidence, selection=None, material=None,
                            explanation=None, conversation=raw[2])
                # A duplicated legacy note is still read from its authoritative archived payload.
                old = db.execute("SELECT id,payload FROM records WHERE product||':'||instance||':'||uid=?",
                                 (item["legacy"],)).fetchone()
                if old:
                    p = json.loads(old[1])
                    item.update(selection=p.get("selection"), explanation=p.get("explanation"),
                                material=p.get("document") or p.get("version_id"))
                item["source_url"] = "/data/item/" + key
            item["fingerprint"] = self.fingerprint(item)
            return item

    @staticmethod
    def fingerprint(item):
        return hashlib.sha256(json.dumps([item["query"], item["answer"]],
                                         ensure_ascii=False).encode()).hexdigest()

    def snapshot_keys(self):
        with self.connection() as (db, attached):
            return [dict(r) for r in db.execute(self.cte(attached) +
                    "SELECT key,updated,thread,status,kind FROM items ORDER BY key")]

    def map(self):
        inventory = self.snapshot_keys()
        current = {r["key"]: r for r in inventory}
        data = json.loads(self.map_path.read_text()) if self.map_path.exists() else {
            "generated": None, "topics": [], "records": {}, "method": "尚未生成认知地图"}
        saved = data.get("records", {})
        topics = []
        for t in data["topics"]:
            keys = [k for k in t["keys"] if k in current]
            topics.append({**t, "keys": keys, "count": len(keys)})
        records = {k: v for k, v in saved.items() if k in current}
        return {**data, "topics": topics, "records": records,
                "total": len(current), "mapped": len(records),
                "pending": [k for k in current if k not in records],
                "changed": [k for k in records if records[k].get("updated") != current[k]["updated"]]}

    def status(self):
        with self.connection() as (db, _attached):
            old = [dict(row) for row in db.execute("""
                WITH ids AS (SELECT product FROM records
                    UNION SELECT product FROM checkpoints UNION SELECT product FROM jobs)
                SELECT i.product, c.observed_at, j.state sync_state, j.error
                FROM ids i LEFT JOIN checkpoints c ON c.product=i.product
                LEFT JOIN jobs j ON j.id=(SELECT max(id) FROM jobs WHERE product=i.product)
                ORDER BY i.product
            """)]
        return {"mode": "manual", "sources": old,
                "assistant_available": self.assistant.exists(),
                "bytes": {p.name: p.stat().st_size for p in [self.archive.path, self.assistant]
                          if p.exists()}, "scope": "新旧问答与已归档笔记；不代表全部原件和资料"}
