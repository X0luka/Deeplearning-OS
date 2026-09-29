"""Whole-instance acceptance using synthetic stores and independent component CLIs."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import uuid

import pytest

from aos.adapters.sqlite import SQLiteArchive
from aos.assistant.store import Store as AssistantStore
from aos.desktop_instance import plan
from aos.instance_guard import instance_lock
from aos.llm.store import ModelStore
from aos.llm.catalog import model_item

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("instance_backup_tool", ROOT / "scripts/instance_backup.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


@pytest.fixture
def sample():
    suffix = uuid.uuid4().hex[:12]
    identity = "dev-backup-" + suffix
    value = plan(ROOT, identity, (18777, 18601, 18602))
    value.state.mkdir(parents=True)
    SQLiteArchive(value.path("workbench") / "archive.db")
    assistant = AssistantStore(value.path("workbench") / "assistant/assistant.db")
    conversation = assistant.create("合成会话", "general", [])
    with assistant.db() as db:
        db.execute("INSERT INTO turns(id,conversation,question,answer,state) VALUES(?,?,?,?,?)",
                   ("synthetic-turn", conversation["id"], "synthetic question", "partial answer", "running"))
    models = ModelStore(value.path("workbench") / "llm")
    models.save_connection({"name":"Synthetic", "protocol":"chat", "base_url":"https://model.invalid/v1", "key":"DO-NOT-BACKUP-SYNTHETIC-KEY"})
    connection = models.read()["connections"][0]["id"]
    models.mutate(lambda state: state["connections"][0]["models"].append(model_item("fixture")))
    models.settings("global", {"selection":{"connection":connection,"model":"fixture"},"custom":"synthetic preference"})
    scripts = {
        "PaperReader": """from pathlib import Path
import hashlib,sys
from paper_reader.reader_store import ReaderStore
s=ReaderStore(Path(sys.argv[1]))
raw=b'<html><body>Synthetic paper only</body></html>'
identity=hashlib.sha256(raw).hexdigest()
(s.root/'raw'/(identity+'.html')).write_bytes(raw)
with s.db() as db: db.execute('INSERT INTO documents VALUES(?,?,?,?,?)',(identity,'synthetic.html','registered',None,None))
""",
        "GitHub-Wiki": """from pathlib import Path
