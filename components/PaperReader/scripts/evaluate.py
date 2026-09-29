import argparse
import json
from pathlib import Path

from paper_reader.evaluation import freeze, load_frozen, run_batch, score
from paper_reader.model import Gateway
from paper_reader.store import Store

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["freeze", "preflight", "run", "score"])
parser.add_argument("--root", type=Path, default=Path("var/evaluation"))
parser.add_argument("--data", type=Path, default=Path("var/data"))
parser.add_argument("--split", choices=["dev", "holdout"], default="dev")
parser.add_argument("--permit", type=Path)
parser.add_argument("--output", type=Path)
parser.add_argument("--runs", type=Path, nargs="*", default=[])
args = parser.parse_args()
store = Store(args.data)
cases, corpus, frozen = [args.root / name for name in ("cases.json", "corpus.json", "frozen.json")]
if args.action == "freeze":
    result = freeze(cases, corpus, store, frozen)
elif args.action == "preflight":
    result = {
        "verified_cases": len(load_frozen(cases, corpus, frozen, store)),
        "model_called": False,
    }
elif args.action == "run":
    if args.permit is None or args.output is None:
        parser.error("--permit and --output required")
    run_batch(cases, corpus, frozen, store, Gateway(store, args.permit), args.split, args.output)
    result = {"status": "batch_finished_requires_independent_review"}
else:
    entries = load_frozen(cases, corpus, frozen, store)
    receipts = []
    candidate = None
    for run in args.runs:
        current_candidate = json.loads((run / "candidate.json").read_text())
        if candidate is not None and current_candidate != candidate:
            raise ValueError("cannot_combine_different_candidates")
        candidate = current_candidate
        if (run / "freeze.json").read_bytes() != frozen.read_bytes():
            raise ValueError("run_freeze_mismatch")
        for path in run.glob("*.json"):
            receipt = json.loads(path.read_text())
            if "case_id" in receipt:
                receipts.append(receipt)
    result = score(entries, receipts)
print(json.dumps(result, ensure_ascii=False, indent=2))
