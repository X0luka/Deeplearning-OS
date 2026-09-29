"""Real Wiki generation acceptance; synthetic source only, original product contracts."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for name in ("AgentOS-K", "GitHub-Wiki"):
    sys.path.insert(0, str(ROOT / "components" / name))
FOLDER = ROOT / ".local/preparation/wiki-generation"
FILES = {
    "README.md": """# Checkpoint demo
A synthetic Python command-line example for resumable document processing.
Run python -m demo.main. Run python -m unittest discover -s tests.
Each validated page is persisted in a JSON checkpoint using a same-directory
temporary file followed by atomic replacement. A restart reads that checkpoint
and skips already completed pages. Invalid pages are not marked completed.
This example does not parse PDFs, call models, handle multiple writers, or
guarantee power-loss durability: it does not call fsync.
Translation status 'uncertain' must not be automatically retried; provider
billing is unknown after a timeout. Only 'not_sent' is automatically eligible.
""",
    "demo/__init__.py": "",
    "demo/checkpoint.py": """import json
from pathlib import Path

def load(path):
    path = Path(path)
    return set(json.loads(path.read_text())) if path.exists() else set()

def save(path, done):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(sorted(done)))
    temp.replace(path)

def accept(path, done, page, valid):
    if valid:
        updated = done | {page}
        save(path, updated)
        return updated
    return done
""",
    "demo/main.py": """from pathlib import Path
from .checkpoint import load, accept
from .billing import retry_allowed

def run(path, pages, validate):
    done = load(path)
    for page in pages:
        if page not in done:
            done = accept(path, done, page, validate(page))
    return done

if __name__ == '__main__':
    print(sorted(run(Path('checkpoint.json'), [1, 2, 3], lambda page: page != 2)))
    print('retry uncertain:', retry_allowed('uncertain'))
""",
    "demo/billing.py": """def retry_allowed(state):
    return state == 'not_sent'
""",
    "tests/test_checkpoint.py": """import tempfile
import unittest
from pathlib import Path
from demo.main import run
from demo.billing import retry_allowed

class CheckpointTest(unittest.TestCase):
    def test_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'progress.json'
            self.assertEqual(run(path, [1, 2, 3], lambda p: p != 2), {1, 3})
            visited = []
            def validate(page):
                visited.append(page)
                return True
            self.assertEqual(run(path, [1, 2, 3], validate), {1, 2, 3})
            self.assertEqual(visited, [2])
    def test_uncertain(self):
        self.assertFalse(retry_allowed('uncertain'))
        self.assertTrue(retry_allowed('not_sent'))
""",
}
def write(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)

async def main(args):
    assert os.environ.get("DLOS_ISOLATED_TEST") == "1"
    os.umask(0o077)
    from aos.llm.store import ModelStore
    from wiki_app import engine, llm_client, model_config
    from wiki_app.architecture import knowledge, auto_diagrams
    from wiki_app.store import Store
    settings = ModelStore(ROOT / ".local/instances/dev-api-settings/workbench/llm").resolve("assistant", "chat", include_secret=True)
    if settings["base_url"] != "https://api.deepseek.com" or settings["model"] != "deepseek-flash" or settings.get("custom"):
        raise ValueError("unexpected_test_configuration")
    cfg = {**settings, "source": "agentos", "protocol": "openai", "enabled": True, "key_env": "", "max_calls": None}
    FOLDER.mkdir(exist_ok=True)
    ledger_path = FOLDER / "ledger.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {"material": "synthetic checkpoint demo", "calls": [], "stages": {}}
    if args.stage in ledger["stages"] and not args.resume:
        raise ValueError("stage_already_attempted_no_automatic_replay")
    if not 1 <= args.max_requests <= 160 or (args.max_requests > 80 and not args.reason):
        raise ValueError("batch_extension_requires_review")
    if args.resume and not args.reason:
        raise ValueError("resume_requires_diagnosis")
    if args.dry_run:
        print(json.dumps({"stage":args.stage,"files":len(FILES),"model":cfg["model"],"max_output_tokens":cfg["max_output_tokens"],"thinking":cfg["thinking"],"calls":0}))
        return
    store = Store(FOLDER / "data")
    with store.db() as db:
        db.execute("INSERT OR IGNORE INTO repos VALUES(?,?,?,?)", ("synthetic", "https://github.com/example/checkpoint-demo", "main", None))
    def config(*a, **kw):
        return cfg
    engine.config = model_config.config = knowledge.config = auto_diagrams.config = config
    original_complete = llm_client.complete
    halted = False
    async def measured(*a, **kw):
        nonlocal halted
        if halted or len(ledger["calls"]) >= args.max_requests:
            raise ValueError("acceptance_batch_review_required")
        call = {"stage":args.stage,"state":"reserved","started":time.time()}
        ledger["calls"].append(call); write(ledger_path, ledger)
        try:
            message = await original_complete(*a, **kw)
            call.update(state="completed", answer_chars=len(message.get("content") or ""), usage_available=False)
            return message
        except Exception as exc:
            halted = True
            call.update(state="failed",error=type(exc).__name__)
            if isinstance(exc, ValueError) and str(exc).replace("_","").isalnum():
                call["code"] = str(exc)[:100]
            raise
        finally:
            call["seconds"] = round(time.time()-call["started"],2)
            write(ledger_path,ledger)
            print(json.dumps({"call":len(ledger["calls"]),**call}),flush=True)
    llm_client.complete = measured
    async def fetch(url, ref):
        sha = hashlib.sha256(json.dumps(FILES,sort_keys=True).encode()).hexdigest()[:40]
        manifest = {"files":[{"path":p,"lines":len(t.splitlines()),"sha256":hashlib.sha256(t.encode()).hexdigest()} for p,t in FILES.items()],
                    "included":len(FILES),"bytes":sum(len(t.encode()) for t in FILES.values()),"skipped":[]}
        return sha, dict(FILES), manifest
    if args.stage in ledger["stages"]:
        ledger.setdefault("stage_history", []).append({"stage":args.stage, **ledger["stages"][args.stage]})
    record = {"state":"running","reason":args.reason,"batch_request_ceiling":args.max_requests}
    ledger["stages"][args.stage] = record; write(ledger_path,ledger)
    try:
        if args.stage == "build":
            job = store.enqueue("build", {"repo":"synthetic"})
        else:
            rows = store.rows("SELECT id FROM jobs WHERE kind=? AND state='queued' ORDER BY created", (args.stage,))
            if rows:
                job = rows[0]["id"]
            elif args.resume:
                version = store.repo("synthetic")["current"]
                job = store.enqueue(args.stage, {"version":version})
            else:
                raise ValueError("expected_followup_job_missing")
        row = store.claim()
        if not row or row["id"] != job:
            raise ValueError("unexpected_queued_job")
        record["job"] = job; write(ledger_path,ledger)
        result = await engine.Engine(store, fetcher=fetch).run(row)
        result = engine.schedule_followup(store,args.stage,result)
        store.finish(job,"succeeded",result)
        reopened = Store(store.root)
        assert reopened.job(job)["result"] == result
        record.update(state="completed",result=result,reopened_equal=True)
        print(json.dumps(record,ensure_ascii=False),flush=True)
    except BaseException as exc:
        if record.get("job"):
            store.finish(record["job"],"failed",error=type(exc).__name__)
        record.update(state="failed",error=type(exc).__name__)
        raise
    finally:
        write(ledger_path,ledger)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["build","architecture","knowledge","page_diagrams"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-requests", type=int, default=80)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reason", default="")
    asyncio.run(main(parser.parse_args()))
