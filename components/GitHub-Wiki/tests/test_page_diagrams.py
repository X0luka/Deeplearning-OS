import copy
import json

import pytest

from wiki_app.architecture.page_diagrams import decorate, fingerprint, sections, validate


def sample():
    page = {"slug": "flows", "title": "关键流程", "markdown": "# 关键流程\n\n## 消息\n\n发送与回传。"}
    graph = {
        "section": "1",
        "title": "消息链路",
        "caption": "源码依据",
        "nodes": [
            {"id": "a", "label": "UI", "basis": "source", "evidence": ["E1"]},
            {"id": "b", "label": "API", "basis": "source", "evidence": ["E1"]},
        ],
        "edges": [{"source": "a", "target": "b", "label": "发送", "basis": "source", "evidence": ["E1"]}],
    }
    evidence = {"E1": {"path": "main.py", "start": 1, "end": 3, "text": "example"}}
    return page, graph, evidence


def test_section_heading_is_not_an_empty_diagram():
    page, _, _ = sample()
    assert [s["id"] for s in sections(page["markdown"])] == ["1"]


@pytest.mark.parametrize("problem", ["evidence", "endpoint", "duplicate", "section"])
def test_reject_invalid_diagram(problem):
    page, graph, evidence = sample()
    if problem == "evidence":
        graph["edges"][0]["evidence"] = ["invented"]
    elif problem == "endpoint":
        graph["edges"][0]["target"] = "absent"
    elif problem == "duplicate":
        graph["nodes"][1]["id"] = "a"
    else:
        graph["section"] = "9"
    with pytest.raises(ValueError):
        validate([graph], page, evidence)


def test_only_current_reviewed_diagrams_are_visible(tmp_path, monkeypatch):
    page, graph, evidence = sample()
    knowledge = {"knowledge_id": "k1", "pages": [page]}
    artifact = {
        "knowledge_id": "k1",
        "pages_hash": fingerprint(knowledge),
        "review": {"status": "draft"},
        "pages": {"flows": [graph]},
        "evidence": evidence,
    }
    monkeypatch.setattr("wiki_app.architecture.auto_diagrams.directory", lambda *args: tmp_path)
    folder = tmp_path / "page-diagrams"
    folder.mkdir()
    target = folder / "current.json"
    target.write_text(json.dumps(artifact))
    assert "visual_sections" not in decorate(None, "v", knowledge)[0]
    artifact["review"]["status"] = "approved"
    target.write_text(json.dumps(artifact))
    assert decorate(None, "v", knowledge)[0]["visual_sections"][0]["graph"]["nodes"] == graph["nodes"]
    changed = copy.deepcopy(knowledge)
    changed["pages"][0]["markdown"] += "已修改"
    assert "visual_sections" not in decorate(None, "v", changed)[0]
    changed = {**knowledge, "knowledge_id": "k2"}
    assert "visual_sections" not in decorate(None, "v", changed)[0]
