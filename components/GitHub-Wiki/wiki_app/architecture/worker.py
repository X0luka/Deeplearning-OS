"""One bounded analysis process; never imports or executes snapshot code."""

import argparse
import json
import signal
import time
from pathlib import Path

from ..source import DiskFiles
from ..store import Store
from . import graph as graph_module
from .facts import digest
from .graph import analyze


def run(root, version_id, output):
    store = Store(root)
    version = store.version(version_id)
    if not version:
        raise ValueError("version_not_found")
    raw = store.root / "raw" / version_id
    started = time.monotonic()
    for entry in version["manifest"]["files"]:
        target = (raw / entry["path"]).resolve()
        if not target.is_relative_to(raw.resolve()):
            raise ValueError("invalid_snapshot_path")
        data = target.read_bytes()
        if entry.get("sha256") and digest(data) != entry["sha256"]:
            raise ValueError("snapshot_hash_mismatch")
    files = DiskFiles(raw, [entry["path"] for entry in version["manifest"]["files"]])
    original = graph_module.cached_parse
    def timeout_handler(signum, frame):
        raise TimeoutError("file_parse_timeout")
    signal.signal(signal.SIGALRM, timeout_handler)
    def bounded_parse(*args):
        signal.setitimer(signal.ITIMER_REAL, 15)
        try:
            return original(*args)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    graph_module.cached_parse = bounded_parse
    graph = analyze(
        files,
        store.root / "cache" / "syntax",
        version_id,
        version["sha"],
        len(version["manifest"].get("skipped", [])),
    )
    graph["analysis_id"] = digest(
        json.dumps({k: v for k, v in graph.items() if k != "coverage"}, sort_keys=True).encode()
    )
    graph["coverage"]["duration_seconds"] = round(time.monotonic() - started, 3)
    Path(output).write_text(json.dumps(graph, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("version")
    parser.add_argument("output")
    args = parser.parse_args()
    run(args.root, args.version, args.output)
