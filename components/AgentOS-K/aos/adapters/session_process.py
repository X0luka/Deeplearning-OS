"""Opt-in desktop ownership; standalone LocalProcesses semantics stay unchanged."""
import asyncio
import json
import os
import signal
import time
from pathlib import Path
from urllib.parse import urlsplit

from aos.adapters.process import LocalProcesses, identity
from aos.domain import ProductError


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def members(group):
    result = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == group and fields[0] != 'Z':
                result[path.parent.name] = fields[19]
        except (OSError, ValueError, IndexError):
            continue
    return result


class SessionLedger:
    def __init__(self, directory, session, emit=lambda **event: None, grace=30, reject_external=False):
        self.directory, self.session, self.emit, self.grace = directory, session, emit, grace
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.records = []
        self.reject_external = reject_external

    def save(self, record):
        path = self.directory / (str(record['pid']) + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record))
        temporary.chmod(0o600)
        temporary.replace(path)

    def register(self, record, name):
        record.update(session=self.session, group=record['pid'], boot=boot_id(), name=name,
                      members=members(record['pid']))
        self.records.append(record)
        self.save(record)

    def valid_members(self, record):
        if record.get('boot') != boot_id():
            return {}
        current = members(record['group'])
        known = record.get('members', {})
        known[str(record['pid'])] = record['start']
        return current if any(current.get(pid) == start for pid, start in known.items()) else {}

    def refresh(self):
        for record in self.records:
            current = self.valid_members(record)
            if current:
                record['members'] = current
                self.save(record)

    async def stop(self, record):
        current = self.valid_members(record)
        forced = False
        if current:
            record['members'] = current
            self.save(record)
            self.emit(event='stopping', service=record['name'])
            try:
                os.killpg(record['group'], signal.SIGTERM)
                deadline = time.monotonic() + self.grace
                while self.valid_members(record) and time.monotonic() < deadline:
                    await asyncio.sleep(.1)
                if self.valid_members(record):
                    forced = True
                    os.killpg(record['group'], signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.emit(event='stopped', service=record['name'], forced=forced)
        (self.directory / (str(record['pid']) + '.json')).unlink(missing_ok=True)

    async def recover(self):
        # Caller holds the exclusive desktop lock, so no live session is adopted.
        for path in self.directory.glob('*.json'):
            record = json.loads(path.read_text())
            await self.stop(record)

    async def close(self):
        await asyncio.gather(*(self.stop(r) for r in reversed(self.records)))


class SessionProcesses(LocalProcesses):
    def __init__(self, services, worker, data_dir, ledger, **kwargs):
        super().__init__(services, worker, data_dir, **kwargs)
        self.ledger = ledger
        self.ownership = 'starting'
        self.owned = []

    def spawn(self, service):
        record = super().spawn(service)
        try:
            self.ledger.register(record, service.name)
        except BaseException:
            os.killpg(record['pid'], signal.SIGKILL)
            raise
        self.owned.append(record)
        return record

    async def stop(self, record):
        if record and record in self.owned:
            await self.ledger.stop(record)
        for child in self.children:
            child.poll()

    async def ensure(self, service):
        async with self.lock(service):
            if await self.probe(service):
                mine = any(identity(r['pid']) == r['start'] and
                           self.listening(r['pid'], urlsplit(service.url).port)
                           for r in self.owned)
                if not mine and self.ledger.reject_external:
                    raise ProductError(f'{service.name}_port_conflict')
                self.ownership = 'running' if mine else 'external'
                return
            if await self.occupied(service):
                raise ProductError(f'{service.name}_port_conflict')
            record = self.spawn(service)
            try:
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    if identity(record['pid']) != record['start']:
                        raise ProductError(f'{service.name}_exited')
                    if await self.probe(service):
                        if self.ledger.reject_external and not self.listening(record['pid'], urlsplit(service.url).port):
                            raise ProductError(f'{service.name}_port_conflict')
                        self.ownership = 'running'
                        return
                    await asyncio.sleep(.1)
                raise ProductError(f'{service.name}_timeout')
            except BaseException:
                await self.stop(record)
                raise

    async def close(self):
        await asyncio.gather(*(self.stop(r) for r in self.owned))
