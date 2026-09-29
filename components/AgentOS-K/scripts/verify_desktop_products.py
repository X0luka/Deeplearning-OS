"""Four ownership combinations using real product servers and isolated empty data."""
import asyncio
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

import httpx

from aos.adapters.process import LocalProcesses, identity
from aos.bootstrap import parse_args


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def run():
    root = Path(__file__).resolve().parents[1]
    results = []
    with tempfile.TemporaryDirectory(prefix='aos-desktop-') as temporary:
        base = Path(temporary)
        for name, package in [('GitHub-Wiki', 'wiki_app'), ('PaperReader', 'paper_reader')]:
            target = base / name
            target.mkdir()
            for item in ['.venv', package]:
                (target / item).symlink_to(root.parent / name / item, target_is_directory=True)
        for external in [(), ('github-wiki',), ('paper',), ('github-wiki', 'paper')]:
            home = base / ('session-' + str(len(results)))
            home.mkdir()
            options = ['--products', 'wiki,paper', '--data-dir', str(home),
                       '--port', str(port()), '--wiki-dir', str(base / 'GitHub-Wiki'),
                       '--paper-dir', str(base / 'PaperReader'),
                       '--wiki-url', f'http://127.0.0.1:{port()}',
                       '--paper-url', f'http://127.0.0.1:{port()}']
            args, providers = parse_args(['serve', *options])
            entries = [p.create(args) for p in providers]
            owners = {e.product.id: LocalProcesses(e.services, None, home) for e in entries}
            process = None
            try:
                for key in external:
                    await owners[key].ensure_product()
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-m', 'aos.desktop_session', *options, cwd=root,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=(home / 'stderr.log').open('wb'))
                token = os.urandom(32).hex()
                process.stdin.write((json.dumps({'token': token})+'\n').encode())
                await process.stdin.drain()
                ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
                assert ready['event'] == 'ready', ready
                async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                    for _ in range(100):
                        apps = (await client.get(ready['url']+'api/v1/apps')).json()
                        if all(a['activity']['state'] == 'ready' for a in apps):
                            break
                        await asyncio.sleep(.2)
                    assert len(apps) == 2
                    assert all(a['activity']['state'] == 'ready' for a in apps), apps
                    assert all(a['activity']['ownership'] ==
                               ('external' if a['id'] in external else 'running') for a in apps)
                process.stdin.write((json.dumps({'token': token, 'action': 'exit'})+'\n').encode())
                await process.stdin.drain()
                output, errors = await asyncio.wait_for(process.communicate(), 40)
                assert process.returncode == 0, errors
                assert '"event": "closed"' in output.decode()
                for key, owner in owners.items():
                    assert await owner.probe(owner.services[0]) is (key in external)
                results.append({'external': external, 'result': 'PASS'})
                print(json.dumps(results[-1]), flush=True)
            finally:
                if process and process.returncode is None:
                    process.stdin.close()
                    await asyncio.wait_for(process.wait(), 40)
                for owner in owners.values():
                    for service in owner.services:
                        record = owner.saved(service)
                        if record and identity(record['pid']) == record['start']:
                            await owner.stop(record)
                for entry in entries:
                    await entry.client.aclose()
    print(json.dumps({'combinations': results, 'data': 'isolated temporary databases'}))


if __name__ == '__main__':
    asyncio.run(run())
