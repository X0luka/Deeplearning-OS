"""Build frontends with a private, pinned Node runtime and a private npm cache."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--proxy", default="")
parser.add_argument("--offline", action="store_true")
parser.add_argument("--refresh-lock", action="store_true", help="Explicitly refresh the root browser-tool lock")
args = parser.parse_args()
node = root / ".local/tools/node/bin/node"
npm = root / ".local/tools/node/lib/node_modules/npm/bin/npm-cli.js"
assert node.exists() and npm.exists(), "Run scripts/install_node.py first"
home = root / ".local/setup-home"
home.mkdir(parents=True, exist_ok=True)
config = root / ".local/empty.npmrc"
config.write_text("")
env = {"PATH": str(node.parent) + ":/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8",
       "npm_config_cache": str(root / ".local/npm-cache"),
       "npm_config_userconfig": str(config), "npm_config_audit": "false", "npm_config_fund": "false",
       "npm_config_fetch_retries": "1", "npm_config_fetch_timeout": "30000",
       "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "NODE_OPTIONS": "--max-old-space-size=512"}
if args.proxy:
    proxy = urlsplit(args.proxy)
    if proxy.scheme not in ("http", "https") or not proxy.hostname or proxy.username or proxy.password:
        raise SystemExit("Use an HTTP(S) proxy without embedded credentials")
    env.update(HTTP_PROXY=args.proxy, HTTPS_PROXY=args.proxy, NO_PROXY="localhost,127.0.0.1,::1",
               npm_config_proxy=args.proxy, npm_config_https_proxy=args.proxy)
reports = []
jobs = [
    ("browser-tools-install", root, ["ci", "--ignore-scripts"]),
    ("reader-install", root / "components/PaperReader", ["ci", "--ignore-scripts"]),
    ("reader-build", root / "components/PaperReader", ["run", "build"]),
    ("wiki-install", root / "components/GitHub-Wiki/frontend", ["ci", "--ignore-scripts"]),
    ("wiki-typecheck", root / "components/GitHub-Wiki/frontend", ["run", "check"]),
    ("wiki-build", root / "components/GitHub-Wiki/frontend", ["run", "build"]),
]
if args.refresh_lock:
    jobs.insert(0, ("browser-tools-lock", root, ["install", "--package-lock-only", "--ignore-scripts"]))
for name, cwd, operation in jobs:
    log = root / ".local/preparation" / (name + ".log")
    command = [str(node), str(npm), *operation]
    if args.offline:
        command += ["--offline"]
    if operation[0] == "run":
        # Execute repository build code only after hiding production files and network.
        command = ["bash", str(root / "scripts/run-isolated.sh"), "bash", "-c",
                   'cd "$1"; shift; exec "$@"', "frontend-build", str(cwd), *command]
    started = time.monotonic()
    with log.open("w") as output:
        try:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=240)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    item = {"operation": name, "exit_code": code, "seconds": round(time.monotonic()-started,1),
            "log": str(log)}
    reports.append(item)
    (root / ".local/preparation/frontend-build.json").write_text(json.dumps(reports, indent=2))
    print(json.dumps(item), flush=True)
    if code:
        break
raise SystemExit(0 if all(r["exit_code"] == 0 for r in reports) and len(reports)==len(jobs) else 1)
