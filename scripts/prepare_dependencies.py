"""Rebuild isolated dependency environments; never mutate production or global settings."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--uv", required=True, help="Existing uv executable; no global installation")
parser.add_argument("--proxy", default="", help="Optional proxy for this invocation only")
parser.add_argument("--refresh-locks", action="store_true")
parser.add_argument("--offline", action="store_true")
args = parser.parse_args()
if args.proxy:
    parsed = urlsplit(args.proxy)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("Expected an HTTP(S) proxy without embedded credentials.")
local = root / ".local"
home = local / "setup-home"
home.mkdir(parents=True, exist_ok=True)
env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8",
       "UV_CACHE_DIR": str(local / "uv-cache"), "UV_LINK_MODE": "copy",
       "UV_PYTHON_DOWNLOADS": "never", "UV_HTTP_TIMEOUT": "20", "UV_HTTP_RETRIES": "1",
       "UV_CONCURRENT_DOWNLOADS": "2", "UV_CONCURRENT_BUILDS": "1", "UV_CONCURRENT_INSTALLS": "1",
       "PYTHONNOUSERSITE": "1"}
if args.proxy:
    env.update(HTTP_PROXY=args.proxy, HTTPS_PROXY=args.proxy, NO_PROXY="localhost,127.0.0.1,::1")
reports = []
for name in ("AgentOS-K", "PaperReader", "GitHub-Wiki"):
    component = root / "components" / name
    operations = [["lock"]] if args.refresh_locks else []
    operations += [["sync", "--locked", "--extra", "dev", "--python", "/usr/bin/python3"]]
    for operation in operations:
        log = local / "preparation" / (name + "-" + operation[0] + "-relocated.log")
        command = [args.uv, "--no-config", *operation]
        if args.offline:
            command.append("--offline")
        started = time.monotonic()
        with log.open("w") as output:
            try:
                result = subprocess.run(command, cwd=component, env=env, stdout=output,
                                        stderr=subprocess.STDOUT, timeout=240)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
        item = {"component": name, "operation": operation[0], "exit_code": code,
                "seconds": round(time.monotonic() - started, 1), "log": str(log)}
        reports.append(item)
        (local / "preparation/dependency-rebuild.json").write_text(json.dumps(reports, indent=2))
        print(json.dumps(item), flush=True)
        if code:
            break
if all(r["exit_code"] == 0 for r in reports):
    command = [str(root / "components/AgentOS-K/.venv/bin/python"),
               str(root / "components/AgentOS-K/scripts/install_assistant_tokenizers.py")]
    if args.proxy:
        command += ["--proxy", args.proxy]
    if args.offline:
        command.append("--offline")
    log = local / "preparation/assistant-tokenizers-setup.log"
    with log.open("w") as output:
        try:
            result = subprocess.run(command, cwd=root, env=env, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=180)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    item = {"component": "AgentOS-K", "operation": "tokenizers", "exit_code": code, "log": str(log)}
    reports.append(item)
    (local / "preparation/dependency-rebuild.json").write_text(json.dumps(reports, indent=2))
    print(json.dumps(item), flush=True)
raise SystemExit(0 if all(r["exit_code"] == 0 for r in reports) else 1)
