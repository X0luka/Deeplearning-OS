"""Disk backed material pages; request-sized memory, durable evidence separately."""
import json
import sqlite3
import uuid


class MaterialPages:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (uuid.uuid4().hex + ".sqlite")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, payload TEXT)")
        self.count = 0

    def extend(self, items):
        with sqlite3.connect(self.path) as db:
            db.executemany("INSERT INTO items(payload) VALUES(?)",
                           [(json.dumps(item, ensure_ascii=False),) for item in items])
        self.count += len(items)

    def __len__(self):
        return self.count

    def __iter__(self):
        with sqlite3.connect(self.path) as db:
            for row in db.execute("SELECT payload FROM items ORDER BY id"):
                yield json.loads(row[0])

    def close(self):
        self.path.unlink(missing_ok=True)


class EvidenceSelection:
    def __init__(self, store, turn, ids):
        self.store, self.turn, self.ids = store, turn, ids

    def __iter__(self):
        with self.store.db() as db:
            for identity in self.ids:
                row = db.execute("SELECT payload FROM turn_evidence WHERE turn=? AND id=?",
                                 (self.turn, identity)).fetchone()
                yield json.loads(row[0])
