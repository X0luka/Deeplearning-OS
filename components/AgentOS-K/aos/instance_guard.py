"""Cooperative lifecycle lock shared by development startup and maintenance."""
from contextlib import contextmanager
import fcntl
import json

from .desktop_instance import inside
from .adapters.session_process import boot_id, members


@contextmanager
def instance_lock(root, identity):
    folder = inside(root, ".local", "instance-locks")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = inside(root, ".local", "instance-locks", identity + ".lock")
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("instance_busy") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def assert_stopped(state):
    # Never stop or adopt anything while preparing a backup.
    directory = state / "workbench/desktop"
    lock = directory / "session.lock"
    if lock.exists():
        with lock.open("rb") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("instance_busy") from None
    for path in (directory / "processes").glob("*.json"):
        record = json.loads(path.read_text())
        if record.get("boot") == boot_id():
            known = {**record.get("members", {}), str(record["pid"]): record["start"]}
            current = members(record["group"])
            if any(current.get(pid) == start for pid, start in known.items()):
                raise ValueError("instance_processes_still_running")
