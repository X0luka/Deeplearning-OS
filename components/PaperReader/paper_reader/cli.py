import argparse
import json
from pathlib import Path

from .evidence import build_context
from .model import Gateway
from .runner import run_job, worker_lock
from .store import Store
from .types import Question


def main():
    parser = argparse.ArgumentParser(description="PaperReader stage A validation CLI")
    parser.add_argument("--data", type=Path, default=Path("var/data"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import").add_argument("path", type=Path)
    sub.add_parser("document").add_argument("id")
    sub.add_parser("context").add_argument("request", type=Path)
    ask = sub.add_parser("ask")
    ask.add_argument("request", type=Path)
    run = sub.add_parser("run")
    run.add_argument("id")
    run.add_argument("--permit", type=Path, required=True)
    sub.add_parser("job").add_argument("id")
    sub.add_parser("recover")
    args = parser.parse_args()
    store = Store(args.data)
    if args.command == "import":
        result = store.import_pdf(args.path)
        result.pop("parsed", None)
    elif args.command == "document":
        result = store.document(args.id)
    elif args.command in {"context", "ask"}:
        request = Question.model_validate_json(args.request.read_text())
        result = (
            build_context(store, request)
            if args.command == "context"
            else {"job_id": store.enqueue(request)}
        )
    elif args.command == "run":
        result = run_job(store, args.id, Gateway(store, args.permit))
    elif args.command == "job":
        result = store.job(args.id)
    else:
        with worker_lock(store):
            result = {"interrupted": store.recover()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
