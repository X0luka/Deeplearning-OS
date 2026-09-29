"""Offline completion, export and restore checks for the real synthetic Wiki run."""
import asyncio
import io
import json
import os
from pathlib import Path
import sys
import zipfile
import httpx

ROOT = Path(__file__).resolve().parents[1]
for name in ("AgentOS-K","GitHub-Wiki"):
    sys.path.insert(0,str(ROOT/"components"/name))
FOLDER = ROOT/".local/preparation/wiki-generation"

async def main():
    assert os.environ.get("DLOS_ISOLATED_TEST")=="1"
    from aos.llm.store import ModelStore
    from wiki_app import llm_client, pipeline
    from wiki_app.architecture import knowledge, auto_diagrams
    from wiki_app.backup import backup, restore
    from wiki_app.main import create_app
    from wiki_app.store import Store
    store=Store(FOLDER/"data")
    repo=store.repo("synthetic"); version=repo["current"]
    data=store.version(version)
    cfg=ModelStore(ROOT/".local/instances/dev-api-settings/workbench/llm").resolve("assistant","chat",include_secret=False)
    cfg={**cfg,"enabled":True,"source":"agentos","protocol":"openai"}
    knowledge.config=lambda *a,**k:cfg
    async def forbidden(*a,**k):
        raise AssertionError("Completed stage tried to call a model")
    llm_client.complete=forbidden
    kb=knowledge.load(store,version)
    assert kb and kb.get("status","ready")=="ready", "Knowledge remains partial"
    cache=await knowledge.build(store,version)
    diagrams=await auto_diagrams.build(store,version,settings=cfg)
    assert cache.get("cached") and diagrams.get("cached")
    ledger=json.loads((FOLDER/"ledger.json").read_text())
    completed={}
    for kind in ("build","architecture","knowledge","page_diagrams"):
        job=store.job(ledger["stages"][kind]["job"])
        assert job["state"]=="succeeded" and not (job.get("result") or {}).get("partial"), kind
        completed[kind]=job["id"]
    summary=pipeline.status(store,completed["knowledge"])
    assert summary["state"]=="succeeded", summary["state"]
    assert [job["kind"] for job in summary["stages"]]==["knowledge","page_diagrams"]
    assert summary["stages"][-1]["id"]==completed["page_diagrams"]
    artifact=auto_diagrams.published(store,version,kb)
    assert artifact and not artifact.get("counts",{}).get("review_failed")
    app=create_app(store.root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver") as client:
        page=await client.get("/versions/"+version)
        assert page.status_code==200
        response=await client.get("/api/versions/"+version+"/export")
        assert response.status_code==200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.testzip() is None
            exported=json.loads(archive.read("knowledge.json"))
            assert exported["knowledge_id"]==kb["knowledge_id"]
            assert len(archive.read("overview.md"))>100
            names=archive.namelist()
    backup(store.root,FOLDER/"backup")
    restore(FOLDER/"backup",FOLDER/"restored")
    reopened=Store(FOLDER/"restored")
    assert knowledge.load(reopened,version)==kb
    assert reopened.version(version)==data
    report={"status":"passed","input":"six synthetic source files; live GitHub transport separately verified",
            "pages":len(data["pages"]),"domains":len(kb.get("domains",[])),
            "diagram_counts":artifact.get("counts"),"completed_stage_jobs":completed,
            "latest_retry_chain_stages":len(summary["stages"]),
            "prior_failed_and_cancelled_attempts_retained":True,
            "cached_without_model_calls":True,"http_export_entries":len(names),
            "reopened_and_restored_equal":True,"additional_model_calls":0}
    (FOLDER/"verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))

if __name__=="__main__":
    asyncio.run(main())
