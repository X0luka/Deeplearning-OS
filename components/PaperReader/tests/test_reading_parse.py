import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from conftest import tiny_pdf

from paper_reader import reading_parse as parser
from paper_reader.paper_content import build
from paper_reader.reading_structure import prepare


def output(command, log, checkpoint):
    folder = Path(command[4])
    first, last = int(command[6]), int(command[7])
    identity = command[5]
    body = []
    items = {}
    for page in range(first, last + 1):
        ref = f"#/texts/{page-first}"
        body.append(f'<p data-source-ref="{ref}">Page {page} unique text.</p>')
        items[ref] = [{"page": page, "bbox": [10, 20, 200, 40]}]
    image = b"synthetic-image-" + str(first).encode()
    (folder / "image-0000.png").write_bytes(image)
    body.append(f'<figure><img src="/api/reading-jobs/{identity}/assets/image-0000.png"></figure>')
    (folder / "source.html").write_text("<article>" + "".join(body) + "</article>")
    parser.save(folder / "assets.json", {"image-0000.png": parser.sha(folder / "image-0000.png")})
    parser.save(folder / "provenance.json", {"pages": [{"page": p, "width": 612, "height": 792, "issues": []} for p in range(first, last+1)], "items": items})


def fixture(tmp_path, monkeypatch, pages=8):
    pdf = tiny_pdf(tmp_path / "fixture.pdf", [[(50, 700, f"Page {i}")] for i in range(pages)])
    folder = tmp_path / "job"
    folder.mkdir()
    monkeypatch.setattr(parser, "worker", output)
    return pdf, folder


def test_ranges_merge_images_absolute_pages_and_resume_without_work(tmp_path, monkeypatch):
    pdf, folder = fixture(tmp_path, monkeypatch)
    parser.parse(pdf, folder, "a"*64, "python", lambda: None)
    plan = prepare((folder / "source.html").read_text())
    content, _ = build(plan, {"document": "test"}, json.loads((folder / "provenance.json").read_text()))
    paragraphs = [b for b in content["blocks"] if b["kind"] == "p"]
    assert [b["page"] for b in paragraphs] == list(range(1, 9))
    assert len({b["source_ref"] for b in paragraphs}) == 8
    assert len(json.loads((folder / "assets.json").read_text())) == 2
    images = BeautifulSoup(plan["original"], "html.parser").select("img")
    assert images[0]["src"] != images[1]["src"]
    monkeypatch.setattr(parser, "worker", lambda *args: pytest.fail("completed work repeated"))
    parser.parse(pdf, folder, "a"*64, "python", lambda: None)


def test_memory_pause_preserves_pages_then_resume_only_remaining(tmp_path, monkeypatch):
    pdf, folder = fixture(tmp_path, monkeypatch)
    calls = []
    def first_run(command, *args):
        start = int(command[6]); calls.append(start)
        if start == 5:
            raise parser.ParseError("pdf_memory_unavailable")
        output(command, *args)
    monkeypatch.setattr(parser, "worker", first_run)
    with pytest.raises(parser.ParseError, match="pdf_memory_unavailable"):
        parser.parse(pdf, folder, "a"*64, "python", lambda: None)
    state = json.loads((folder / "parse-progress.json").read_text())
    assert state["done"] == 4 and state["status"] == "paused"
    def resumed(command, *args):
        assert int(command[6]) == 5
        output(command, *args)
    monkeypatch.setattr(parser, "worker", resumed)
    parser.parse(pdf, folder, "a"*64, "python", lambda: None)
    assert json.loads((folder / "parse-progress.json").read_text())["done"] == 8


def test_memory_failure_splits_then_switches_single_page_profile(tmp_path, monkeypatch):
    pdf, folder = fixture(tmp_path, monkeypatch, 4)
    calls = []
    def low_memory(command, *args):
        first, last, profile = int(command[6]), int(command[7]), command[8]
        calls.append((first, last, profile))
        if profile == "standard":
            raise parser.ParseError("pdf_parse_memory_limit", rss_bytes=123)
        output(command, *args)
    monkeypatch.setattr(parser, "worker", low_memory)
    parser.parse(pdf, folder, "a"*64, "python", lambda: None)
    assert calls[:4] == [(1,4,"standard"),(1,2,"standard"),(1,1,"standard"),(1,1,"conservative")]
    state = json.loads((folder / "parse-progress.json").read_text())
    assert state["done"] == 4 and len(state["chunks"]) == 4
    assert all(c["profile"] == "conservative" for c in state["chunks"])


