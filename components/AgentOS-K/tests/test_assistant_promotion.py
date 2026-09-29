import json

import httpx
import pytest

from aos.assistant.redirects import redirect_app
from aos.assistant.store import Store
from aos.assistant.transfer import transfer


def test_transfer_atomic_idempotent_and_preserves_edited_destination(tmp_path):
    src=Store(tmp_path/"source.db"); dst=Store(tmp_path/"dest.db")
    c=src.create("source","general",[])
    tid,_=src.enqueue(c["id"],"unique-request", "question", {})
    src.update(tid,state="completed",answer="answer")
    src.save_evidence(tid,[{"id":"E1","text":"source"}])
    report=tmp_path/"report.json"
    assert transfer(src.path,dst.path,report)["turns"] == 1
    assert dst.turn(tid)["answer"] == "answer"
    with dst.db() as db: db.execute("UPDATE conversations SET title='edited' WHERE id=?",(c["id"],))
    assert not any(transfer(src.path,dst.path,report).values())
    assert dst.conversation(c["id"])["title"] == "edited"
    src.update(tid,answer="changed")
    with pytest.raises(ValueError,match="conflict"): transfer(src.path,dst.path,report)
    assert dst.turn(tid)["answer"] == "answer"
    assert json.loads(report.read_text())["conflicts"]


def test_transfer_collision_rolls_back_and_busy_blocks(tmp_path):
    src=Store(tmp_path/"s.db");dst=Store(tmp_path/"d.db");r=tmp_path/"r.json"
    c=src.create("one","general",[])
    with src.db() as db: row=db.execute("SELECT * FROM conversations").fetchone()
    with dst.db() as db: db.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?,?,?,?)",tuple(row));db.execute("UPDATE conversations SET title='other'")
    src.create("two","general",[])
    with pytest.raises(ValueError,match="conflict"):transfer(src.path,dst.path,r)
    assert len(dst.rows("SELECT * FROM conversations")) == 1
    src.enqueue(c["id"],"some-request","q",{})
    with pytest.raises(ValueError,match="busy"):transfer(src.path,dst.path,r)


async def test_legacy_redirect_never_replays_writes():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=redirect_app("http://127.0.0.1:8602")),base_url="http://test") as c:
        r=await c.get('/?assistant_document=one')
        assert 'http://127.0.0.1:8602/?assistant_document=one' in r.text
        assert 'location.hash' in r.text
        assert (await c.post('/api/ask',json={"question":"test"})).status_code==410


async def test_redirect_escapes_script_end():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=redirect_app("http://127.0.0.1:8602")),base_url="http://test") as c:
        r=await c.get('/%3C/script%3E')
        assert r.text.count('</script>') == 1


async def test_desktop_private_pipe_two_cycles_and_crash_recovery(tmp_path):
    import asyncio
    import socket
    import sys
    from pathlib import Path

    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    token="isolated-desktop-control-token-123456789"
    store=Store(tmp_path/"assistant/assistant.db")
    conversation=store.create("preserved","general",[])

    async def launch():
        process=await asyncio.create_subprocess_exec(sys.executable,"-m","aos.desktop_session",
            "--products","none","--data-dir",str(tmp_path),"--port",str(port),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            cwd=Path(__file__).resolve().parents[1])
        process.stdin.write((json.dumps({"token":token})+"\n").encode());await process.stdin.drain()
        while True:
            line=await asyncio.wait_for(process.stdout.readline(),20)
            assert line, (await process.stderr.read()).decode()
            event=json.loads(line)
            assert event["event"]!="error",event
            if event["event"]=="ready":return process

    for _ in range(2):
        process=await launch()
        process.stdin.write((json.dumps({"token":token,"action":"prepare"})+"\n").encode());await process.stdin.drain()
        event=json.loads(await asyncio.wait_for(process.stdout.readline(),10));assert event=={"event":"prepared","busy":[]}
        process.stdin.write((json.dumps({"token":token,"action":"exit"})+"\n").encode());await process.stdin.drain()
        stdout,stderr=await asyncio.wait_for(process.communicate(),20)
        assert process.returncode==0,stderr.decode()
        assert b'"event": "closed"' in stdout
        assert store.conversation(conversation["id"])["title"]=="preserved"
        with socket.socket() as sock:assert sock.connect_ex(("127.0.0.1",port))!=0

    process=await launch()
    turn,_=store.enqueue(conversation["id"],"synthetic-no-model","synthetic",{})
    store.update(turn,state="running")
    process.kill();await process.communicate()
    process=await launch()
    assert store.turn(turn)["state"]=="interrupted"
    assert store.turn(turn)["error"]=="service_restarted"
    assert len(store.turns(conversation["id"]))==1
    process.stdin.write((json.dumps({"token":token,"action":"exit"})+"\n").encode());await process.stdin.drain()
    await asyncio.wait_for(process.communicate(),20)