import sys
from wiki_app.store import Store
s=Store(Path(sys.argv[1]))
(s.root/'notes'/'synthetic.md').write_text('Synthetic note only')
with s.db() as db: db.execute('INSERT INTO jobs(id,kind,args,state) VALUES(?,?,?,?)',('synthetic-job','fixture','{}','queued'))
"""
    }
    for component, code in scripts.items():
        data = value.path("reader" if component == "PaperReader" else "wiki")
        subprocess.run([str(ROOT / "components" / component / ".venv/bin/python"), "-c", code, str(data)],
                       env={"PATH":"/usr/bin:/bin","PYTHONDONTWRITEBYTECODE":"1"}, cwd=ROOT/"components"/component, check=True)
    return value, "bundle-" + suffix, "dev-restored-" + suffix


def test_whole_instance_roundtrip_without_credentials_or_replay(sample):
    value, name, restored_id = sample
    saved = bundle.backup(ROOT, value.instance, name)
    before = bundle.inventory(saved)
    assert not any("private" in Path(n).parts for n in before)
    assert not any(b"DO-NOT-BACKUP-SYNTHETIC-KEY" in (saved/n).read_bytes() for n in before)
    restored = bundle.restore(ROOT, name, restored_id)
    assert bundle.inventory(saved) == before
    assert not (restored/"workbench/llm/private").exists()
    with sqlite3.connect(restored/"workbench/assistant/assistant.db") as db:
        assert db.execute("SELECT title FROM conversations").fetchone()[0] == "合成会话"
        assert db.execute("SELECT state,error,answer FROM turns").fetchone() == ("interrupted","restored_without_retry","partial answer")
    with sqlite3.connect(restored/"wiki/wiki.db") as db:
        assert db.execute("SELECT state FROM jobs").fetchone()[0] == "interrupted"
    assert (restored/"wiki/notes/synthetic.md").read_text() == "Synthetic note only"
    assert len(list((restored/"reader/raw").glob("*.html"))) == 1
    source_models = ModelStore(value.path("workbench")/"llm")
    restored_models = ModelStore(restored/"workbench/llm")
    identity = restored_models.read()["connections"][0]["id"]
    reference = restored_models.read()["connections"][0].get("credential_ref",identity)
    assert not restored_models.secret(reference)
    assert restored_models.read()["defaults"] == source_models.read()["defaults"]
    with pytest.raises(ValueError, match="model_key_not_configured"):
        restored_models.resolve("assistant", "chat", include_secret=True)
    assert source_models.secret(source_models.read()["connections"][0].get("credential_ref",identity)) == "DO-NOT-BACKUP-SYNTHETIC-KEY"
    assert json.loads((restored/"restore.json").read_text())["credentials"] == "reenter_required"


def test_busy_instance_is_not_backed_up(sample):
    value, name, _ = sample
    with instance_lock(ROOT, value.instance):
        with pytest.raises(ValueError, match="instance_busy"):
            bundle.backup(ROOT, value.instance, name)
        result = subprocess.run([str(ROOT/"components/AgentOS-K/.venv/bin/python"),
            "-m", "aos.desktop_instance", "--instance", value.instance, "--host-port", "18777",
            "--wiki-port", "18601", "--paper-port", "18602"],
            cwd=ROOT/"components/AgentOS-K", env={"PATH":"/usr/bin:/bin","PYTHONDONTWRITEBYTECODE":"1"},
            capture_output=True,text=True,timeout=10)
        assert json.loads(result.stdout)["event"] == "error"
        assert "备份恢复操作" in json.loads(result.stdout)["message"]
    assert not bundle.bundle_path(ROOT,name).exists()


def test_corrupt_bundle_never_creates_destination(sample):
    value, name, restored_id = sample
    saved = bundle.backup(ROOT,value.instance,name)
    (saved/"wiki/notes/synthetic.md").write_text("tampered")
    with pytest.raises(ValueError,match="integrity"):
        bundle.restore(ROOT,name,restored_id)
    assert not plan(ROOT,restored_id,(18777,18601,18602)).state.exists()


def test_existing_instance_is_never_overwritten(sample):
    value, name, _ = sample
    bundle.backup(ROOT,value.instance,name)
    before=(value.path("workbench")/"archive.db").read_bytes()
    with pytest.raises(ValueError,match="new_instance"):
        bundle.restore(ROOT,name,value.instance)
    assert (value.path("workbench")/"archive.db").read_bytes()==before


def test_restore_component_failure_cleans_new_instance_preserves_bundle(sample,monkeypatch):
    value,name,restored_id=sample
    saved=bundle.backup(ROOT,value.instance,name)
    before=bundle.inventory(saved)
    def fail(*args):
        raise ValueError("simulated_component_restore_failure")
    monkeypatch.setattr(bundle,"run_component",fail)
    with pytest.raises(ValueError,match="simulated"):
        bundle.restore(ROOT,name,restored_id)
    assert not plan(ROOT,restored_id,(18777,18601,18602)).state.exists()
    assert bundle.inventory(saved)==before


def test_incomplete_restore_cannot_start(sample):
    _,_,identity=sample
    value=plan(ROOT,identity,(18777,18601,18602))
    value.state.mkdir()
    (value.state/".restore-in-progress").write_text("interrupted")
    with pytest.raises(ValueError,match="restore_incomplete"):
        plan(ROOT,identity,(18777,18601,18602))


def test_injected_private_file_is_rejected_before_restore(sample):
    import hashlib
    value,name,identity=sample
    saved=bundle.backup(ROOT,value.instance,name)
    private=saved/"workbench/llm/private/forbidden"
    private.parent.mkdir()
    private.write_text("synthetic injected key")
    metadata=json.loads((saved/"bundle.json").read_text())
    metadata["files"]["workbench/llm/private/forbidden"]=hashlib.sha256(private.read_bytes()).hexdigest()
    (saved/"bundle.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match="invalid_instance_backup_paths"):
        bundle.restore(ROOT,name,identity)
    assert not plan(ROOT,identity,(18777,18601,18602)).state.exists()


def test_live_orphan_process_record_prevents_backup(sample):
    import os
    from aos.adapters.process import identity as process_identity
    from aos.adapters.session_process import boot_id
    value,name,_=sample
    directory=value.path("workbench")/"desktop/processes"
    directory.mkdir(parents=True)
    (directory/"synthetic.json").write_text(json.dumps({"pid":os.getpid(),"group":os.getpgrp(),
        "start":process_identity(os.getpid()),"boot":boot_id(),"members":{}}))
    with pytest.raises(ValueError,match="processes_still_running"):
        bundle.backup(ROOT,value.instance,name)
    assert not bundle.bundle_path(ROOT,name).exists()
