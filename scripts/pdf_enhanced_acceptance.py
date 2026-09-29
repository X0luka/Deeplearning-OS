"""Actual enhanced parsing with a controlled checkpoint pause; no model API calls."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"components/PaperReader"))
from paper_reader.reading_parse import parse
folder=ROOT/".local/preparation/enhanced-pdf-job"
folder.mkdir(exist_ok=True)
pdf=ROOT/".local/fixtures/arxiv-1706.03762.pdf"
assert pdf.exists()
os.environ.update(DOCLING_ARTIFACTS_PATH=str(ROOT/".local/pdf-cpu/models"),
    DOCLING_CACHE_DIR=str(ROOT/".local/pdf-cpu/cache"),HF_HOME=str(ROOT/".local/pdf-cpu/hf"),
    HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1")
ledger=folder/"parse-progress.json"
paused=folder/"controlled-pause.json"
started=time.monotonic()
def checkpoint():
    if ledger.exists() and not paused.exists():
        state=json.loads(ledger.read_text())
        if state.get("chunks"):
            first=state["chunks"][0]
            files={name:{"sha256":digest,"mtime_ns":(folder/first["folder"]/name).stat().st_mtime_ns}
                   for name,digest in first["files"].items()}
            paused.write_text(json.dumps({"folder":first["folder"],"files":files}))
            raise RuntimeError("controlled_pause_after_verified_chunk")
try:
    parse(pdf,folder,"enhanced-public-paper",str(ROOT/".local/pdf-cpu/venv/bin/python"),checkpoint)
except RuntimeError as exc:
    if str(exc)!="controlled_pause_after_verified_chunk":
        raise
    print("Controlled pause after a verified chunk; resuming from saved state",flush=True)
    parse(pdf,folder,"enhanced-public-paper",str(ROOT/".local/pdf-cpu/venv/bin/python"),checkpoint)
state=json.loads(ledger.read_text())
assert state["status"]=="completed" and state["done"]==state["total"]==15
saved=json.loads(paused.read_text())
for name,expected in saved["files"].items():
    path=folder/saved["folder"]/name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected["sha256"]
    assert path.stat().st_mtime_ns==expected["mtime_ns"]
report={"status":"passed","pages":state["done"],"chunks":len(state["chunks"]),
    "profiles":[c["profile"] for c in state["chunks"]],"checkpoint_reused_without_reparse":True,
    "events":state["events"],"seconds":round(time.monotonic()-started,2),"model_api_calls":0}
(ROOT/".local/preparation/enhanced-pdf-acceptance.json").write_text(json.dumps(report,indent=2))
print(json.dumps(report))
