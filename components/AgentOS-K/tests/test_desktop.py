"""Real OS ownership, stale recovery and private-pipe lifecycle regressions."""
import asyncio
import json
import os
import signal
import socket
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_process import manager, service  # noqa: F401

from aos.adapters.process import identity
from aos.adapters.session_process import SessionLedger, SessionProcesses
from aos.domain import ProductError


@pytest.mark.parametrize('external', [False, True])
async def test_session_ownership(service, tmp_path, external):  # noqa: F811
    outside = manager(service, tmp_path)
    ledger = SessionLedger(tmp_path / 'ledger', 'one', grace=.2)
    desktop = SessionProcesses([service], service, tmp_path, ledger,
                               lock_dir=tmp_path / 'locks', timeout=2)
    try:
        if external:
            await outside.ensure_product()
        await desktop.ensure_product()
        assert desktop.ownership == ('external' if external else 'running')
        await desktop.close()
        assert await outside.probe(service) is external
    finally:
        await desktop.close()
        await outside.stop(outside.saved(service))


async def test_conflict_retry_and_stale_pid(service, tmp_path):  # noqa: F811
    outside = manager(service, tmp_path)
    await outside.ensure_product()
    ledger = SessionLedger(tmp_path / 'ledger', 'one', grace=.2)
    bad = replace(service, product_id='different')
    desktop = SessionProcesses([bad], bad, tmp_path, ledger, timeout=2)
    try:
        with pytest.raises(ProductError, match='port_conflict'):
            await desktop.ensure_product()
        assert await outside.probe(service)
        record = dict(outside.saved(service))
        ledger.register(record, 'unrelated')
        record['start'] = 'wrong'
        record['members'] = {}
        await ledger.stop(record)
        assert await outside.probe(service)
    finally:
        await outside.stop(outside.saved(service))
    desktop.services = [service]
    await desktop.ensure_product()
    assert desktop.ownership == 'running'
    await desktop.close()
    assert not await desktop.probe(service)


async def test_group_descendant_and_stale_recovery(service, tmp_path):  # noqa: F811
    code = ('import subprocess,time,signal; '
            'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
            'subprocess.Popen(["sleep","60"]); time.sleep(60)')
    command = replace(service, command=(sys.executable, '-c', code))
    ledger = SessionLedger(tmp_path / 'ledger', 'crashed', grace=.2)
    desktop = SessionProcesses([], command, tmp_path, ledger)
    record = desktop.spawn(command)
    await asyncio.sleep(.2)
    ledger.refresh()
    descendants = dict(record['members'])
    assert len(descendants) >= 2
    os.kill(record['pid'], signal.SIGKILL)
    await asyncio.sleep(.1)
    replacement = SessionLedger(tmp_path / 'ledger', 'replacement', grace=.2)
    await replacement.recover()
    await asyncio.sleep(.1)
    assert all(identity(int(pid)) != start for pid, start in descendants.items())
    for child in desktop.children:
        child.wait()


@pytest.mark.parametrize('how', ['exit', 'eof'])
async def test_private_pipe_credentials_and_cleanup(tmp_path, how):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'aos.desktop_session', '--products', 'none',
        '--port', str(port), '--data-dir', str(tmp_path),
        cwd=Path(__file__).resolve().parents[1], stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def send(**message):
        process.stdin.write((json.dumps(message)+'\n').encode())
        await process.stdin.drain()
    async def event():
        return json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
    try:
        await send(token='a'*32)
        assert (await event())['event'] == 'ready'
        await send(token='bad', action='exit')
        assert (await event())['event'] == 'denied'
        assert process.returncode is None
        if how == 'exit':
            await send(token='a'*32, action='exit')
        else:
            process.stdin.close()
        assert (await event())['event'] == 'closing'
        assert (await event())['event'] == 'closed'
        assert await asyncio.wait_for(process.wait(), 10) == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
