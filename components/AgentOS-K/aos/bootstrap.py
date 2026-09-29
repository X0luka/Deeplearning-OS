"""Generic CLI and composition root for locally selected product extensions."""
import argparse
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

import uvicorn

from aos.adapters.activity import ProductActivity
from aos.adapters.process import LocalProcesses, Service
from aos.adapters.sqlite import SQLiteArchive
from aos.application import ProductRuntime, SyncService
from aos.host.web import create_app
from aos.integrations import discover
from aos.llm.store import ModelStore
from aos.worker import run_worker


def parse_args(argv):
    parser = argparse.ArgumentParser(description="AgentOS local workbench")
    parser.add_argument("command", choices=["serve", "worker"])
    parser.add_argument("--products", default=os.getenv("AOS_PRODUCTS", "all"),
                        help="all, none, or comma-separated local extension names")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("AOS_DATA_DIR", "var")))
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--allow-query", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--unified-assistant", action="store_true", help="Opt in after isolated acceptance")
    parser.add_argument("--autostart", action="store_true")
    # Read selection without importing any product package in core-only mode.
    selection = argparse.ArgumentParser(add_help=False)
    selection.add_argument("--products", default=os.getenv("AOS_PRODUCTS", "all"))
    selected, _ = selection.parse_known_args(argv)
    try:
        providers = discover(selected.products)
    except ValueError as exc:
        parser.error(str(exc))
    for provider in providers:
        provider.configure(parser)
    return parser.parse_args(argv), providers


def worker_for(data_dir, options=()):
    root = Path(__file__).resolve().parents[1]
    return Service("worker", (sys.executable, "-m", "aos.bootstrap", "worker", *options,
                              "--data-dir", str(data_dir.resolve())),
                   data_dir.resolve(), "", "worker", {"PYTHONPATH": str(root)})


def assemble(args, providers, options=(), session_ledger=None):
    archive = SQLiteArchive(args.data_dir / "archive.db")
    entries = [entry for provider in providers if (entry := provider.create(args)) is not None]
    model_store = ModelStore(args.data_dir / 'llm')
    model_tasks = {e.product.id: e.model_tasks for e in entries if e.model_tasks}
    for entry in entries:
        if entry.model_tasks:
            env = {'AOS_UNIFIED_ASSISTANT': '1' if getattr(args,'unified_assistant',False) else '0', 'AOS_LLM_MODE': 'managed', 'AOS_LLM_URL': f'http://127.0.0.1:{args.port}',
                   'AOS_LLM_PRODUCT': entry.product.id,
                   'AOS_LLM_TOKEN': model_store.product_token(entry.product.id)}
            entry.services = [replace(s, env={**s.env, **env}) for s in entry.services]
    ids = [entry.product.id for entry in entries]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_product_extension")
    worker = worker_for(args.data_dir, options)
    registry, services = {}, {}
    for entry in entries:
        product = entry.product
        activity = ProductActivity(archive, product.id) if entry.manual_activation else None
        supervisor = entry.supervisor(entry.services, worker, args.data_dir.resolve())
        if session_ledger is not None:
            from aos.adapters.session_process import SessionProcesses
            supervisor = SessionProcesses(entry.services, worker, args.data_dir.resolve(),
                                          session_ledger)
        runtime = ProductRuntime(supervisor, activity, supervise_worker=False)
        registry[product.id] = (product, entry.adapter, runtime)
        services[product.id] = SyncService(product.id, entry.adapter, archive, activity)
    host = ProductRuntime(LocalProcesses([], worker, args.data_dir.resolve()),
                          supervise_worker=False) if entries else None
    if host and session_ledger is not None:
        host.supervisor = SessionProcesses([], worker, args.data_dir.resolve(), session_ledger)
    default = next((e for e in entries if e.preferred), entries[0] if entries else None)
    app = create_app(default.product if default else None, default.adapter if default else None,
                     archive, args.allow_query, runtime=host, registry=registry,
                     autostart=tuple(e.product.id for e in entries
                                     if e.autostart or session_ledger is not None),
                     startup_errors={e.product.id: e.startup_errors for e in entries},
                     sync_services=services,
                     clients=[e.client for e in entries], model_store=model_store, model_tasks=model_tasks,
                     assistant_providers={e.product.id:{"url":e.product.api_url.rstrip("/"),"name":e.product.name,"section":e.assistant_section,"runtime":registry[e.product.id][2]} for e in entries if e.assistant_section})
    app.state.worker_command = worker.command if entries else ()
    app.state.desktop = session_ledger is not None
    app.state.registry = registry
    return app, archive, services, entries


def main():
    argv = sys.argv[1:]
    args, providers = parse_args(argv)
    app, archive, services, entries = assemble(args, providers, argv[1:])
    if args.command == "serve":
        uvicorn.run(app, host="127.0.0.1", port=args.port)
    else:
        async def work():
            try:
                if services:
                    await run_worker(services, archive, args.data_dir / "worker.lock", args.once)
            finally:
                for entry in entries:
                    await entry.client.aclose()
        asyncio.run(work())


if __name__ == "__main__":
    main()
