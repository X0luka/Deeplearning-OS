"""Page geometry first; conservative column ordering with visible limitations."""

import hashlib
import re
from collections import Counter

import pdfplumber

PARSER_VERSION = "pdfplumber-columns-5"


def norm(text):
    return " ".join(text.split())


def order_lines(words, width):
    starts = Counter(
        round(w["x0"] / 3)
        for w in words
        if width / 2 < w["x0"] < width / 2 + 40 and len(w["text"]) >= 3 and w["text"][:3].isalpha()
    )
    peaks = starts.most_common(2)
    aligned = bool(
        peaks and peaks[0][1] >= 10 and (len(peaks) == 1 or peaks[0][1] >= 2 * peaks[1][1])
    )
    rows = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        row = next((r for r in reversed(rows[-4:]) if abs(r[0]["top"] - word["top"]) < 3), None)
        if row is None:
            rows.append([word])
        else:
            row.append(word)
    segments = []
    gutter_rows = 0
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        threshold = 8 if aligned else 12
        current = []
        for word in row:
            if (
                current
                and word["x0"] - current[-1]["x1"] > threshold
                and current[-1]["x1"] < width / 2 + 12
                and word["x0"] > width / 2 - 12
            ):
                gutter_rows += 1
                segments.append(current)
                current = []
            current.append(word)
        if current:
            segments.append(current)
    # Tables have gutters too: require repeated gutter evidence across the page,
    # otherwise preserve row order and expose possible table-layout limitations.
    two_columns = aligned or (gutter_rows >= 8 and gutter_rows >= len(rows) * 0.4)
    if not two_columns:
        segments = rows
    lines = []
    for segment in segments:
        bbox = [
            min(w["x0"] for w in segment),
            min(w["top"] for w in segment),
            max(w["x1"] for w in segment),
            max(w["bottom"] for w in segment),
        ]
        column = 0 if bbox[2] < width / 2 + 1 else 1 if bbox[0] > width / 2 - 1 else 2
        lines.append(
            {
                "text": norm(" ".join(w["text"] for w in segment)),
                "bbox": bbox,
                "column": column if two_columns else 0,
            }
        )
    if not two_columns:
        return sorted(lines, key=lambda x: (x["bbox"][1], x["bbox"][0])), False
    # Full-width material creates reading bands; each band reads left then right.
    ordered, band = [], []
    for line in sorted(lines, key=lambda x: (x["bbox"][1], x["bbox"][0])):
        if line["column"] == 2:
            ordered.extend(sorted(band, key=lambda x: (x["column"], x["bbox"][1])))
            band = []
            ordered.append(line)
        else:
            band.append(line)
    ordered.extend(sorted(band, key=lambda x: (x["column"], x["bbox"][1])))
    return ordered, True


def is_heading(text):
    return (
        bool(
            re.match(
                r"^(abstract|references|conclusions?|acknowledg\w*|\d+(?:\.\d+)*\.?\s+[A-Z])",
                text,
                re.IGNORECASE
                if text.lower() in {"abstract", "references", "conclusion", "conclusions"}
                else 0,
            )
        )
        and len(text) < 130
    )


def parse_pdf(path):
    pages, blocks = [], []
    section = "Front matter"
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > 100:
            raise ValueError("page_limit_100")
        for page_no, page in enumerate(pdf.pages, 1):
            issues = []
            upright = page.filter(
                lambda obj: obj.get("object_type") != "char" or obj.get("upright", True)
            )
            words = upright.extract_words(x_tolerance=2, y_tolerance=3)
            if len(upright.chars) < len(page.chars):
                issues.append("rotated_text_omitted_from_linear_reading")
            if len(words) > 12000:
                raise ValueError("page_word_limit")
            lines, columns = order_lines(words, page.width)
            if columns:
                issues.append("column_order_heuristic_review_required")
            if not words or sum(len(w["text"]) for w in words) < 30:
                issues.append("empty_or_image_page_no_ocr")
            text = "\n".join(line["text"] for line in lines)
            if "(cid:" in text or "\ufffd" in text:
                issues.append("unmapped_glyphs")
            # Linear text never certifies two-dimensional mathematical structure.
            if re.search(r"[=∑∏√∈∂∇≤≥]", text):
                issues.append("formula_layout_not_certified")
            if re.search(r"(?:Table\s+\d|\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+\d)", text):
                issues.append("table_cells_not_structurally_verified")
            if page.rotation:
                issues.append("rotated_page_review_required")
            pending = []

            def flush(pending, page_no, section):
                if not pending:
                    return
                value = "\n".join(line["text"] for line in pending)
                bounds = [
                    min(x["bbox"][0] for x in pending),
                    min(x["bbox"][1] for x in pending),
                    max(x["bbox"][2] for x in pending),
                    max(x["bbox"][3] for x in pending),
                ]
                blocks.append(
                    {
                        "id": f"p{page_no}-b{len(blocks) + 1}",
                        "page": page_no,
                        "bbox": bounds,
                        "text": value,
                        "section": section,
                        "column": pending[0]["column"],
                        "lines": list(pending),
                    }
                )
                pending.clear()

            for line in lines:
                if is_heading(line["text"]):
                    flush(pending, page_no, section)
                    section = line["text"]
                if pending and (
                    line["column"] != pending[-1]["column"]
                    or line["bbox"][1] - pending[-1]["bbox"][3] > 9
                    or sum(len(x["text"]) for x in pending) > 1000
                ):
                    flush(pending, page_no, section)
                pending.append(line)
            flush(pending, page_no, section)
            pages.append(
                {
                    "page": page_no,
                    "width": page.width,
                    "height": page.height,
                    "rotation": page.rotation,
                    "issues": issues,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "word_count": len(words),
                }
            )
    return {"parser": PARSER_VERSION, "pages": pages, "blocks": blocks}
