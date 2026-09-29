"""One immutable content revision for reading, translation, retrieval and citations."""
import copy
import hashlib
import html
import json
import re

from bs4 import BeautifulSoup

VERSION = "paper-content-1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def text_of(node):
    """Keep native LaTeX rather than flattening MathML into broken character streams."""
    node = copy.copy(node)
    for math in node.find_all("math"):
        annotation = math.find("annotation", attrs={"encoding": "application/x-tex"})
        tex = math.get("alttext") or (annotation.get_text() if annotation else None)
        math.replace_with(" $" + tex + "$ " if tex else " [公式：需查看原文] ")
    for img in node.find_all("img"):
        img.replace_with(" [图片：" + img.get("alt", "需查看原文") + "] ")
    for br in node.find_all("br"):
        br.replace_with("\n")
    for row in node.select("tr,.ltx_listingline"):
        row.append("\n")
    return node.get_text(" ", strip=True)


def build(plan, source, provenance=None):
    """Annotate the same DOM that is translated/displayed, not a second PDF parse."""
    root = BeautifulSoup(plan["original"], "html.parser")
    provenance = provenance or {}
    blocks, selected = [], set()
    section = "正文"
    selectors = "h1,h2,h3,h4,h5,h6,p,figcaption,caption,li,dt,dd,pre,table,[data-protected]"
    for node in root.select(selectors):
        if any(id(p) in selected for p in node.parents):
            continue
        # Containers with paragraphs must be traversed, never flattened wholesale.
        if node.name in {"li", "dd", "figcaption"} and node.find("p"):
            continue
        text = text_of(node)
        if not text.strip():
            continue
        identity = "b" + str(len(blocks) + 1)
        node["data-content-id"] = identity
        selected.add(id(node))
        if re.fullmatch("h[1-6]", node.name):
            section = text
        origin = next((p for p in [node, *node.parents] if p.get("data-source-ref")), None)
        ref = origin.get("data-source-ref") if origin else None
        locations = provenance.get("items", {}).get(ref, [])
        location = locations[0] if locations else {}
        page, bbox = location.get("page"), location.get("bbox")
        lines = [{"text": line, "bbox": bbox} for line in text.splitlines() if line.strip()]
        blocks.append({"id": identity, "text": text, "section": section,
            "kind": node.name, "page": page, "bbox": bbox, "lines": lines,
            "source_ref": ref, "locations": locations, "quality": node.get("data-quality"),
            "target": {"kind": "pdf" if page is not None and bbox is not None else "html", "block": identity}})
    if not blocks:
        raise ValueError("no_extractable_text")
    plan = dict(plan, original=str(root))
    # Added content IDs are part of the locked original; regenerate its inventory.
    plan["protected"] = [{"id": n["data-protected"], "tag": n.name,
        "sha256": hashlib.sha256(str(n).encode()).hexdigest()}
        for n in root.select("[data-protected]")]
    # Locked fragments inside translation units must use the annotated DOM too.
    from .reading_structure import encode
    for unit in plan["units"]:
        node = root.select_one('[data-reading-unit="' + unit["id"] + '"]')
        unit["text"], unit["pieces"] = encode(node)
    content = {"version": VERSION, "document": source["document"], "source": source,
        "blocks": blocks, "pages": provenance.get("pages", []),
        "original": plan["original"]}
    for block in blocks:
        if block.get("quality"):
            for page_info in content["pages"]:
                if page_info["page"] == block["page"] and block["quality"] not in page_info["issues"]:
                    page_info["issues"].append(block["quality"])
    content["parse_sha256"] = digest(content)
    return content, plan


def load(store, document):
    pointer = store.root / "reading" / document / "current.json"
    if not pointer.exists():
        return None
    job = json.loads(pointer.read_text()).get("job", "")
    if not re.fullmatch(r"[a-f0-9]{64}", job):
        raise ValueError("reading_integrity_error")
    folder = store.root / "reading-jobs" / job
    path = folder / "content.json"
    if not path.exists():
        return None  # Immutable pre-migration artifacts are readable as history.
    manifest = json.loads((folder / "manifest.json").read_text())
    raw = path.read_bytes()
    if manifest.get("content_sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("reading_integrity_error")
    content = json.loads(raw)
    if content["document"] != document:
        raise ValueError("reading_integrity_error")
    for block in content["blocks"]:
        block["target"]["revision"] = job
    return content


def search(content, query, limit=24):
    """Length-normalized lexical ranking; repeated table labels must not dominate prose."""
    import math
    from collections import Counter

    def tokens(text):
        return re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,40}|[\u4e00-\u9fff]", text.lower())

    words = set(tokens(query))
    documents = [Counter(tokens(b["text"])) for b in content["blocks"]]
    lengths = [sum(d.values()) for d in documents]
    average = sum(lengths) / max(1, len(lengths)) or 1
    frequencies = {w: sum(w in d for d in documents) for w in words}
    scored = []
    for i, counts in enumerate(documents):
        score = 0.0
        for word in words:
            tf = counts[word]
            if tf:
                idf = math.log(1 + (len(documents) - frequencies[word] + .5) / (frequencies[word] + .5))
                score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * lengths[i] / average))
        if score:
            scored.append((score, i, content["blocks"][i]["id"]))
    return [identity for _, _, identity in sorted(scored, key=lambda x: (-x[0], x[1]))[:limit]]


def markdown(content, translated=None):
    root = BeautifulSoup(translated or content["original"], "html.parser")
    parts = []
    for block in content["blocks"]:
        node = root.select_one('[data-content-id="' + block["id"] + '"]')
        for warning in node.select('.reading-review-note'):
            warning['style'] = 'color:#b91c1c'
        text = text_of(node)
        if re.fullmatch("h[1-6]", node.name):
            text = "#" * int(node.name[1]) + " " + text
        elif node.name == "pre":
            text = "```\n" + node.get_text() + "\n```"
        elif node.name == "table":
            text = str(node)  # Markdown permits HTML; keep merged cells intact.
        for img in node.select("img[src]"):
            text += "\n\n![" + img.get("alt", "图片") + "](" + img["src"] + ")"
        if node.name != 'table':
            for warning in node.select('.reading-review-note'):
                message = warning.get_text(' ', strip=True)
                marker = '<span style="color:#b91c1c">' + html.escape(message) + '</span>'
                text = text.replace(message, marker) if message in text else marker + "\n\n" + text
        parts.append("<!-- " + block["id"] + " -->\n" + text)
    return "\n\n".join(parts) + "\n"
