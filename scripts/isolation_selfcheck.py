"""Prove the test namespace hides production and cannot reach host services."""
import json
import os
from pathlib import Path
import socket

root = Path(__file__).resolve().parents[1]
assert os.environ.get('DLOS_ISOLATED_TEST') == '1'
baseline = root / '.local/preparation/baseline.json'
sources = [r['source'] for r in json.loads(baseline.read_text())['repositories']] if baseline.exists() else []
for source in sources:
    assert not Path(source).exists(), 'Source path visible in private namespace'
assert not Path('/mnt/c').exists()
assert not Path('/run/docker.sock').exists()
assert not any(k.endswith(('API_KEY', 'TOKEN', 'SECRET')) for k in os.environ)
assert len([line for line in Path('/proc/net/route').read_text().splitlines()[1:] if line.split()[1] == '00000000']) == 0
for port in (7777, 8601, 8602, 8777, 8701, 8702):
    with socket.socket() as s:
        s.settimeout(0.1)
        assert s.connect_ex(('127.0.0.1', port)) != 0, port
# O_TMPFILE creates no directory entry and cannot leave an unwanted host file.
try:
    fd = os.open('/etc', os.O_TMPFILE | os.O_RDWR, 0o600)
except OSError as exc:
    assert exc.errno in (13, 30), exc
else:
    os.close(fd)
    raise AssertionError('System filesystem is writable')
probe = root / '.local/tmp/isolation-probe'
probe.write_text('isolated')
assert probe.read_text() == 'isolated'
probe.unlink()
print(json.dumps({'source_directories_hidden': True if sources else None, 'source_paths_checked': len(sources), 'windows_mount_hidden': True,
                  'production_network_unreachable': True, 'outbound_network_disabled': True,
                  'system_read_only': True, 'workspace_writable': True,
                  'credentials_not_inherited': True}))
