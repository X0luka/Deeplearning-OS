import copy

import pytest

from paper_reader.evaluation import score


def records():
    cases, receipts = [], []
    layouts = {
        "dev": {"selection": 8, "fulltext": 8, "formula": 4, "boundary": 4},
        "holdout": {"selection": 4, "fulltext": 4, "formula": 4, "boundary": 4},
    }
    for split, groups in layouts.items():
        for category, count in groups.items():
            for _ in range(count):
                identity = str(len(cases))
                cases.append({"id": identity, "category": category, "split": split})
                receipts.append(
                    {
                        "case_id": identity,
                        "result": {
                            "state": "completed",
                            "answer": {"citation_integrity": "verified"},
                        },
                        "review": {
                            "reviewer": "fixture-reviewer",
                            "method": "source_checked_by_reviewer",
                            "rationale": "Synthetic gate fixture, not real model evidence.",
                            "passed": True,
                            "critical_error": False,
                        },
                    }
                )
    return cases, receipts


def test_gate_requires_all_reviews():
    cases, receipts = records()
    assert score(cases, receipts)["stage_a_passed"]
    receipts[0]["review"] = None
    assert not score(cases, receipts)["stage_a_passed"]


def test_self_score_does_not_release():
    cases, receipts = records()
    receipts[0]["review"]["method"] = "generator_self_score"
    assert not score(cases, receipts)["stage_a_passed"]


def test_total_and_per_category_thresholds():
    cases, receipts = records()
    for index in (0, 8, 16):
        receipts[index]["review"]["passed"] = False
    result = score(cases, receipts)
    assert result["answerable_passed"] == 29 and result["stage_a_passed"]
    receipts[1]["review"]["passed"] = False
    assert not score(cases, receipts)["stage_a_passed"]


@pytest.mark.parametrize("index", [0, 25, 39])
def test_any_critical_error_blocks(index):
    cases, receipts = records()
    receipts[index]["review"]["critical_error"] = True
    assert not score(cases, receipts)["stage_a_passed"]


def test_bad_citation_is_blocking_even_with_passing_review():
    cases, receipts = records()
    receipts[0]["result"]["answer"]["citation_integrity"] = "failed"
    assert not score(cases, receipts)["stage_a_passed"]


def test_holdout_threshold_and_boundary():
    cases, receipts = records()
    for i in (24, 28):
        receipts[i]["review"]["passed"] = False
    assert not score(cases, receipts)["stage_a_passed"]
    cases, receipts = records()
    receipts[-1]["review"]["passed"] = False
    assert not score(cases, receipts)["stage_a_passed"]


def test_duplicate_results_never_silently_replace_failures():
    cases, receipts = records()
    receipts.append(copy.deepcopy(receipts[0]))
    with pytest.raises(ValueError, match="duplicate_case_receipts"):
        score(cases, receipts)


def test_untested_is_not_passed():
    cases, _ = records()
    result = score(cases, [])
    assert len(result["pending_reviews"]) == 40
    assert result["answerable_passed"] == 0
    assert not result["stage_a_passed"]
