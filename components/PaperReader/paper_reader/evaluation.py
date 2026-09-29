"""Immutable evaluation inputs and explicit, independently reviewed release gate."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .evidence import build_context
from .runner import run_job
from .types import Question


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(cases_path, corpus_path, store, output):
    cases = json.loads(Path(cases_path).read_text())
    corpus = json.loads(Path(corpus_path).read_text())
    assert len(cases) == len({c["id"] for c in cases}) == 40, "40 unique cases required"
    assert Counter(c["category"] for c in cases) == {
        "selection": 12,
        "fulltext": 12,
        "formula": 8,
        "boundary": 8,
    }
    assert Counter(c["split"] for c in cases) == {"dev": 24, "holdout": 16}
    assert sum(c["split"] == "holdout" and c["category"] != "boundary" for c in cases) == 12
    docs = {p["sha256"]: p for p in corpus}
    coverage = []
    for case in cases:
        request = Question.model_validate(case["request"])
        document = store.document(request.document)
        assert docs[request.document]["split"] == case["split"]
        assert digest(store.root / "raw" / (request.document + ".pdf")) == request.document
        registry = {b["id"]: b for b in document["parsed"]["blocks"]}
        assert case["expected_points"] and case["critical_errors"]
        for item in case["gold_evidence"]:
            assert registry[item["id"]]["text"] == item["text"]
            assert registry[item["id"]]["page"] == item["page"]
        context = build_context(store, request)
        provided = {b["id"] for b in context["evidence"]}
        missing = [b["id"] for b in case["gold_evidence"] if b["id"] not in provided]
        coverage.append(
            {
                "id": case["id"],
                "missing_gold_blocks": missing,
                "all_extracted_text": context["coverage"]["all_extracted_text"],
            }
        )
    # This reports retrieval coverage only, not answer quality.
    frozen = {
        "cases_sha256": digest(cases_path),
        "corpus_sha256": digest(corpus_path),
        "documents": {p["sha256"]: p["parse_sha256"] for p in corpus},
        "created": datetime.now(UTC).isoformat(),
        "coverage": coverage,
        "quality": "NOT_RUN",
    }
    with Path(output).open("x") as f:
        json.dump(frozen, f, ensure_ascii=False, indent=2)
    return frozen


def load_frozen(cases_path, corpus_path, frozen_path, store):
    frozen = json.loads(Path(frozen_path).read_text())
    if (
        digest(cases_path) != frozen["cases_sha256"]
        or digest(corpus_path) != frozen["corpus_sha256"]
    ):
        raise ValueError("evaluation_inputs_changed")
    for identity, parse_hash in frozen["documents"].items():
        if digest(store.root / "raw" / (identity + ".pdf")) != identity:
            raise ValueError("evaluation_original_changed")
        if store.document(identity)["parsed"]["parse_sha256"] != parse_hash:
            raise ValueError("evaluation_parser_changed")
    return json.loads(Path(cases_path).read_text())


def run_batch(cases_path, corpus_path, frozen_path, store, gateway, split, output):
    cases = load_frozen(cases_path, corpus_path, frozen_path, store)
    selected = [c for c in cases if c["split"] == split]
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).parent
    code_hashes = {p.name: digest(p) for p in sorted(source.glob("*.py"))}
    (out / "candidate.json").write_text(json.dumps(code_hashes, indent=2))
    if split == "holdout":
        # Any attempt consumes the original holdout, even if the process crashes.
        with Path(frozen_path).with_suffix(".holdout-used").open("x") as f:
            f.write(str(out.resolve()))
    (out / "freeze.json").write_bytes(Path(frozen_path).read_bytes())
    for case in selected:
        identity = store.enqueue(Question.model_validate(case["request"]))
        receipt = {"case_id": case["id"], "job_id": identity}
        # Persist mapping before network dispatch so an interrupted batch is auditable.
        with (out / (case["id"] + ".json")).open("x") as f:
            json.dump(receipt, f)
        result = run_job(store, identity, gateway)
        receipt.update({"result": result, "review": None})
        (out / (case["id"] + ".json")).write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(case["id"], result["state"], result["error"] or "", flush=True)
        if result["error"] in {"model_budget_exhausted", "model_key_not_configured"}:
            break


def score(cases, receipts):
    by_id = {r["case_id"]: r for r in receipts}
    if len(by_id) != len(receipts):
        raise ValueError("duplicate_case_receipts")
    failures = Counter()
    passed_answerable = heldout_passed = 0
    blockers, pending = [], []
    for case in cases:
        receipt = by_id.get(case["id"])
        review = receipt.get("review") if receipt else None
        if (
            not review
            or not review.get("reviewer")
            or not review.get("rationale")
            or type(review.get("passed")) is not bool
            or type(review.get("critical_error")) is not bool
        ):
            pending.append(case["id"])
            continue
        # A generator's self-score cannot open the release gate.
        if review.get("method") != "source_checked_by_reviewer":
            pending.append(case["id"])
            continue
        result = receipt.get("result", {})
        structural = (
            result.get("state") == "completed"
            and result.get("answer", {}).get("citation_integrity") == "verified"
        )
        if review.get("critical_error") or not structural:
            blockers.append(case["id"])
        passed = structural and review.get("passed") is True and not review.get("critical_error")
        if passed and case["category"] != "boundary":
            passed_answerable += 1
            if case["split"] == "holdout":
                heldout_passed += 1
        if not passed:
            failures[case["category"]] += 1
    released = (
        len(cases) == 40
        and len(by_id) == 40
        and not pending
        and not blockers
        and passed_answerable >= 29
        and heldout_passed >= 11
        and failures["boundary"] == 0
        and all(failures[k] <= 1 for k in ("selection", "fulltext", "formula"))
    )
    return {
        "stage_a_passed": released,
        "answerable_passed": passed_answerable,
        "holdout_answerable_passed": heldout_passed,
        "failures": dict(failures),
        "critical_errors": blockers,
        "pending_reviews": pending,
    }
