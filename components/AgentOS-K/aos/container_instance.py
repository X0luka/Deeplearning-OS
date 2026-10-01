"""Container-only foreground owner; reuses developer instance data and lifecycle."""
import argparse
import asyncio
import os
from pathlib import Path
import secrets
from urllib.request import ProxyHandler, build_opener

import uvicorn

from aos.adapters.session_process import SessionLedger
from aos.bootstrap import assemble, parse_args
from aos.desktop_instance import check_ports, plan
from aos.instance_guard import instance_lock


def configuration():
    root = Path(__file__).resolve().parents[3]
    ports = tuple(int(os.getenv(key, default)) for key, default in
                  (("DLOS_HOST_PORT", "18877"), ("DLOS_WIKI_PORT", "18878"),
                   ("DLOS_PAPER_PORT", "18879")))
    return plan(root, "dev-docker", ports)


def healthy():
    value = configuration()
    opener = build_opener(ProxyHandler({}))
    for port, path in zip(value.ports, ("/api/v1/identity", "/aos/v1/describe", "/aos/v1/describe")):
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
            if response.status != 200:
                return False
    return True


async def serve(value):
    options = [*value.arguments(), "--listen-host", "0.0.0.0"]
    args, providers = parse_args(["serve", *options])
    ledger = SessionLedger(value.path("workbench") / "container", secrets.token_hex(16),
                           grace=30, reject_external=True)
    await ledger.recover()
    app, _, _, _ = assemble(args, providers, options, session_ledger=ledger)
    app.state.desktop = False
    server = uvicorn.Server(uvicorn.Config(app, host=args.listen_host, port=args.port,
                                           access_log=False, log_level="warning"))
    try:
        await server.serve()
    finally:
        await ledger.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    if args.healthcheck:
        try:
            raise SystemExit(0 if healthy() else 1)
        except (OSError, ValueError):
            raise SystemExit(1) from None
    if not Path("/.dockerenv").exists():
        raise SystemExit("This entry is for Docker only. Use the developer desktop entry locally.")
    value = configuration()
    check_ports(value.ports)
    os.umask(0o077)
    with instance_lock(value.root, value.instance):
        for name in ("workbench", "wiki", "reader", "home", "tmp", "cache", "config", "locks"):
            value.path(name).mkdir(parents=True, exist_ok=True, mode=0o700)
        # User keys are configured in the UI; never inherit host/provider credentials.
        environment = value.environment()
        os.environ.clear()
        os.environ.update(environment)
        os.chdir(value.root / "components/AgentOS-K")
        asyncio.run(serve(value))


if __name__ == "__main__":
    main()
