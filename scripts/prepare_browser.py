"""Install the pinned Playwright headless browser into this copy only."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--proxy", default="")
args = parser.parse_args()
node = root / ".local/tools/node/bin/node"
cli = root / "node_modules/playwright/cli.js"
env = {"PATH": str(node.parent) + ":/usr/bin:/bin", "LANG": "C.UTF-8",
       "HOME": str(root / ".local/setup-home"),
       "PLAYWRIGHT_BROWSERS_PATH": str(root / ".local/browsers"),
       "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT": "30000",
       "NODE_OPTIONS": "--max-old-space-size=256"}
if args.proxy:
    proxy = urlsplit(args.proxy)
    if proxy.scheme not in ("http", "https") or not proxy.hostname or proxy.username or proxy.password:
        raise SystemExit("Use an HTTP(S) proxy without embedded credentials")
    env.update(HTTP_PROXY=args.proxy, HTTPS_PROXY=args.proxy, NO_PROXY="localhost,127.0.0.1,::1")
log = root / ".local/preparation/browser-install.log"
started = time.monotonic()
with log.open("w") as output:
    try:
        result = subprocess.run([str(node), str(cli), "install", "chromium", "--only-shell"],
            cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=240)
        code = result.returncode
    except subprocess.TimeoutExpired:
        code = 124
report = {"exit_code": code, "seconds": round(time.monotonic()-started, 1), "log": str(log)}
(root / ".local/preparation/browser-install.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
raise SystemExit(code)
