"""Document-level library organization; trash never removes papers or user records."""
import unicodedata
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from .types import StrictModel


class LibraryAction(StrictModel):
    action: Literal['pin', 'unpin', 'trash', 'restore', 'rename']
    name: str | None = Field(default=None, max_length=180)


def listing(store):
    with store.db() as db:
        rows = db.execute('''SELECT s.*, d.name, COALESCE(l.pinned,0) pinned, l.deleted_at
            FROM sessions s JOIN documents d ON d.id=s.document
            LEFT JOIN library_state l ON l.document=s.document ORDER BY s.updated DESC, s.rowid DESC''').fetchall()
    files = {}
    for row in rows:
        s = dict(row)
        if s['document'] not in files:
            files[s['document']] = {
                'document': s['document'], 'title': s['name'], 'pinned': bool(s['pinned']),
                'deleted_at': s['deleted_at'], 'updated': s['updated'], 'page': s['page'],
                'session': s['id'], 'sessions': [],
            }
        files[s['document']]['sessions'].append({k: s[k] for k in ('id', 'title', 'page', 'updated', 'document')})
    items = sorted(files.values(), key=lambda f: (f['pinned'], f['updated'], f['document']), reverse=True)
    return {'items': [f for f in items if not f['deleted_at']],
            'trash': [f for f in items if f['deleted_at']]}


def apply_action(store, identity, action, name=None):
    if action == 'rename':
        name = (name or '').strip()
        if not name or any(unicodedata.category(c).startswith('C') or c in '/\\' for c in name):
            raise HTTPException(422, 'library_name_invalid')
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM documents WHERE id=?', (identity,)).fetchone():
            raise HTTPException(404, 'document_not_found')
        if action == 'trash':
            busy = db.execute("SELECT 1 FROM reading_jobs WHERE document=? AND state IN "
                              "('queued','fetching','parsing','translating','validating')", (identity,)).fetchone()
            busy = busy or db.execute("SELECT 1 FROM translations WHERE document=? AND state IN "
                                     "('queued','running')", (identity,)).fetchone()
            busy = busy or db.execute("SELECT 1 FROM jobs WHERE json_extract(request,'$.document')=? "
                                     "AND state IN ('queued','running')", (identity,)).fetchone()
            if busy:
                raise HTTPException(409, 'library_document_busy')
        db.execute('INSERT OR IGNORE INTO library_state(document) VALUES(?)', (identity,))
        if action == 'rename':
            db.execute('UPDATE documents SET name=? WHERE id=?', (name, identity))
            db.execute('UPDATE sessions SET title=? WHERE document=?', (name, identity))
        elif action in ('pin', 'unpin'):
            db.execute('UPDATE library_state SET pinned=? WHERE document=?', (int(action == 'pin'), identity))
        elif action == 'trash':
            db.execute('UPDATE library_state SET deleted_at=COALESCE(deleted_at,CURRENT_TIMESTAMP) WHERE document=?', (identity,))
        elif action == 'restore':
            db.execute('UPDATE library_state SET deleted_at=NULL WHERE document=?', (identity,))
    return {'saved': True}


def install(app, store):
    @app.post('/api/library/{identity}/actions')
    def action(identity: str, value: LibraryAction):
        return apply_action(store, identity, value.action, value.name)
