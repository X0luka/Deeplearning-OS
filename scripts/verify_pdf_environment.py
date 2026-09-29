"""Verify installed CPU parser assets before enabling them for desktop instances."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
parser=argparse.ArgumentParser()
parser.add_argument("--require-acceptance", action="store_true")
args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1]
local=ROOT/".local/pdf-cpu"
def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()
lock=ROOT/"requirements/pdf-cpu-py312-linux.txt"
versions={}
for name,version in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)",lock.read_text(),re.M):
    actual=importlib.metadata.version(name)
    if actual!=version:
        raise ValueError("Parser dependency version mismatch: "+name)
    versions[name]=actual
downloaded=json.loads((ROOT/".local/preparation/pdf-assets-downloaded.json").read_text())
models=json.loads((ROOT/"assets/pdf-models.lock.json").read_text())
for repo in models:
    for item in repo["files"]:
        key=repo["repo"]+"/"+item["name"]
        saved=downloaded[key]
        if saved["revision"]!=repo["revision"]:
            raise ValueError("Model revision mismatch")
        path=local/"models"/repo["repo"].replace("/","--")/item["name"]
        expected=item["lfs_sha256"] or saved["sha256"]
        if path.stat().st_size!=item["bytes"] or digest(path)!=expected:
            raise ValueError("Model asset integrity mismatch")
for item in json.loads((ROOT/"assets/pdf-ocr.lock.json").read_text()):
    if item["path"].endswith(".onnx"):
        continue
    path=local/"models/RapidOcr"/item["path"]
    if path.stat().st_size!=item["bytes"] or digest(path)!=item["sha256"]:
        raise ValueError("OCR asset integrity mismatch")
acceptance_path=ROOT/".local/preparation/enhanced-pdf-acceptance.json"
acceptance=json.loads(acceptance_path.read_text()) if acceptance_path.exists() else {}
if args.require_acceptance and (acceptance.get("status")!="passed" or not acceptance.get("checkpoint_reused_without_reparse")):
    raise ValueError("Enhanced parser acceptance incomplete")
ready={"schema":1,"requirements_sha256":digest(lock),"model_lock_sha256":digest(ROOT/"assets/pdf-models.lock.json"),
       "ocr_lock_sha256":digest(ROOT/"assets/pdf-ocr.lock.json"),"docling":versions["docling"],
       "torch":versions["torch"],"acceptance_pages":acceptance.get("pages")}
(local/"ready.json").write_text(json.dumps(ready,indent=2))
print(json.dumps({"status":"verified","dependencies":len(versions),"desktop_parser_enabled":True}))
