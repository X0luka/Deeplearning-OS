"""Development desktop entry point with explicit, copy-local instance state."""
import argparse
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
import socket

RESERVED_PORTS = {7777, 8601, 8602, 8777, 8701, 8702}


def inside(root, *parts):
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Instance paths must not contain symbolic links")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Instance path escapes the preparation copy")
    return path


@dataclass(frozen=True)
class Instance:
    root: Path
    instance: str
    ports: tuple[int, int, int]

    @property
    def state(self):
        return inside(self.root, ".local", "instances", self.instance)

    def path(self, name):
        return inside(self.root, ".local", "instances", self.instance, name)

    def arguments(self):
        host, wiki, paper = self.ports
        return ["--products", "wiki,paper", "--port", str(host),
                "--data-dir", str(self.path("workbench")),
                "--wiki-dir", str(self.root / "components/GitHub-Wiki"),
                "--wiki-url", f"http://127.0.0.1:{wiki}", "--wiki-data-dir", str(self.path("wiki")),
                "--paper-dir", str(self.root / "components/PaperReader"),
                "--paper-url", f"http://127.0.0.1:{paper}", "--paper-data-dir", str(self.path("reader")),
                "--unified-assistant"]

    def environment(self):
        env = {"PATH": str(self.root / ".local/tools/node/bin") + ":/usr/bin:/bin",
                "HOME": str(self.path("home")), "TMPDIR": str(self.path("tmp")),
                "XDG_CACHE_HOME": str(self.path("cache")), "XDG_CONFIG_HOME": str(self.path("config")),
                "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "AOS_RUNTIME_LOCK_DIR": str(self.path("locks")),
                "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                "RAYON_NUM_THREADS": "1", "NODE_OPTIONS": "--max-old-space-size=256"}
        prepared = inside(self.root, ".local", "pdf-cpu", "ready.json")
        if prepared.is_file():
            assets = inside(self.root, ".local", "pdf-cpu", "models")
            binaries = inside(self.root, ".local", "pdf-cpu", "venv", "bin")
            python = binaries / "python"
            if not python.is_file() or not assets.is_dir():
                raise ValueError("Prepared PDF parser environment is incomplete")
            # uv's interpreter symlink is expected, but must target the system Python.
            if python.is_symlink() and python.resolve() != Path("/usr/bin/python3").resolve():
                raise ValueError("Unexpected PDF parser interpreter target")
            env.update(PAPER_DOCLING_PYTHON=str(python), DOCLING_ARTIFACTS_PATH=str(assets),
                       DOCLING_CACHE_DIR=str(self.path("cache") / "docling"),
                       HF_HOME=str(self.path("cache") / "huggingface"),
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        return env


def plan(root, instance, ports):
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError("Preparation root must not contain symbolic links")
    if not re.fullmatch(r"dev-[a-z0-9][a-z0-9-]{0,35}", instance):
        raise ValueError("Use a development instance ID such as dev-alpha")
    if len(ports) != 3 or any(type(p) is not int or not 1024 <= p <= 65535 or p in RESERVED_PORTS for p in ports) or len(set(ports)) != 3:
        raise ValueError("Choose three distinct unprivileged, non-production ports")
    marker = inside(root, ".local", "preparation", "capture-complete.json")
    if not marker.is_file() and not inside(root, ".local", "development.json").is_file():
        raise ValueError("Initialize the developer checkout with scripts/init_development.py")
    for component in ("AgentOS-K", "PaperReader", "GitHub-Wiki"):
        if not inside(root, "components", component).is_dir():
            raise ValueError("Incomplete preparation copy")
    value = Instance(root, instance, tuple(ports))
    for name in ("workbench", "wiki", "reader", "home", "tmp", "cache", "config", "locks"):
        value.path(name)
    if inside(root, ".local", "instances", instance, ".restore-in-progress").exists():
        raise ValueError("instance_restore_incomplete")
    return value


def check_ports(ports):
    # Preflight is advisory; the supervisors also reject external service adoption.
    listeners = []
    try:
        for port in ports:
            listener = socket.socket()
            listeners.append(listener)
            listener.bind(("127.0.0.1", port))
    except OSError as exc:
        raise ValueError("An instance port is occupied; choose another port, do not stop the other service") from exc
    finally:
        for listener in listeners:
            listener.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--host-port", type=int, required=True)
    parser.add_argument("--wiki-port", type=int, required=True)
    parser.add_argument("--paper-port", type=int, required=True)
    args = parser.parse_args()
    try:
        value = plan(Path(__file__).resolve().parents[3], args.instance,
                     (args.host_port, args.wiki_port, args.paper_port))
        check_ports(value.ports)
    except ValueError as exc:
        parser.error(str(exc))
    from aos.instance_guard import instance_lock
    try:
        with instance_lock(value.root, value.instance):
            os.umask(0o077)
            for name in ("workbench", "wiki", "reader", "home", "tmp", "cache", "config", "locks"):
                value.path(name).mkdir(parents=True, exist_ok=True, mode=0o700)
            os.environ.clear()
            os.environ.update(value.environment())
            os.chdir(value.root / "components/AgentOS-K")
            from aos.desktop_session import run
            asyncio.run(run(value.arguments(), reject_external=True))
    except ValueError as exc:
        if str(exc) != "instance_busy":
            raise
        import json
        print(json.dumps({"event": "error", "message": "另一个桌面会话或备份恢复操作仍在运行，请稍后重试。"}), flush=True)



if __name__ == "__main__":
    main()
