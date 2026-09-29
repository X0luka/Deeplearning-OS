import pytest

from paper_reader.evidence import build_context, prompt, validate_answer
from paper_reader.types import Question


def context(imported):
    store, doc, _ = imported
    q = Question(document=doc["id"], mode="fulltext", question="解释方法")
    return build_context(store, q), q


def raw(block, start, end):
    return {
        "status": "answered",
        "claims": [
            {
                "kind": "paper",
                "text": "有依据的解释",
                "citations": [{"evidence_id": block["id"], "start_line": start, "end_line": end}],
            }
        ],
        "limitations": [],
    }


def test_server_copies_exact_source_lines(imported):
    ctx, _ = context(imported)
    block = next(b for b in ctx["evidence"] if len(b["lines"]) >= 2)
    response = validate_answer(raw(block, 2, 2), ctx)
    cite = response["claims"][0]["citations"][0]
    assert cite["quote"] == block["lines"][1]["text"]
    assert response["anchors"][0]["line_boxes"] == [block["lines"][1]["bbox"]]


def test_line_outside_source_is_rejected(imported):
    ctx, _ = context(imported)
    with pytest.raises(ValueError, match="citation_line_out_of_range"):
        validate_answer(raw(ctx["evidence"][0], 100, 100), ctx)


def test_id_not_repaired_to_another_page(imported):
    ctx, _ = context(imported)
    response = raw(ctx["evidence"][0], 1, 1)
    response["claims"][0]["citations"][0]["evidence_id"] = "p99-b1"
    with pytest.raises(ValueError, match="citation_not_in_supplied_evidence"):
        validate_answer(response, ctx)


def test_mixed_generated_quote_and_lines_rejected(imported):
    ctx, _ = context(imported)
    response = raw(ctx["evidence"][0], 1, 1)
    response["claims"][0]["citations"][0]["quote"] = "Fabricated excerpt"
    with pytest.raises(ValueError, match="ambiguous_citation_format"):
        validate_answer(response, ctx)


def test_prompt_numbers_lines_without_gold(imported):
    ctx, question = context(imported)
    message = prompt(question, ctx)[1]["content"]
    assert "[1]" in message and "expected_points" not in message
