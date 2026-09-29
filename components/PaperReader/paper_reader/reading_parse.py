"""Bounded PDF workers and verified, resumable page-range artifacts."""
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from bs4 import BeautifulSoup

VERSION = "pdf-ranges-1"


class ParseError(ValueError):
    def __init__(self, code, **details):
        super().__init__(code)
        self.details = details


def save(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def memory():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.split()[0]) * 1024
    return values


def worker(command, log, checkpoint, timeout=900):
    """Stop before a worker exhausts the machine; never classify SIGKILL as proven OOM."""
    mem = memory()
    reserve = 384 * 1024**2
    budget = min(int(mem["MemTotal"] * .65), mem["MemAvailable"] - reserve)
    if budget < 512 * 1024**2:
        raise ParseError("pdf_memory_unavailable")
    process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                               stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            checkpoint()
            if time.monotonic() > deadline:
                raise ParseError("pdf_parse_timeout")
            try:
                status = Path(f"/proc/{process.pid}/status").read_text()
                match = re.search(r"^VmRSS:\s+(\d+)", status, re.MULTILINE)
                rss = int(match[1]) * 1024 if match else 0
                available = memory()["MemAvailable"]
                if rss > budget or available < 192 * 1024**2:
                    raise ParseError("pdf_parse_memory_limit", rss_bytes=rss, budget_bytes=budget)
            except FileNotFoundError:
                pass
            time.sleep(.1)
        if process.returncode:
            code = ("pdf_parser_file_missing" if process.returncode == 79 else
                    "pdf_parser_dependency_missing" if process.returncode == 78 else
                    "pdf_parser_killed" if process.returncode == -9 else "pdf_parser_failed")
            raise ParseError(code, returncode=process.returncode)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def page_count(pdf):
    # PDFium counts pages without materializing all page text or raster images.
    import pypdfium2
    document = pypdfium2.PdfDocument(pdf)
    try:
        return len(document)
    finally:
        document.close()


def validate(folder, first, last):
    assets = json.loads((folder / "assets.json").read_text())
    provenance = json.loads((folder / "provenance.json").read_text())
    pages = [p["page"] for p in provenance["pages"]]
    if sorted(pages) != list(range(first, last + 1)):
        raise ParseError("pdf_page_coverage_changed")
    for name, expected in assets.items():
        if not re.fullmatch(r"image-[0-9]{4}\.png", name) or sha(folder / name) != expected:
            raise ParseError("source_image_changed")
    files = ["source.html", "provenance.json", "assets.json", *assets]
    return {name: sha(folder / name) for name in files}


def merge(folder, chunks, identity, total):
    """Keep native HTML and absolute PDF page coordinates; namespace local object refs."""
    bodies, assets, provenance, notes = [], {}, {"pages": [], "items": {}}, []
    for chunk in chunks:
        base = folder / chunk["folder"]
        if validate(base, chunk["first"], chunk["last"]) != chunk["files"]:
            raise ParseError("pdf_checkpoint_changed")
        dom = BeautifulSoup((base / "source.html").read_text(), "html.parser")
        original_assets = json.loads((base / "assets.json").read_text())
        names = {}
        for old, expected in original_assets.items():
            name = f"image-{len(assets):04}.png"
            shutil.copyfile(base / old, folder / name)
            assets[name] = expected
            names[old] = name
        for image in dom.select("img[src]"):
            old = image["src"].rsplit("/", 1)[-1]
            if old not in names:
                raise ParseError("source_image_changed")
            image["src"] = f"/api/reading-jobs/{identity}/assets/{names[old]}"
        prefix = f"pages-{chunk['first']}-{chunk['last']}:"
        for node in dom.select("[data-source-ref]"):
            node["data-source-ref"] = prefix + node["data-source-ref"]
        prov = json.loads((base / "provenance.json").read_text())
        provenance["pages"].extend(prov["pages"])
        provenance["items"].update({prefix + k: v for k, v in prov["items"].items()})
        for node in dom.select('[data-quality="source_image_no_verified_text"]'):
            notes.append({"source_ref": node.get("data-source-ref"), "kind": "source_image",
                          "reason": "此处保留原文图像，未完成可靠识别，请核对原文。"})
        body = dom.article or dom
        bodies.append(body.decode_contents())
    if [p["page"] for p in provenance["pages"]] != list(range(1, total + 1)):
        raise ParseError("pdf_page_coverage_changed")
    temp = folder / "source.html.tmp"
    temp.write_text("<article>" + "".join(bodies) + "</article>", encoding="utf-8")
    temp.replace(folder / "source.html")
    save(folder / "assets.json", assets)
    save(folder / "provenance.json", provenance)
    save(folder / "parse-quality.json", {"source_images": len(notes), "issues": notes,
         "conservative_pages": sum(c["last"] - c["first"] + 1 for c in chunks if c["profile"] == "conservative")})


