"""Small public downloads only. No keys, provider generation or personal data."""
import json
from pathlib import Path
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "github_web": "https://github.com/octocat/Hello-World",
    "github_api": "https://api.github.com/repos/octocat/Hello-World",
    "github_archive": "https://codeload.github.com/octocat/Hello-World/tar.gz/refs/heads/master",
    "paper_arxiv": "https://arxiv.org/abs/1706.03762",
}
results = []
for mode, proxy in (("no_explicit_proxy_under_TUN", None), ("explicit_existing_proxy", "http://127.0.0.1:7897")):
    for name, url in TARGETS.items():
        item = {"mode": mode, "target": name}
        start = time.monotonic()
        try:
            with httpx.Client(proxy=proxy, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(8, connect=5),
                              headers={"User-Agent": "Deeplearning-OS-network-acceptance"}) as client:
                with client.stream("GET", url) as response:
                    item["http_status"] = response.status_code
                    size = 0
                    for chunk in response.iter_bytes(chunk_size=16384):
                        size += len(chunk)
                        if size >= 65536:
                            break
                    item["sample_bytes"] = size
        except Exception as exc:
            item["error"] = type(exc).__name__
        item["seconds"] = round(time.monotonic()-start, 2)
        results.append(item)
        print(json.dumps(item), flush=True)
(ROOT / ".local/preparation/network-matrix.json").write_text(json.dumps(results, indent=2))
