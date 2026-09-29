"""Bound test duration and sampled aggregate RSS inside the private PID namespace."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

assert os.environ.get("DLOS_ISOLATED_TEST") == "1"
ns = next(line for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("NSpid:"))
assert ns.split()[-1] == "1", "Run only as PID 1 in the private test namespace"
os.nice(10)
process = subprocess.Popen(sys.argv[1:], start_new_session=True)
started = time.monotonic()
peak = 0
reason = ""
while process.poll() is None:
    total = 0
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            for line in (directory / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
                    break
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    peak = max(peak, total)
    if total > 1024 * 1024:
        reason = "sampled aggregate RSS exceeded 1 GiB"
    elif time.monotonic() - started > 600:
        reason = "600 second deadline"
    if reason:
        os.killpg(process.pid, signal.SIGKILL)
        break
    time.sleep(0.1)
code = process.wait()
print(json.dumps({"resource_guard": {"peak_sampled_rss_mib": round(peak/1024, 1),
    "seconds": round(time.monotonic()-started, 1), "stop_reason": reason, "exit_code": code}}),
    file=sys.stderr, flush=True)
sys.exit(124 if reason else code if code >= 0 else 128-code)
