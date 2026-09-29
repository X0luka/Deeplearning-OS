import json

import pytest
from conftest import tiny_pdf

from paper_reader.evidence import build_context, prompt, validate_answer
from paper_reader.parse import norm
from paper_reader.runner import run_job, worker_lock
from paper_reader.store import Store
from paper_reader.types import Question, Selection


def request(doc, **kw):
    return Question(document=doc["id"], mode="fulltext", question="概述方法与实验局限", **kw)


def answer(block):
    return {
        "status": "answered",
        "claims": [
            {
                "kind": "paper",
                "text": "解释",
                "citations": [{"evidence_id": block["id"], "quote": block["text"]}],
            }
        ],
        "limitations": [],
    }


def test_duplicate_and_reopen(imported):
    store, doc, path = imported
    repeated = store.import_pdf(path)
    assert repeated == doc
    assert Store(store.root).document(doc["id"]) == doc
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM block_search").fetchone()[0] == len(
            doc["parsed"]["blocks"]
        )


def test_new_version_does_not_change_old(imported, tmp_path):
    store, doc, _ = imported
    other = tiny_pdf(tmp_path / "second.pdf", [[(50, 700, "A revised document with new results.")]])
    second = store.import_pdf(other)
    assert second["id"] != doc["id"]
    assert store.document(doc["id"]) == doc


def test_bad_pdf_keeps_original(tmp_path):
    store = Store(tmp_path / "data")
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.4\nnot a valid PDF")
    doc = store.import_pdf(path)
    assert doc["state"] == "failed"
    assert (store.root / "raw" / (doc["id"] + ".pdf")).read_bytes() == path.read_bytes()
    with pytest.raises(ValueError, match="document_not_ready"):
        build_context(store, request(doc))


