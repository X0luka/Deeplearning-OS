"""Exercise the product's cancel/retry API on synthetic partial Wiki results; no model calls."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"components/GitHub-Wiki"))
FOLDER=ROOT/".local/preparation/wiki-generation"
async def main(args):
    assert os.environ.get("DLOS_ISOLATED_TEST")=="1"
    from wiki_app.main import create_app
    from wiki_app.store import Store
    if args.attempt > 1 and not args.reason:
        raise ValueError("Additional retry requires a concrete diagnosis")
    report=FOLDER/("quality-retry-request.json" if args.attempt == 1 else "quality-retry-request-2.json")
    if report.exists():
        raise ValueError("Retry already requested; inspect existing job")
    ledger=json.loads((FOLDER/"ledger.json").read_text())
    previous=ledger["stages"]["knowledge"]
    assert previous["result"]["partial"] is True
    identity=previous["job"]
    store=Store(FOLDER/"data")
    pending=previous["result"]["next_job_id"]
    assert store.job(pending)["state"]=="queued"
    app=create_app(store.root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver",
                                headers={"X-Wiki-Token":app.state.token}) as client:
        cancelled=await client.post("/api/jobs/"+identity+"/pipeline/cancel")
        assert cancelled.status_code==200
        response=await client.post("/api/jobs/"+identity+"/pipeline/retry")
        assert response.status_code==202
        job=response.json()["job_id"]
    queued=store.job(job)
    assert queued["kind"]=="knowledge" and queued["args"]["refresh"] is True
    assert store.job(pending)["state"]=="cancelled"
    value={"status":"queued","job":job,"previous_diagram_job_cancelled_before_generation":True,
           "product_retry_refresh":True,"model_calls":0,"reason":args.reason}
    report.write_text(json.dumps(value,indent=2))
    print(json.dumps(value))
if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, choices=[1,2], default=1)
    parser.add_argument("--reason", default="")
    asyncio.run(main(parser.parse_args()))
