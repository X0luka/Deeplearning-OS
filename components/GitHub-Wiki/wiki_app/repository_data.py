"""Repository-owned data deletion with durable, resumable receipts.
Question/answer snapshots and external exports/backups are deliberately retained.
"""
import hashlib
import json
import re
import shutil
from pathlib import Path

from .rules import RuleError
from .store import now


def bounded(root, relative):
    root = Path(root).resolve()
    parts = Path(relative).parts
    if len(parts) != 2 or parts[0] not in {"raw", "wiki", "tmp"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", parts[1]):
        raise ValueError("delete_unsafe_path")
    target = root / relative
    # Reject symlinks at every level, including the target itself.
    for part in [target, *target.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("delete_unsafe_path")
    if not target.resolve().is_relative_to(root):
        raise ValueError("delete_unsafe_path")
    return target


def footprint(path):
    if not path.exists():
        return 0
    if path.is_symlink():
        raise ValueError("delete_unsafe_path")
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def inventory(store, identity, connection=None):
    def rows(sql, args=()):
        if connection is not None:
            return [dict(x) for x in connection.execute(sql, args)]
        return store.rows(sql, args)
    repo = next(iter(rows("SELECT * FROM repos WHERE id=?", (identity,))), None)
    if not repo:
        raise ValueError("repository_not_found")
    versions = rows("SELECT id,sha,created FROM versions WHERE repo=? ORDER BY id", (identity,))
    vids = {v["id"] for v in versions}
    staging = []
    for m in rows("SELECT key,value FROM meta WHERE key LIKE 'staging:%'"):
        value = json.loads(m["value"])
        if value.get("repo") == identity:
            if value.get("version"):
                vids.add(value["version"])
            staging.append(m["key"])
    jobs = []
    for j in rows("SELECT id,kind,args,state FROM jobs ORDER BY id"):
        args = json.loads(j["args"])
        if args.get("repo") == identity or args.get("version") in vids:
            jobs.append(j)
    paths = [f"{folder}/{vid}" for vid in sorted(vids) for folder in ("raw", "wiki")]
    paths += ["tmp/"+key.removeprefix("staging:") for key in staging]
    return repo, versions, jobs, paths, staging


def preview(store, identity):
    old = store.meta("deletion:"+identity)
    if old:
        saved = json.loads(old)
        return {**saved["preview"], "deletion_state": saved["state"], "job_id": saved.get("job_id")}
    repo, versions, jobs, paths, staging = inventory(store, identity)
    basis = {"repo": repo, "versions": versions, "jobs": jobs, "paths": paths, "staging": staging}
    token = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
    size = sum(footprint(bounded(store.root, path)) for path in paths)
    return {"repo_id": identity, "name": repo["url"].removeprefix("https://github.com/"),
            "preview_token": token, "versions": len(versions), "file_bytes": size,
            "active_jobs": [j["id"] for j in jobs if j["state"] in {"queued", "running"}],
            "retained": ["Wiki 问答及其引用快照", "平台独立会话", "已有导出和备份", "无法确认归属的共享缓存和笔记"],
            "deletion_state": None}


def request_delete(store, identity, token, name):
    current = preview(store, identity)
    if current["name"] != name:
        raise RuleError("deletion_confirmation_required", stage="delete", resource=identity)
    if current["preview_token"] != token:
        raise RuleError("deletion_preview_changed", stage="delete", resource=identity)
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("SELECT value FROM meta WHERE key=?", ("deletion:"+identity,)).fetchone()
        if not existing:
            repo, versions, jobs, paths, staging = inventory(store, identity, c)
            basis = {"repo": repo, "versions": versions, "jobs": jobs, "paths": paths, "staging": staging}
            actual = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
            if actual != token:
                raise RuleError("deletion_preview_changed", stage="delete", resource=identity)
            if any(j["state"] in {"queued", "running"} for j in jobs):
                raise RuleError("repo_busy", stage="delete", resource=identity)
            receipt = {"state": "pending", "created": now(), "preview": current,
                       "versions": versions, "jobs": jobs, "paths": paths, "staging": staging}
            c.execute("INSERT INTO meta VALUES(?,?)", ("deletion:"+identity, json.dumps(receipt)))
    job = store.enqueue("delete_repo", {"repo": identity})
    receipt = json.loads(store.meta("deletion:"+identity))
    receipt.update(job_id=job)
    store.set_meta("deletion:"+identity, json.dumps(receipt))
    return {"job_id": job}


def execute_delete(store, identity, job):
    receipt = json.loads(store.meta("deletion:"+identity) or "{}")
    if receipt.get("state") == "complete":
        return {"deleted_repo": identity, "file_bytes": receipt.get("file_bytes", 0)}
    if not receipt:
        raise ValueError("deletion_receipt_missing")
    receipt["state"] = "deleting"
    store.set_meta("deletion:"+identity, json.dumps(receipt))
    removed = set(receipt.get("removed", []))
    try:
        for relative in receipt["paths"]:
            if store.cancelled(job):
                raise ValueError("deletion_cancelled")
            target = bounded(store.root, relative)
            if target.exists():
                shutil.rmtree(target)
            removed.add(relative)
            receipt["removed"] = sorted(removed)
            store.set_meta("deletion:"+identity, json.dumps(receipt))
        with store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            for relative in receipt["paths"]:
                if relative.startswith("raw/"):
                    c.execute("DELETE FROM chunks WHERE version=?", (relative.removeprefix("raw/"),))
            for v in receipt["versions"]:
                c.execute("DELETE FROM chunks WHERE version=?", (v["id"],))
                c.execute("DELETE FROM versions WHERE id=?", (v["id"],))
                c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                          ("deleted-version:"+v["id"], json.dumps({"repo": identity,
                           "name": receipt["preview"]["name"], "sha": v["sha"]})))
                # Retain original evidence and sync identity; mark its source unavailable.
                for row in c.execute("SELECT id,payload FROM interactions WHERE json_extract(payload,'$.version_id')=?",
                                     (v["id"],)).fetchall():
                    data = json.loads(row["payload"])
                    data.update(source_deleted=True, source_repository=receipt["preview"]["name"])
                    c.execute("UPDATE interactions SET payload=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row["id"]))
            for j in receipt["jobs"]:
                if j["kind"] not in {"chat", "ask", "delete_repo"}:
                    c.execute("DELETE FROM jobs WHERE id=?", (j["id"],))
                    for prefix in ("execution:", "failure:", "staging:"):
                        c.execute("DELETE FROM meta WHERE key=?", (prefix+j["id"],))
            for key in receipt["staging"]:
                c.execute("DELETE FROM meta WHERE key=?", (key,))
            c.execute("DELETE FROM repos WHERE id=?", (identity,))
            receipt.update(state="complete", completed=now(), file_bytes=receipt["preview"]["file_bytes"])
            c.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(receipt), "deletion:"+identity))
            c.execute("INSERT OR REPLACE INTO meta VALUES('database_compaction_pending','true')")
        return {"deleted_repo": identity, "file_bytes": receipt["file_bytes"],
                "database_compaction_pending": True, "history_retained": True}
    except BaseException:
        receipt["state"] = "incomplete"
        store.set_meta("deletion:"+identity, json.dumps(receipt))
        raise


def compact(store):
    # Called by the serial worker only when no job is running/queued.
    if store.one("SELECT id FROM jobs WHERE state IN ('queued','running') LIMIT 1"):
        return
    required = (store.root/"wiki.db").stat().st_size * 2 + 16*1024*1024
    if shutil.disk_usage(store.root).free < required:
        return
    store.set_meta("maintenance_lock", "true")
    try:
        with store.db() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("VACUUM")
        store.set_meta("database_compaction_pending", "false")
    finally:
        store.set_meta("maintenance_lock", "false")