def test_non_pdf_rejected(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_text("not PDF")
    with pytest.raises(ValueError, match="invalid_pdf_header"):
        Store(tmp_path / "data").import_pdf(path)


def test_empty_page_is_not_silent_success(tmp_path):
    doc = Store(tmp_path / "data").import_pdf(tiny_pdf(tmp_path / "empty.pdf", [[]]))
    assert "empty_or_image_page_no_ocr" in doc["parsed"]["pages"][0]["issues"]


def test_two_columns_do_not_interleave(tmp_path):
    lines = [(50, 750, "1 Introduction")]
    for i in range(10):
        lines += [
            (50, 720 - i * 13, f"Left paragraph item {i} continued."),
            (330, 720 - i * 13, f"Right paragraph item {i} continued."),
        ]
    store = Store(tmp_path / "data")
    doc = store.import_pdf(tiny_pdf(tmp_path / "columns.pdf", [lines]))
    text = " ".join(b["text"] for b in doc["parsed"]["blocks"])
    assert text.index("Left paragraph item 9") < text.index("Right paragraph item 0")
    assert "column_order_heuristic_review_required" in doc["parsed"]["pages"][0]["issues"]


def test_selected_context_and_geometry(imported):
    store, doc, _ = imported
    block = next(b for b in doc["parsed"]["blocks"] if "This method" in b["text"])
    selection = Selection(page=block["page"], bbox=block["bbox"], text=block["text"])
    question = Question(
        document=doc["id"], mode="selection", question="This 指什么？", selection=selection
    )
    context = build_context(store, question)
    assert block["id"] in {b["id"] for b in context["evidence"]}
    assert len(context["evidence"]) > 1
    question.selection.text = "a fabricated passage"
    with pytest.raises(ValueError, match="selection_text_geometry_mismatch"):
        build_context(store, question)


def test_selection_off_page(imported):
    store, doc, _ = imported
    q = Question(
        document=doc["id"],
        mode="selection",
        question="解释",
        selection=Selection(page=99, bbox=(1, 1, 5, 5), text="fake"),
    )
    with pytest.raises(ValueError, match="selection_out_of_page"):
        build_context(store, q)


@pytest.mark.parametrize("bbox", [(0, 0, 0, 4), (2, 3, 1, 4), (0, 0, float("nan"), 4)])
def test_invalid_geometry(bbox):
    with pytest.raises(ValueError):
        Selection(page=1, bbox=bbox, text="text")


def test_full_document_coverage_and_search(imported):
    store, doc, _ = imported
    context = build_context(store, request(doc))
    assert context["coverage"]["all_extracted_text"]
    assert any("Conclusion" in b["section"] for b in context["evidence"])
    assert store.search(doc["id"], "residual")
    assert store.search("0" * 64, "residual") == []


def test_context_budget_reports_omissions(imported, monkeypatch):
    store, doc, _ = imported
    monkeypatch.setattr("paper_reader.evidence.MAX_CONTEXT_CHARS", 350)
    context = build_context(store, request(doc))
    assert not context["coverage"]["all_extracted_text"]
    assert context["coverage"]["not_fully_read_sections"]


def test_citations_are_exact_and_locatable(imported):
    store, doc, _ = imported
    context = build_context(store, request(doc))
    block = context["evidence"][1]
    result = validate_answer(answer(block), context)
    anchor = result["anchors"][0]
    assert anchor["document"] == doc["id"]
    assert anchor["line_boxes"]
    assert norm(anchor["quote"]) in norm(block["text"])
    assert result["semantic_support"] == "not_automatically_verified"


@pytest.mark.parametrize("change", ["quote", "id", "uncited"])
def test_bad_citations_rejected(imported, change):
    store, doc, _ = imported
    context = build_context(store, request(doc))
    raw = answer(context["evidence"][0])
    if change == "quote":
        raw["claims"][0]["citations"][0]["quote"] = "Fabricated information not present."
    elif change == "id":
        raw["claims"][0]["citations"][0]["evidence_id"] = "another-document"
    else:
        raw["claims"][0]["citations"] = []
    with pytest.raises(ValueError):
        validate_answer(raw, context)


def test_failure_persisted_and_no_retry(imported):
    store, doc, _ = imported

    class Failing:
        calls = 0

        def complete(self, *args):
            self.calls += 1
            raise TimeoutError("do not leak raw response")

    provider = Failing()
    identity = store.enqueue(request(doc))
    result = run_job(store, identity, provider)
    assert result["state"] == "failed" and result["error"] == "TimeoutError"
    assert result["context"]
    with pytest.raises(ValueError, match="job_not_queued"):
        run_job(store, identity, provider)
    assert provider.calls == 1


def test_malformed_response_preserved_for_review(imported):
    store, doc, _ = imported

    class Bad:
        def complete(self, *args):
            return {"invalid": "output"}, {"total_tokens": 4}

    result = run_job(store, store.enqueue(request(doc)), Bad())
    assert result["state"] == "failed"
    assert result["answer"]["raw"] == {"invalid": "output"}


def test_restart_marks_interrupted_never_requeues(imported):
    store, doc, _ = imported
    identity = store.enqueue(request(doc))
    store.claim(identity)
    reopened = Store(store.root)
    with worker_lock(reopened):
        assert reopened.recover() == 1
    assert reopened.job(identity)["state"] == "interrupted"


def test_worker_lock_excludes_second_worker(imported):
    store, _, _ = imported
    with (
        worker_lock(store),
        pytest.raises(ValueError, match="worker_already_running"),
        worker_lock(store),
    ):
        pass


def test_followup_cannot_cross_documents(imported, tmp_path):
    store, doc, _ = imported
    identity = store.enqueue(request(doc))
    store.set_job(
        identity, "completed", context=build_context(store, request(doc)), answer={"claims": []}
    )
    other = store.import_pdf(
        tiny_pdf(tmp_path / "other.pdf", [[(50, 700, "Another paper unrelated to this.")]])
    )
    with pytest.raises(ValueError, match="invalid_previous_question"):
        store.enqueue(request(other, previous=identity))
    q = request(doc, previous=identity)
    context = build_context(store, q)
    assert context["previous"]["question"]
    messages = prompt(q, context)
    assert "expected_answer" not in json.dumps(messages)
