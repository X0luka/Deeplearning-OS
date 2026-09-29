"""Foreground desktop session. Private stdin/stdout is the only control channel."""
import asyncio
import fcntl
import json
import os
import secrets
import signal
import socket
import sys

import httpx
import uvicorn

from aos.adapters.session_process import SessionLedger
from aos.bootstrap import assemble, parse_args


async def run(argv, *, reject_external=False):
    os.umask(0o077)
    # Existing installed desktop launchers also opt into the accepted assistant.
    argv = [*argv, '--unified-assistant'] if '--unified-assistant' not in argv else argv
    args, providers = parse_args(['serve', *argv])
    directory = args.data_dir.resolve() / 'desktop'
    directory.mkdir(parents=True, exist_ok=True)
    log = (directory / 'session.log').open('a', buffering=1)

    def emit(**event):
        line = json.dumps(event, ensure_ascii=True)
        log.write(line + '\n')
        try:
            print(line, flush=True)
        except BrokenPipeError:
            pass

    lock = (directory / 'session.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        emit(event='error', message='另一个桌面会话仍在运行或清理，请稍后重试。')
        return
    reader = asyncio.StreamReader(limit=8192)
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
    try:
        hello = json.loads(await asyncio.wait_for(reader.readline(), 15))
        token = hello.get('token', '')
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError('invalid session credential')
        session = secrets.token_hex(16)
        ledger = SessionLedger(directory / 'processes', session, emit, reject_external=reject_external)
        await ledger.recover()
        # Reserve before constructing the archive/worker. Never take over a preexisting host.
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(('127.0.0.1', args.port))
        except OSError:
            listener.close()
            emit(event='error', message='工作台端口已被占用，请先关闭原工作台后重试。')
            return
        app, _, _, entries = assemble(args, providers, argv, session_ledger=ledger)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port,
                                              log_level='warning', access_log=False,
                                              timeout_graceful_shutdown=30))
        # Compatibility listeners share this process and stop with the desktop.
        from aos.assistant.redirects import redirect_app
        redirects = []
        try:
            for port, target in ([(8777, args.port), (8702, 8602), (8701, 8601)] if args.port == 7777 else []):
                sock = socket.socket()
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(('127.0.0.1', port))
                except OSError:
                    sock.close()
                    raise
                redirect = uvicorn.Server(uvicorn.Config(redirect_app(f'http://127.0.0.1:{target}'),
                    log_level='warning', access_log=False))
                redirects.append((redirect, sock))
        except OSError:
            for _, sock in redirects:
                sock.close()
            listener.close()
            emit(event='error', message='旧试用端口被占用，请停止旧试用进程后重试；未接管未知进程。')
            return
        exit_requested = asyncio.Event()

        async def control():
            while not exit_requested.is_set():
                raw = await reader.readline()
                if not raw:  # Shell crash / handle closure: identical owned-process cleanup.
                    exit_requested.set()
                    break
                try:
                    message = json.loads(raw)
                    if not secrets.compare_digest(str(message.get('token', '')), token):
                        emit(event='denied')
                        continue
                    if message.get('action') == 'exit':
                        exit_requested.set()
                    elif message.get('action') == 'prepare':
                        busy = []
                        if app.state.assistant.tasks:
                            busy.append("平台统一助手")
                        for entry in entries:
                            rt = app.state.registry[entry.product.id][2]
                            if rt.supervisor.ownership == 'external':
                                continue
                            if not any(ledger.valid_members(r) for r in rt.supervisor.owned):
                                continue
                            try:
                                response = await entry.client.get('/health', timeout=3)
                                value = response.json()
                                if value.get('busy', True):
                                    busy.append(entry.product.name)
                            except (httpx.HTTPError, ValueError, AttributeError):
                                busy.append(entry.product.name)
                        emit(event='prepared', busy=busy)
                    elif message.get('action') == 'status':
                        emit(event='status', session=session,
                             products={k: v[2].snapshot() for k, v in app.state.registry.items()})
                except (ValueError, AttributeError):
                    emit(event='denied')

        async def monitor():
            announced = False
            ticks = 0
            while not exit_requested.is_set():
                ledger.refresh()
                if server.started and not announced:
                    announced = True
                    emit(event='ready', url=f'http://127.0.0.1:{args.port}/',
                         products=[e.product.ui_url for e in entries],
                         product_routes={e.product.id: e.product.ui_url for e in entries})
                ticks += 1
                if ticks % 4 == 0:
                    for _, _, rt in app.state.registry.values():
                        if rt.state['state'] == 'ready':
                            for service in rt.supervisor.services:
                                if not await rt.supervisor.probe(service):
                                    rt.state.update(state='failed', error='service_unreachable')
                                    break
                await asyncio.sleep(.5)

        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, exit_requested.set)
        serve = asyncio.create_task(server.serve(sockets=[listener]))
        redirect_tasks = [asyncio.create_task(s.serve(sockets=[sock])) for s, sock in redirects]
        controls = asyncio.create_task(control())
        monitoring = asyncio.create_task(monitor())
        exiting = asyncio.create_task(exit_requested.wait())
        try:
            await asyncio.wait([serve, controls, exiting], return_when=asyncio.FIRST_COMPLETED)
        finally:
            emit(event='closing')
            exit_requested.set()
            server.should_exit = True
            for redirect, _ in redirects:
                redirect.should_exit = True
            await asyncio.gather(serve, *redirect_tasks)
            for _, sock in redirects:
                sock.close()
            # Covers partial startup and exceptions before lifespan cleanup.
            await ledger.close()
            for task in (controls, monitoring, exiting):
                task.cancel()
            await asyncio.gather(controls, monitoring, exiting, return_exceptions=True)
            listener.close()
            emit(event='closed')
    except (ValueError, OSError, TimeoutError) as exc:
        emit(event='error', message=str(exc))
    finally:
        transport.close()
        lock.close()
        log.close()


if __name__ == '__main__':
    asyncio.run(run(sys.argv[1:]))
