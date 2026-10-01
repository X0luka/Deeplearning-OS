"""Static preparation inventory; findings never include matching secret values."""
import argparse
import json
from pathlib import Path
import re
import tomllib

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true", help="Fail on credential patterns or missing lock declarations")
args = parser.parse_args()

root = Path(__file__).resolve().parents[1]
excluded = {'.git', '.local', '.venv', 'node_modules', 'var', '__pycache__', '.pytest_cache', '.ruff_cache'}
patterns = {
    'credential_pattern': re.compile(r'(?:sk-[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'),
    'personal_source_path': re.compile(r'/home/[^/]+/code/(?:AgentOS-K|PaperReader|GitHub-Wiki)|Documents[\\/]+ChatGPT[\\/]+AgentOS-K'),
    'shell_profile_read': re.compile(r'\.bashrc'),
}
findings, count = [], 0
for path in root.rglob('*'):
    rel = path.relative_to(root)
    if any(part in excluded for part in rel.parts) or not path.is_file() or path.is_symlink():
        continue
    if path.suffix.lower() not in {'.py', '.js', '.mjs', '.ts', '.json', '.md', '.toml', '.ps1', '.cmd', '.vbs', '.cs', '.html', '.sh', '.yml', '.yaml', '.txt'}:
        continue
    text = path.read_text(errors='replace')
    count += 1
    for line, content in enumerate(text.splitlines(), 1):
        for kind, pattern in patterns.items():
            if pattern.search(content):
                findings.append({'kind': kind, 'path': str(rel), 'line': line})
locks = []
for directory in (root / 'components').iterdir():
    project = tomllib.loads((directory / 'pyproject.toml').read_text())['project']
    lock = tomllib.loads((directory / 'uv.lock').read_text())
    package = next(p for p in lock['package'] if p['name'] == project['name'])
    normalize = lambda value: re.match(r'[A-Za-z0-9_.-]+', value)[0].lower().replace('_', '-')
    declared = {normalize(d) for d in project.get('dependencies', [])}
    locked = {d['name'] for d in package.get('dependencies', [])}
    extras = set(project.get('optional-dependencies', {}))
    locked_extras = set(package.get('optional-dependencies', {}))
    locks.append({'component': directory.name, 'missing_base_dependencies': sorted(declared - locked),
                  'missing_optional_groups': sorted(extras - locked_extras)})
report = {'files_scanned': count, 'findings': findings, 'lock_declarations': locks,
          'limitations': 'Heuristic scan only; third-party rights and full secret/privacy audit remain pending.'}
(root / '.local/preparation/static-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'files_scanned': count, 'finding_counts': {kind: sum(f['kind'] == kind for f in findings) for kind in patterns}, 'lock_declarations': locks}, ensure_ascii=False))

if args.check and (any(f["kind"] == "credential_pattern" for f in findings) or
                   any(x["missing_base_dependencies"] or x["missing_optional_groups"] for x in locks)):
    raise SystemExit(1)
