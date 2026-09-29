import argparse
import hashlib
from urllib.parse import urlsplit
import json
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument("--proxy", default="")
parser.add_argument("--offline", action="store_true")
args=parser.parse_args()
for key in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
    os.environ.pop(key,None)
if args.proxy:
    parsed=urlsplit(args.proxy)
    if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("Use an HTTP(S) proxy without embedded credentials")
    os.environ.update(HTTP_PROXY=args.proxy,HTTPS_PROXY=args.proxy)
os.environ.update(NO_PROXY="127.0.0.1,localhost",HF_HOME=str(ROOT/".local/pdf-cpu/hf"),
    DOCLING_CACHE_DIR=str(ROOT/".local/pdf-cpu/cache"))
from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel
folder=ROOT/".local/pdf-cpu/models"/RapidOcrModel._model_repo_folder
if not args.offline:
    RapidOcrModel.download_models(backend="torch",local_dir=folder)
records=[]
for path in folder.rglob("*"):
    if path.is_file():
        with path.open("rb") as stream:
            digest=hashlib.file_digest(stream,"sha256").hexdigest()
        records.append({"path":str(path.relative_to(folder)),"bytes":path.stat().st_size,"sha256":digest})
expected=json.loads((ROOT/"assets/pdf-ocr.lock.json").read_text())
actual={x["path"]:x for x in records}
for item in expected:
    if item["path"].endswith(".onnx"):
        continue  # Earlier ONNX probe is not required by this pinned CPU environment.
    if actual.get(item["path"])!=item:
        raise ValueError("OCR asset checksum mismatch")
(ROOT/".local/preparation/pdf-ocr-assets.json").write_text(json.dumps(records,indent=2))
print(json.dumps({"ocr_backend":"torch","files":len(records),"bytes":sum(x["bytes"] for x in records)}))
