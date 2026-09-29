"""Run existing JS/browser regressions serially, each component in a private namespace."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
reports = []
for name in ("AgentOS-K", "PaperReader", "GitHub-Wiki"):
    tests = sorted((root / "components" / name / "tests").glob("*.test.mjs"))
    command = ["bash", str(root / "scripts/run-isolated.sh"), "bash", "-c",
        'cd "$1"; shift; exec node --test --test-timeout=90000 --test-concurrency=1 "$@"',
        "frontend-tests", str(root / "components" / name), *map(str, tests)]
    log = root / ".local/preparation" / (name + "-frontend-tests.log")
    with log.open("w") as output:
        result = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT)
    item = {"component": name, "exit_code": result.returncode,
        "log": str(log), "summary": log.read_text(errors="replace").splitlines()[-12:]}
    reports.append(item)
    (root / ".local/preparation/frontend-tests.json").write_text(json.dumps(reports, indent=2))
    print(json.dumps(item), flush=True)
raise SystemExit(0 if all(x["exit_code"] == 0 for x in reports) else 1)
