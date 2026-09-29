"""Run component regressions serially, only inside run-isolated.sh."""
import json
import os
from pathlib import Path
import subprocess
import sys

assert os.environ.get('DLOS_ISOLATED_TEST') == '1', 'Use scripts/run-isolated.sh'
root = Path(__file__).resolve().parents[1]
label = sys.argv[1] if len(sys.argv) > 1 else 'baseline'
assert label.replace('-', '').isalnum()
reports = []
for component in ('AgentOS-K', 'PaperReader', 'GitHub-Wiki'):
    directory = root / 'components' / component
    log = root / '.local/preparation' / (label + '-' + component + '-pytest.log')
    cmd = [str(directory / '.venv/bin/python'), '-m', 'pytest', '-q', '--tb=short']
    with log.open('w') as output:
        try:
            result = subprocess.run(cmd, cwd=directory, stdout=output, stderr=subprocess.STDOUT, timeout=170)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    report = {'component': component, 'exit_code': code, 'log': str(log),
              'summary': log.read_text(errors='replace').splitlines()[-10:]}
    reports.append(report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
(root / '.local/preparation' / (label + '-tests.json')).write_text(json.dumps(reports, ensure_ascii=False, indent=2))
sys.exit(0 if all(r['exit_code'] == 0 for r in reports) else 1)
