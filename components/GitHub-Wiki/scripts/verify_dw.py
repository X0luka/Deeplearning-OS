"""Local delivery gate: tests, lint, diff and staged-data checks with receipts."""

import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_WIKI = (ROOT / "wiki_app").is_dir()
SCOPES = ["wiki_app", "tests"] if IS_WIKI else ["aos", "tests"]
receipt = {"time": datetime.now(UTC).isoformat(), "repo": str(ROOT), "checks": []}


def run(command):
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    receipt["checks"].append(
        {"command": command, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    )
    print("PASS" if result.returncode == 0 else "FAIL", " ".join(command))
    if result.returncode:
        print(result.stdout, result.stderr)
    return result.returncode == 0


receipt["head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
diff = subprocess.check_output(["git", "diff", "HEAD"], cwd=ROOT)
receipt["diff_sha256"] = hashlib.sha256(diff).hexdigest()
ok = run([sys.executable, "-m", "ruff", "check", *SCOPES])
ok = run([sys.executable, "-m", "pytest", "tests", "-q"]) and ok
ok = run(["git", "diff", "--check"]) and ok
ok = run(["git", "diff", "--cached", "--check"]) and ok
staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=ROOT, text=True).splitlines()
for name in staged:
    p = Path(name)
    if (
        "human" in p.parts
        or "var" in p.parts
        or p.name == ".env"
        or p.suffix in {".db", ".sqlite", ".sqlite3"}
    ):
        receipt["checks"].append({"staged_forbidden": name, "exit_code": 1})
        ok = False
# Include new, untracked implementation files in provenance before the first code commit.
names = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, text=True
).splitlines()
receipt["source_hashes"] = {
    name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names if (ROOT / name).is_file()
}
doc_names = (
    ["AGENTS.md", "README.md", "docs/README.md", "docs/spec.md", "docs/quality-cases.md"]
    if IS_WIKI
    else ["AGENTS.md", "docs/integrations/github-wiki.md", "docs/plans/runs/DW-001.md"]
)
broken = []
for name in doc_names:
    doc = ROOT / name
    for target in re.findall(r"\]\(([^)]+)\)", doc.read_text()):
        target = target.split("#")[0]
        if (
            target
            and "://" not in target
            and not target.startswith("/")
            and not (doc.parent / target).exists()
        ):
            broken.append({"document": name, "target": target})
receipt["checks"].append({"docs_links": doc_names, "broken": broken, "exit_code": 1 if broken else 0})
ok = ok and not broken
receipt["passed"] = ok
out = ROOT / "var" / "verification"
out.mkdir(parents=True, exist_ok=True)
target = out / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + ".json")
target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
print("Receipt:", target)
raise SystemExit(0 if ok else 1)
