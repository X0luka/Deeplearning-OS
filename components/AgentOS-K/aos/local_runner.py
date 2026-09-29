"""Foreground WSL supervisor used by the hidden Windows launcher."""

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from aos.adapters.process import identity

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "var"


def stop():
    path = DATA / "local-runner.json"
    if not path.is_file():
        return
    saved = json.loads(path.read_text())
    if saved.get("start") and identity(saved["pid"]) == saved["start"]:
        os.kill(saved["pid"], signal.SIGTERM)


def run():
    DATA.mkdir(exist_ok=True)
    with (DATA / "local-runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("AgentOS background runner already active.")
            return
        record = {"pid": os.getpid(), "start": identity(os.getpid())}
        (DATA / "local-runner.json").write_text(json.dumps(record))
        stopped = False
        child = None

        def shutdown(signum, frame):
            nonlocal stopped
            stopped = True
            if child is not None and child.poll() is None:
                child.terminate()

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        logs = DATA / "logs"
        logs.mkdir(exist_ok=True)
        failures = 0
        while not stopped and failures < 5:
            started = time.monotonic()
            fd = os.open(logs / "host.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "ab") as log:
                child = subprocess.Popen(
                    [sys.executable, "-m", "aos.bootstrap", "serve", "--autostart"],
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                )
                code = child.wait()
            if stopped:
                break
            failures = failures + 1 if time.monotonic() - started < 60 else 0
            if failures:
                print(f"Host exited ({code}); attempt {failures}/5", flush=True)
            for _ in range(min(2**failures, 30) * 10):
                if stopped:
                    break
                time.sleep(0.1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["start", "stop"], nargs="?", default="start")
    args = p.parse_args()
    (stop if args.action == "stop" else run)()


if __name__ == "__main__":
    main()
