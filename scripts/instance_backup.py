"""Offline development-instance backup. Credentials and process state are excluded."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/AgentOS-K"))
from aos.backup import snapshot
from aos.desktop_instance import inside, plan
from aos.instance_guard import instance_lock, assert_stopped

WORKBENCH = ("archive.db", "assistant/assistant.db", "llm/config.db", "appearance.json")


def instance(root, name):
    return plan(root, name, (18777, 18601, 18602))


def bundle_path(root, name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        raise ValueError("invalid_backup_name")
    return inside(root, ".local", "backups", name)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(directory):
    result = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("backup_symlink_not_supported")
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = digest(path)
        elif not path.is_dir():
            raise ValueError("backup_special_file_not_supported")
    return result


def allowed(name):
    path = Path(name)
    return (not path.is_absolute() and ".." not in path.parts and path.as_posix() == name
            and (name in {"workbench/" + p for p in WORKBENCH}
                 or path.parts[0] in {"reader", "wiki"}))


def run_component(root, component, module, operation, source, target):
    command = [str(root / "components" / component / ".venv/bin/python"),
               "-m", module + ".backup", operation, str(source), str(target)]
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
           "HOME": str(root / ".local/runtime-home"), "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    try:
        subprocess.run(command, cwd=root / "components" / component, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Do not expose paths, saved content or provider configuration in subprocess errors.
        raise ValueError("component_backup_restore_failed_" + module) from None


@contextmanager
def stopped(value):
    with instance_lock(value.root, value.instance):
        inside(value.root, ".local", "instances", value.instance, "workbench", "desktop", "processes")
        inside(value.root, ".local", "instances", value.instance, "workbench", "desktop", "session.lock")
        assert_stopped(value.state)
        # Also protect against pre-guard desktop launchers during maintenance.
        session = inside(value.root, ".local", "instances", value.instance,
                         "workbench", "desktop", "session.lock")
        session.parent.mkdir(parents=True, exist_ok=True)
        import fcntl
        with session.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("instance_busy") from None
            yield


def backup(root, identity, name):
    value = instance(root, identity)
    destination = bundle_path(root, name)
    if not value.state.is_dir():
        raise ValueError("instance_not_found")
    with instance_lock(root, "backup-" + name), stopped(value):
        if destination.exists():
            raise ValueError("backup_target_exists")
        # Require a complete, initialized three-component instance.
        for rel in ("workbench/archive.db", "workbench/assistant/assistant.db",
                    "workbench/llm/config.db", "reader/paper.db", "wiki/wiki.db"):
            path = inside(root, ".local", "instances", identity, *Path(rel).parts)
            if not path.is_file():
                raise ValueError("instance_data_incomplete")
        for component in ("reader", "wiki"):
            for path in value.path(component).rglob("*"):
                if path.is_symlink():
                    raise ValueError("backup_symlink_not_supported")
        destination.mkdir(parents=True, mode=0o700)
        try:
            for rel in WORKBENCH:
                source = inside(root, ".local", "instances", identity, "workbench", *Path(rel).parts)
                if not source.exists():
                    continue
                target = destination / "workbench" / rel
                if source.suffix == ".db":
                    snapshot(source, target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            run_component(root, "PaperReader", "paper_reader", "backup", value.path("reader"), destination / "reader")
            run_component(root, "GitHub-Wiki", "wiki_app", "backup", value.path("wiki"), destination / "wiki")
            manifest = {"format": 1, "credentials": "excluded", "files": inventory(destination)}
            (destination / "bundle.json").write_text(json.dumps(manifest, indent=2))
        except BaseException:
            shutil.rmtree(destination)
            raise
    return destination


def verify(source):
    manifest = json.loads((source / "bundle.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("credentials") != "excluded":
        raise ValueError("invalid_instance_backup")
    files = manifest.get("files")
    required = {"workbench/archive.db", "workbench/assistant/assistant.db", "workbench/llm/config.db",
                "reader/paper.db", "reader/manifest.json", "wiki/wiki.db", "wiki/backup.json"}
    if not isinstance(files, dict) or not required <= files.keys() or not all(allowed(n) for n in files):
        raise ValueError("invalid_instance_backup_paths")
    current = inventory(source)
    current.pop("bundle.json", None)
    if current != files:
        raise ValueError("instance_backup_integrity_error")
    return files


def restore(root, name, identity):
    source = bundle_path(root, name)
    value = instance(root, identity)
    with instance_lock(root, "backup-" + name), instance_lock(root, identity):
        if value.state.exists():
            raise ValueError("restore_requires_new_instance")
        files = verify(source)
        temp_root = inside(root, ".local", "tmp")
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="instance-restore-", dir=temp_root) as temporary:
            staging = Path(temporary)
            # Component restores may create SQLite sidecars or lock files. Preserve the bundle.
            for name in files:
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, target)
            value.state.mkdir(parents=True, mode=0o700)
            marker = value.state / ".restore-in-progress"
            marker.write_text("Do not start this instance until restore completes.")
            try:
                for rel in WORKBENCH:
                    data = staging / "workbench" / rel
                    if not data.exists():
                        continue
                    target = value.path("workbench") / rel
                    if data.suffix == ".db":
                        snapshot(data, target)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(data, target)
                run_component(root, "PaperReader", "paper_reader", "restore", staging / "reader", value.path("reader"))
                run_component(root, "GitHub-Wiki", "wiki_app", "restore", staging / "wiki", value.path("wiki"))
                with sqlite3.connect(value.path("workbench") / "assistant/assistant.db") as db:
                    db.execute("UPDATE turns SET state='interrupted',error='restored_without_retry' WHERE state IN ('queued','running')")
                with sqlite3.connect(value.path("wiki") / "wiki.db") as db:
                    db.execute("UPDATE jobs SET state='interrupted',error='restored_without_retry' WHERE state IN ('queued','running')")
                (value.state / "restore.json").write_text(json.dumps({
                    "format": 1, "credentials": "reenter_required", "automatic_retry": False}))
                marker.unlink()
            except BaseException:
                shutil.rmtree(value.state)
                raise
    return value.state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "restore"])
    parser.add_argument("--instance", required=True)
    parser.add_argument("--name", required=True, help="Copy-local backup name")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        result = (backup if args.operation == "backup" else lambda r, i, n: restore(r, n, i))(ROOT, args.instance, args.name)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps({"operation": args.operation, "path": str(result), "credentials": "excluded"}))


if __name__ == "__main__":
    main()
