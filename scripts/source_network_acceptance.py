"""Public source acceptance, no model calls or personal files."""
import asyncio
import json
from pathlib import Path
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/GitHub-Wiki"))
from wiki_app.source import fetch
report = {}

async def github():
    try:
        sha, files, manifest = await fetch("https://github.com/octocat/Hello-World",
            proxy="http://127.0.0.1:7897", staging=str(ROOT / ".local/tmp"))
        try:
            report["github_product_import"] = {"status": "passed", "commit": sha,
                "archive_bytes": manifest.get("archive_bytes")}
        finally:
            files.temporary.cleanup()
    except Exception as exc:
        report["github_product_import"] = {"error": type(exc).__name__,
                                          "detail": str(exc)[:160]}
asyncio.run(github())
for host in ("arxiv.org", "export.arxiv.org"):
    try:
        with httpx.Client(proxy="http://127.0.0.1:7897", trust_env=False, timeout=8,
                          follow_redirects=False) as client:
            with client.stream("GET", "https://" + host + "/abs/1706.03762") as response:
                report[host] = {"http_status": response.status_code}
    except Exception as exc:
        report[host] = {"error": type(exc).__name__, "detail": str(exc)[:200]}
print(json.dumps(report), flush=True)
(ROOT / ".local/preparation/source-network-acceptance.json").write_text(json.dumps(report,indent=2))
