"""Actual Reader public PDF download path, without model calls or parsing."""
import hashlib
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/PaperReader"))
os.environ["PAPER_SOURCE_PROXY"] = "http://127.0.0.1:7897"
from paper_reader.reading_sources import fetch
try:
    data, url, kind = fetch("https://arxiv.org/pdf/1706.03762")
    result = {"status": "passed" if data.startswith(b"%PDF-") else "not_pdf",
              "bytes": len(data), "content_type": kind, "sha256": hashlib.sha256(data).hexdigest(),
              "parsing_tested": False, "model_calls": 0}
except Exception as exc:
    result = {"status": "failed", "error": type(exc).__name__, "detail": str(exc)[:200]}
print(json.dumps(result), flush=True)
(ROOT / ".local/preparation/paper-network-acceptance.json").write_text(json.dumps(result,indent=2))
