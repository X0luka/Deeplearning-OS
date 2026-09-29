import json
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
repos=[("docling-project/docling-layout-heron","main"),("docling-project/docling-models","v2.3.0"),("docling-project/CodeFormulaV2","main")]
records=[]
with httpx.Client(proxy="http://127.0.0.1:7897",trust_env=False,timeout=40) as client:
    for repo,revision in repos:
        response=client.get("https://huggingface.co/api/models/"+repo+"/revision/"+revision,params={"blobs":"true"})
        response.raise_for_status()
        info=response.json()
        files=[{"name":f["rfilename"],"bytes":f.get("size",0),"lfs_sha256":f.get("lfs",{}).get("sha256")} for f in info["siblings"]]
        record={"repo":repo,"revision":info["sha"],"license":info.get("cardData",{}).get("license"),"files":files}
        records.append(record)
        print(json.dumps({"repo":repo,"revision":info["sha"],"license":record["license"],"total_mib":round(sum(f["bytes"] for f in files)/1024**2,1)},ensure_ascii=False),flush=True)
(ROOT/".local/preparation/pdf-model-assets.json").write_text(json.dumps(records,indent=2))
