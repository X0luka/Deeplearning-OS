"""Consistent local database snapshots; never modify source databases."""
import json
import sqlite3
import sys
from pathlib import Path

root, target = Path(sys.argv[1]), Path(sys.argv[2])
target.mkdir(parents=True, exist_ok=False)
manifest = []
for name in ('AgentOS-K', 'GitHub-Wiki', 'PaperReader'):
    directory = root.parent / name / 'var'
    for source in directory.rglob('*'):
        if source.suffix not in {'.db', '.sqlite', '.sqlite3'} or 'maintenance' in source.parts:
            continue
        destination = target / name / source.relative_to(directory)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (sqlite3.connect(f'file:{source}?mode=ro', uri=True) as original,
              sqlite3.connect(destination) as copied):
            original.backup(copied)
            if copied.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError(f'Invalid snapshot: {destination.name}')
        manifest.append({'source': str(source), 'backup': str(destination)})
(target / 'manifest.json').write_text(json.dumps(manifest, indent=2))
print(f'Validated {len(manifest)} database snapshots')