def test_tampered_saved_page_not_silently_reused(tmp_path, monkeypatch):
    pdf, folder = fixture(tmp_path, monkeypatch, 1)
    parser.parse(pdf, folder, "a"*64, "python", lambda: None)
    chunk = json.loads((folder / "parse-progress.json").read_text())["chunks"][0]
    (folder / chunk["folder"] / "source.html").write_text("changed")
    with pytest.raises(parser.ParseError, match="pdf_checkpoint_changed"):
        parser.parse(pdf, folder, "a"*64, "python", lambda: None)


def test_page_coverage_never_claims_partial_parse_complete(tmp_path, monkeypatch):
    pdf, folder = fixture(tmp_path, monkeypatch, 4)
    def missing(command, *args):
        output(command, *args)
        path = Path(command[4]) / "provenance.json"
        value = json.loads(path.read_text()); value["pages"].pop()
        parser.save(path, value)
    monkeypatch.setattr(parser, "worker", missing)
    with pytest.raises(parser.ParseError, match="pdf_page_coverage_changed"):
        parser.parse(pdf, folder, "a"*64, "python", lambda: None)
    assert not (folder / "source.html").exists()


def test_stop_keeps_completed_checkpoint(tmp_path, monkeypatch):
    pdf, folder = fixture(tmp_path, monkeypatch)
    def checkpoint():
        ledger = folder / "parse-progress.json"
        if ledger.exists() and json.loads(ledger.read_text())["done"] == 4:
            raise ValueError("stopped")
    with pytest.raises(ValueError, match="stopped"):
        parser.parse(pdf, folder, "a"*64, "python", checkpoint)
    assert json.loads((folder / "parse-progress.json").read_text())["done"] == 4


def test_supervisor_cleans_up_child_on_stop(tmp_path, monkeypatch):
    import sys
    import time
    calls = 0
    def checkpoint():
        nonlocal calls
        calls += 1
        if calls > 2:
            raise ValueError("stopped")
    started = time.monotonic()
    with (tmp_path / "log").open("wb") as log, pytest.raises(ValueError, match="stopped"):
        parser.worker([sys.executable, "-c", "import time; time.sleep(60)"], log, checkpoint)
    assert time.monotonic() - started < 5


def test_low_memory_never_launches_child(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "memory", lambda: {"MemTotal": 4*1024**3, "MemAvailable": 300*1024**2})
    monkeypatch.setattr(parser.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch"))
    with (tmp_path / "log").open("wb") as log, pytest.raises(parser.ParseError, match="pdf_memory_unavailable"):
        parser.worker(["python"], log, lambda: None)


def test_supervisor_memory_pressure_terminates_worker(tmp_path, monkeypatch):
    import sys
    calls = 0
    def pressure():
        nonlocal calls
        calls += 1
        return {"MemTotal": 4*1024**3, "MemAvailable": (3*1024**3 if calls == 1 else 128*1024**2)}
    monkeypatch.setattr(parser, "memory", pressure)
    with (tmp_path / "log").open("wb") as log, pytest.raises(parser.ParseError, match="pdf_parse_memory_limit"):
        parser.worker([sys.executable, "-c", "import time; time.sleep(60)"], log, lambda: None)


@pytest.mark.parametrize("code,returncode", [("pdf_parser_dependency_missing",78),("pdf_parser_file_missing",79)])
def test_missing_dependency_stops_without_range_splitting(tmp_path, monkeypatch, code, returncode):
    pdf, folder = fixture(tmp_path, monkeypatch)
    calls = []
    def unavailable(command, *args):
        calls.append(command)
        raise parser.ParseError(code, returncode=returncode)
    monkeypatch.setattr(parser, "worker", unavailable)
    with pytest.raises(parser.ParseError, match=code):
        parser.parse(pdf, folder, "a"*64, "python", lambda: None)
    state = json.loads((folder / "parse-progress.json").read_text())
    assert len(calls) == 1
    assert state["status"] == "paused"
    assert state["done"] == 0
    assert state["pending"][0] == [1, 4, "standard"]


@pytest.mark.parametrize("code,returncode", [("pdf_parser_dependency_missing",78),("pdf_parser_file_missing",79)])
def test_worker_reports_missing_dependency_exit(tmp_path, monkeypatch, code, returncode):
    import sys
    monkeypatch.setattr(parser, "memory", lambda: {"MemTotal": 4*1024**3, "MemAvailable": 3*1024**3})
    with (tmp_path / "worker.log").open("wb") as log:
        with pytest.raises(parser.ParseError, match=code):
            parser.worker([sys.executable, "-c", "raise SystemExit("+str(returncode)+")"], log, lambda: None)
