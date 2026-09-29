"""Private PID namespace PDF guard; leave host memory reserve and clean own children."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
assert os.environ.get("DLOS_ISOLATED_TEST")=="1"
ns=next(x for x in Path("/proc/self/status").read_text().splitlines() if x.startswith("NSpid:"))
assert ns.split()[-1]=="1"
os.nice(10)
process=subprocess.Popen(sys.argv[1:],start_new_session=True)
started=time.monotonic();peak=0;reason=""
while process.poll() is None:
    total=0
    for p in Path("/proc").glob("[0-9]*/status"):
        try:
            total+=next((int(x.split()[1]) for x in p.read_text().splitlines() if x.startswith("VmRSS:")),0)
        except (OSError,StopIteration):
            pass
    peak=max(peak,total)
    available=next(int(x.split()[1]) for x in Path("/proc/meminfo").read_text().splitlines() if x.startswith("MemAvailable:"))
    if available<1024*1024:
        reason="Host available memory below 1 GiB"
    elif total>3000*1024:
        reason="PDF aggregate RSS above 3000 MiB"
    elif time.monotonic()-started>1800:
        reason="PDF test deadline reached; checkpoint remains"
    if reason:
        os.killpg(process.pid,signal.SIGKILL)
        break
    time.sleep(.1)
code=process.wait()
print(json.dumps({"pdf_resource_guard":{"peak_rss_mib":round(peak/1024,1),"seconds":round(time.monotonic()-started,1),"reason":reason,"exit_code":code}}),flush=True)
raise SystemExit(124 if reason else code if code>=0 else 128-code)
