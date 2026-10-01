from aos.assistant.service import search_resources


def test_paper_title_and_number_discover_same_authorized_resource():
    paper = {"provider": "paper", "id": "synthetic-document", "title": "Attention Is All You Need",
             "aliases": ["1706.03762v7.pdf", "https://arxiv.org/pdf/1706.03762v7"]}
    unrelated = {"provider": "github-wiki", "id": "other", "title": "Attention repository"}
    for query in ("Attention Is All You Need", "attention need", "1706.03762", "1706.03762v7.pdf"):
        assert search_resources([paper, unrelated], {"query": query})["items"] == [paper]
    assert search_resources([paper, unrelated], {"query": "1706.03762", "provider": "github-wiki"})["items"] == []
    assert search_resources([unrelated], {"query": "1706.03762"})["items"] == []
    assert search_resources([paper], {"query": "unavailable paper"})["items"] == []


def test_same_title_does_not_silently_select_one_version():
    rows = [{"provider": "paper", "id": str(n), "title": "Attention Is All You Need",
             "aliases": [f"1706.03762v{n}.pdf"]} for n in (1, 7)]
    assert search_resources(rows, {"query": "Attention"})["total"] == 2
    assert search_resources(rows, {"query": "1706.03762v7"})["items"] == [rows[1]]
