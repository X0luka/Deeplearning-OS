"""Transactional, repeatable promotion of accepted assistant records."""
import hashlib
import json
import sqlite3
from contextlib import closing

TABLES = {"conversations": ("id",), "turns": ("id",),
          "turn_evidence": ("turn", "id"), "events": ("id",)}


def transfer(source, destination, report):
    # Never construct Store here: its startup recovery changes active tasks.
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
        src.row_factory = dst.row_factory = sqlite3.Row
        conflicts, counts = [], {}
        if any(db.execute("SELECT 1 FROM turns WHERE state IN ('queued','running')").fetchone() for db in (src, dst)):
            raise ValueError("assistant_transfer_busy")
        dst.execute("BEGIN IMMEDIATE")
        try:
            dst.execute("CREATE TABLE IF NOT EXISTS promotion_records(source TEXT, tab TEXT, identity TEXT, digest TEXT, PRIMARY KEY(source,tab,identity))")
            origin = str(source.resolve())
            for table, keys in TABLES.items():
                counts[table] = 0
                if [x[1] for x in src.execute(f"PRAGMA table_info({table})")] != [x[1] for x in dst.execute(f"PRAGMA table_info({table})")]:
                    raise ValueError("assistant_transfer_schema_mismatch")
                for row in src.execute(f"SELECT * FROM {table}"):
                    value = dict(row)
                    identity = json.dumps([value[k] for k in keys])
                    digest = hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
                    prior = dst.execute("SELECT digest FROM promotion_records WHERE source=? AND tab=? AND identity=?",(origin,table,identity)).fetchone()
                    if prior:
                        if prior[0] != digest: conflicts.append({"table":table,"id":identity,"reason":"source_changed"})
                        continue
                    existing = dst.execute(f"SELECT * FROM {table} WHERE " + " AND ".join(k+"=?" for k in keys),tuple(value[k] for k in keys)).fetchone()
                    if table == "events":
                        value.pop("id")  # Event IDs are local sequence numbers, not citation identities.
                        existing = None
                    if existing and dict(existing) != value:
                        conflicts.append({"table":table,"id":identity,"reason":"id_conflict"});continue
                    if not existing:
                        try:
                            dst.execute(f"INSERT INTO {table} ("+",".join(value)+") VALUES ("+",".join("?" for _ in value)+")",tuple(value.values()))
                            counts[table] += 1
                        except sqlite3.IntegrityError:
                            conflicts.append({"table":table,"id":identity,"reason":"unique_conflict"});continue
                    dst.execute("INSERT INTO promotion_records VALUES(?,?,?,?)",(origin,table,identity,digest))
            report.write_text(json.dumps({"counts":counts,"conflicts":conflicts,"committed":not conflicts},ensure_ascii=False,indent=2))
            if conflicts: raise ValueError("assistant_transfer_conflict")
            dst.commit()
        except BaseException:
            dst.rollback();raise
    return counts
