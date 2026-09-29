"""Fetch the reviewed Docling assets into this copy, with pinned revisions and LFS hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import httpx
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument("--proxy", default="")
parser.add_argument("--offline", action="store_true")
args=parser.parse_args()
if args.proxy:
    parsed=urlsplit(args.proxy)
    if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("Use an HTTP(S) proxy without embedded credentials")
records=json.loads((ROOT/"assets/pdf-models.lock.json").read_text())
output=ROOT/".local/pdf-cpu/models"
report_path=ROOT/".local/preparation/pdf-assets-downloaded.json"
report=json.loads(report_path.read_text()) if report_path.exists() else {}
def hash_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()
started=time.monotonic()
with httpx.Client(proxy=args.proxy or None,trust_env=False,timeout=httpx.Timeout(40,connect=15),follow_redirects=True) as client:
    for repo in records:
        folder=output/repo["repo"].replace("/","--")
        for item in repo["files"]:
            name=item["name"]
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("invalid_asset_path")
            path=folder/name
            key=repo["repo"]+"/"+name
            old=report.get(key,{})
            if path.exists() and old.get("revision")==repo["revision"] and hash_file(path)==old.get("sha256"):
                continue
            if args.offline:
                raise SystemExit("Verified PDF asset missing in private cache: "+name)
            if time.monotonic()-started>500:
                raise SystemExit("Download batch deadline; verified files retained")
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix(path.suffix+".partial")
            h=hashlib.sha256();size=0
            with client.stream("GET","https://huggingface.co/"+repo["repo"]+"/resolve/"+repo["revision"]+"/"+name) as response:
                response.raise_for_status()
                with temp.open("wb") as stream:
                    for chunk in response.iter_bytes(65536):
                        size+=len(chunk)
                        if size>max(item["bytes"],1024*1024):
                            raise ValueError("asset_exceeds_declared_size")
                        h.update(chunk);stream.write(chunk)
            if size!=item["bytes"] or (item["lfs_sha256"] and h.hexdigest()!=item["lfs_sha256"]):
                raise ValueError("asset_integrity_failed")
            temp.replace(path)
            report[key]={"revision":repo["revision"],"bytes":size,"sha256":h.hexdigest(),"license":repo["license"]}
            report_path.write_text(json.dumps(report,indent=2))
        print(json.dumps({"repo":repo["repo"],"status":"downloaded_and_verified"}),flush=True)
print(json.dumps({"files":len(report),"bytes":sum(x["bytes"] for x in report.values())}))