def parse(pdf, folder, identity, python, checkpoint):
    # A second Reader instance must not start another heavyweight parser in this data directory.
    with (folder.parent / "pdf-parser.lock").open("a") as lock:
        while True:
            checkpoint()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.2)
        return _parse(Path(pdf), folder, identity, python, checkpoint)


def _parse(pdf, folder, identity, python, checkpoint):
    fingerprint = {"pdf": sha(pdf), "version": VERSION}
    ledger = folder / "parse-progress.json"
    state = json.loads(ledger.read_text()) if ledger.exists() else None
    if state and state["fingerprint"] != fingerprint:
        raise ParseError("pdf_checkpoint_changed")
    if state is None:
        total = page_count(pdf)
        if total < 1:
            raise ParseError("pdf_no_pages")
        state = {"fingerprint": fingerprint, "total": total, "done": 0, "chunks": [],
                 "pending": [[i, min(i + 3, total), "standard"] for i in range(1, total + 1, 4)],
                 "events": [], "status": "parsing"}
    chunks = state["chunks"]
    # Validate saved work before reuse, including after a backup/restore.
    for chunk in chunks:
        if validate(folder / chunk["folder"], chunk["first"], chunk["last"]) != chunk["files"]:
            raise ParseError("pdf_checkpoint_changed")
    state["status"] = "parsing"
    state.pop("error", None)
    save(ledger, state)
    while state["pending"]:
        checkpoint()
        first, last, profile = state["pending"][0]
        state["current"] = {"first": first, "last": last, "profile": profile}
        save(ledger, state)
        relative = f"parse-chunks/{first:05}-{last:05}-{profile}"
        target = folder / relative
        target.mkdir(parents=True, exist_ok=True)
        receipt = target / "complete.json"
        try:
            if receipt.exists():
                saved = json.loads(receipt.read_text())
                if saved["fingerprint"] != fingerprint or saved["files"] != validate(target, first, last):
                    raise ParseError("pdf_checkpoint_changed")
                files = saved["files"]
            else:
                command = [python, "-m", "paper_reader.reading_pdf", str(pdf), str(target), identity,
                           str(first), str(last), profile]
                with (folder / "parser.log").open("ab") as log:
                    log.write(f"\nPDF range {first}-{last} profile={profile}\n".encode())
                    log.flush()
                    worker(command, log, checkpoint)
                files = validate(target, first, last)
                save(receipt, {"fingerprint": fingerprint, "files": files})
            chunks.append({"first": first, "last": last, "profile": profile, "folder": relative, "files": files})
            state["pending"].pop(0)
            state["done"] += last - first + 1
            save(ledger, state)
        except ParseError as exc:
            code = str(exc)
            state["events"].append({"first": first, "last": last, "profile": profile,
                                    "code": code, **exc.details})
            recoverable = code in {"pdf_parse_memory_limit", "pdf_parser_killed", "pdf_parse_timeout", "pdf_parser_failed"}
            if recoverable and first < last:
                middle = (first + last) // 2
                state["pending"][0:1] = [[first, middle, profile], [middle + 1, last, profile]]
            elif recoverable and profile == "standard":
                state["pending"][0] = [first, last, "conservative"]
            else:
                state.update(status="paused", error=code)
                save(ledger, state)
                raise
            save(ledger, state)
    checkpoint()
    merge(folder, chunks, identity, state["total"])
    state.update(status="completed", current=None)
    save(ledger, state)
