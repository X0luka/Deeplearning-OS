"""Transactional archive; each operation owns and closes its SQLite connection."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from aos.domain import ProductError


class SQLiteArchive:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS product_activity (
                    product TEXT PRIMARY KEY, state TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY, product TEXT, instance TEXT, uid TEXT,
                    epoch TEXT, revision INTEGER, source TEXT, thread TEXT, payload TEXT,
                    rollback INTEGER DEFAULT 0, UNIQUE(product,instance,uid));
                CREATE TABLE IF NOT EXISTS events (
                    product TEXT, instance TEXT, epoch TEXT, event_id INTEGER, payload TEXT,
                    PRIMARY KEY(product,instance,epoch,event_id));
                CREATE TABLE IF NOT EXISTS checkpoints (
                    product TEXT PRIMARY KEY, cursor TEXT, observed_at TEXT);
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, product TEXT, state TEXT, created_at TEXT,
                    finished_at TEXT, error TEXT, imported INTEGER DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS one_sync ON jobs(product)
                    WHERE state IN ('queued','running');
            """)

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def register_manual(self, product):
        with self.connection() as c:
            c.execute("INSERT OR IGNORE INTO product_activity VALUES(?,'disabled',0)", (product,))

    def activity(self, product):
        with self.connection() as c:
            row = c.execute("SELECT state,generation FROM product_activity WHERE product=?",
                            (product,)).fetchone()
            return dict(row) if row else {"state": "ready", "generation": None}

    def set_activity(self, product, state):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE product_activity SET state=?,generation=generation+1 WHERE product=?",
                      (state, product))
            if state != "ready":
                c.execute("UPDATE jobs SET state='cancelled',error='product_disabled',finished_at=? "
                          "WHERE product=? AND state='queued'",
                          (datetime.now(UTC).isoformat(), product))

    def _require_active(self, c, product, generation=None):
        row = c.execute("SELECT state,generation FROM product_activity WHERE product=?",
                        (product,)).fetchone()
        if row and (row['state'] != 'ready' or
                    (generation is not None and generation != row['generation'])):
            raise ProductError("product_disabled")

    def checkpoint(self, product: str) -> str | None:
        with self.connection() as c:
            row = c.execute("SELECT cursor FROM checkpoints WHERE product=?", (product,)).fetchone()
            return row[0] if row else None

    def apply(self, product: str, batch: dict, expected: str | None, generation=None) -> int:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            self._require_active(c, product, generation)
            row = c.execute("SELECT cursor FROM checkpoints WHERE product=?", (product,)).fetchone()
            if (row[0] if row else None) != expected:
                raise ValueError("checkpoint_conflict")
            count = 0
            for event in batch["events"]:
                count += self._event(c, product, batch, event)
            c.execute("INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)",
                      (product, batch["next_cursor"], datetime.now(UTC).isoformat()))
            return count

    def _event(self, c, product: str, batch: dict, event: dict) -> int:
        instance, epoch = batch["instance_id"], batch["export_epoch"]
        encoded = json.dumps(event["interaction"], ensure_ascii=False)
        inserted = c.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)",
                             (product, instance, epoch, event["event_id"], encoded)).rowcount
        if not inserted:
            return 0
        item = event["interaction"]
        key = (product, instance, item["record_uid"])
        old = c.execute("SELECT * FROM records WHERE product=? AND instance=? AND uid=?", key).fetchone()
        if old and (item["revision"] < old["revision"] or (epoch != old["epoch"]
                    and item["updated_at"] < json.loads(old["payload"])["updated_at"])):
            if epoch != old["epoch"]:
                c.execute("UPDATE records SET rollback=1 WHERE id=?", (old["id"],))
            return 1
        c.execute("""INSERT INTO records(product,instance,uid,epoch,revision,source,thread,payload)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(product,instance,uid) DO UPDATE SET
            epoch=excluded.epoch,revision=excluded.revision,source=excluded.source,
            thread=excluded.thread,payload=excluded.payload,rollback=0""",
            (*key, epoch, item["revision"], item["source_channel"], item.get("conversation_id"), encoded))
        return 1

    def product_ids(self):
        with self.connection() as c:
            return [row[0] for row in c.execute("SELECT DISTINCT product FROM records ORDER BY product")]

    def items(self, source: str = "product_ui", page: int = 1, thread: str | None = None,
              product: str | None = None, q: str | None = None,
              date_from: str | None = None, date_to: str | None = None) -> dict:
        where, args = "source=?", [source]
        if thread:
            where += " AND thread=?"
            args.append(thread)
        if product:
            where += " AND product=?"
            args.append(product)
        if q:
            where += " AND instr(lower(json_extract(payload, '$.query')), lower(?)) > 0"
            args.append(q)
        if date_from:
            where += " AND date(json_extract(payload, '$.updated_at')) >= date(?)"
            args.append(date_from)
        if date_to:
            where += " AND date(json_extract(payload, '$.updated_at')) <= date(?)"
            args.append(date_to)
        with self.connection() as c:
            total = c.execute(f"SELECT count(*) FROM records WHERE {where}", args).fetchone()[0]
            rows = c.execute(f"SELECT * FROM records WHERE {where} ORDER BY id DESC LIMIT 20 OFFSET ?",
                             (*args, (page - 1) * 20)).fetchall()
            return {"total": total, "page": page, "items": [self._record(row) for row in rows]}

    def data_page(self, source="all", page=1, product=None, q=None, thread=None):
        """Read records and source status in one snapshot, without probing products."""
        where, args = "source IN ('product_ui','legacy_unknown')", []
        for column, value in (("source", None if source == "all" else source),
                              ("product", product), ("thread", thread)):
            if value:
                where += f" AND {column}=?"
                args.append(value)
        if q:
            where += (" AND (instr(lower(COALESCE(json_extract(payload, '$.query'),'')),"
                      " lower(?)) > 0 OR instr(lower(COALESCE("
                      "json_extract(payload, '$.answer'),'')), lower(?)) > 0)")
            args.extend((q, q))
        with self.connection() as c:
            c.execute("BEGIN")
            total = c.execute(f"SELECT count(*) FROM records WHERE {where}", args).fetchone()[0]
            rows = c.execute(f"SELECT * FROM records WHERE {where} ORDER BY id DESC "
                             "LIMIT 20 OFFSET ?", (*args, (page - 1) * 20)).fetchall()
            # Include a source even when it only has an empty or failed sync.
            sources = c.execute("""
                WITH identities AS (
                    SELECT product FROM records WHERE source IN ('product_ui','legacy_unknown')
                    UNION SELECT product FROM checkpoints UNION SELECT product FROM jobs
                ) SELECT i.product, cp.observed_at, j.id AS job_id, j.state AS sync_state,
                    j.finished_at, j.error
                FROM identities i LEFT JOIN checkpoints cp ON cp.product=i.product
                LEFT JOIN jobs j ON j.id=(SELECT max(id) FROM jobs WHERE product=i.product)
                ORDER BY i.product
            """).fetchall()
            return {"total": total, "page": page, "items": [self._record(r) for r in rows],
                    "sources": [dict(row) for row in sources]}

    def _record(self, row) -> dict:
        return {**json.loads(row["payload"]), "archive_id": row["id"],
                "product": row["product"], "source_channel": row["source"],
                "rollback_detected": bool(row["rollback"])}

    def detail(self, identity: int) -> dict | None:
        with self.connection() as c:
            row = c.execute("SELECT * FROM records WHERE id=?", (identity,)).fetchone()
            return self._record(row) if row else None

    def enqueue(self, product: str) -> int:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            self._require_active(c, product)
            row = c.execute("SELECT id FROM jobs WHERE product=? AND state IN ('queued','running')",
                            (product,)).fetchone()
            if row:
                return row[0]
            return c.execute("INSERT INTO jobs(product,state,created_at) VALUES(?,'queued',?)",
                             (product, datetime.now(UTC).isoformat())).lastrowid

    def claim(self, products=None) -> dict | None:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE jobs SET state='cancelled',error='product_disabled',finished_at=? "
                      "WHERE state='queued' AND product IN "
                      "(SELECT product FROM product_activity WHERE state!='ready')",
                      (datetime.now(UTC).isoformat(),))
            if products is not None and not products:
                return None
            where = " AND product IN (" + ",".join("?" for _ in products) + ")" if products is not None else ""
            row = c.execute("SELECT * FROM jobs WHERE state='queued'" + where + " ORDER BY id LIMIT 1",
                            tuple(products) if products is not None else ()).fetchone()
            if row:
                c.execute("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
                return dict(row)

    def finish(self, identity: int, state: str, error: str | None = None, imported: int = 0):
        with self.connection() as c:
            c.execute("UPDATE jobs SET state=?,error=?,imported=?,finished_at=? WHERE id=? AND state IN ('queued','running')",
                      (state, error, imported, datetime.now(UTC).isoformat(), identity))

    def recover(self):
        # Caller must hold the worker's OS lock, so no previous worker is alive.
        with self.connection() as c:
            c.execute("UPDATE jobs SET state='interrupted',error='worker_restarted' WHERE state='running'")

    def jobs(self) -> list[dict]:
        with self.connection() as c:
            return [dict(row) for row in c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 30")]


    def job(self, identity: int) -> dict | None:
        with self.connection() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            return dict(row) if row else None


class ReadOnlySQLiteArchive(SQLiteArchive):
    """Expose an existing platform archive without schema changes or write access."""

    def __init__(self, path: Path, source_url=""):
        self.path = Path(path).resolve(strict=True)
        self.source_url = source_url.rstrip("/")

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()
