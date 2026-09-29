"""Docling native structure, images and serializers. No custom page cropping."""
import hashlib
import json
from pathlib import Path


def convert(pdf, output, identity, page_range=None, profile="standard"):
    from bs4 import BeautifulSoup
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import AcceleratorOptions, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.transforms.serializer.html import HTMLDocSerializer, HTMLParams
    from docling_core.types.doc import DoclingDocument, ImageRefMode

    class SourcedHTML(HTMLDocSerializer):
        def serialize(self, *, item=None, **kwargs):
            result = super().serialize(item=item, **kwargs)
            if item is not None and getattr(item, "prov", None) and (result.text or (profile == "conservative" and str(item.label) == "formula")):
                # Do not present flattened multiline code as faithful, executable source.
                # Preserve its exact appearance via Docling's own image API.
                flat_code = str(item.label) == "code" and "\n" not in item.text and any(abs(p.bbox.t-p.bbox.b)>30 for p in item.prov)
                unsafe = "\ufffd" in result.text or "(cid:" in result.text
                source_formula = profile == "conservative" and str(item.label) == "formula"
                if flat_code or unsafe or source_formula:
                    import base64
                    import io
                    pictures = []
                    for index, region in enumerate(item.prov):
                        original = item.get_image(self.doc, prov_index=index)
                        if original is None:
                            raise ValueError("code_original_image_unavailable")
                        buffer = io.BytesIO()
                        original.save(buffer, format="PNG")
                        uri = base64.b64encode(buffer.getvalue()).decode()
                        pictures.append('<img src="data:image/png;base64,' + uri + '" alt="原文区域；提取文字未通过格式校验">')
                    note = '<figcaption class="reading-review-note" style="color:#b91c1c">此处保留原文图像，未完成可靠识别，请核对原文。</figcaption>'
                    result = result.model_copy(update={"text": '<figure data-quality="source_image_no_verified_text" translate="no">' + ''.join(pictures) + note + '</figure>'})
                soup = BeautifulSoup(result.text, "html.parser")
                first = soup.find()
                if first:
                    first["data-source-ref"] = item.self_ref
                    result = result.model_copy(update={"text": str(soup)})
            return result

    output = Path(output).resolve()
    options = PdfPipelineOptions(do_ocr=True, do_table_structure=True,
        generate_page_images=True, generate_picture_images=True, images_scale=2,
        do_code_enrichment=False, do_formula_enrichment=profile == "standard",
        accelerator_options=AcceleratorOptions(num_threads=2 if profile == "standard" else 1))
    fingerprint = {"pdf": hashlib.sha256(Path(pdf).read_bytes()).hexdigest(),
                   "pipeline": "docling-2.129.0-native-formula-code-text-v2", "profile": profile}
    if page_range is not None:
        fingerprint["page_range"] = list(page_range)
    metadata = output / "parser-version.json"
    if metadata.exists() and json.loads(metadata.read_text()) == fingerprint and (output / "docling.json").exists():
        doc = DoclingDocument.model_validate_json((output / "docling.json").read_text())
    else:
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        result = converter.convert(Path(pdf), **({"page_range": page_range} if page_range else {}))
        if str(result.status).split(".")[-1].lower() != "success":
            raise ValueError("pdf_parse_incomplete")
        doc = result.document
        doc.save_as_json(output / "docling.json")
        metadata.write_text(json.dumps(fingerprint))
    doc.save_as_markdown(output / "source.md", image_mode=ImageRefMode.REFERENCED)
    serialized = SourcedHTML(doc=doc, params=HTMLParams(image_mode=ImageRefMode.EMBEDDED)).serialize().text
    soup = BeautifulSoup(serialized, "html.parser")
    assets = {}
    import base64
    for i, img in enumerate(soup.select("img")):
        src = img.get("src", "")
        if not src.startswith("data:image/png;base64,"):
            raise ValueError("source_image_format_unsupported")
        raw = base64.b64decode(src.split(",", 1)[1], validate=True)
        name = f"image-{i:04}.png"
        (output / name).write_bytes(raw)
        assets[name] = hashlib.sha256(raw).hexdigest()
        img["src"] = f"/api/reading-jobs/{identity}/assets/{name}"
    body = soup.body or soup
    (output / "source.html").write_text("<article>" + body.decode_contents() + "</article>", encoding="utf-8")
    (output / "assets.json").write_text(json.dumps(assets))
    provenance = {"pages": [{"page": p.page_no, "width": p.size.width,
        "height": p.size.height, "issues": []} for p in doc.pages.values()], "items": {}}
    for item, _ in doc.iterate_items():
        locations = []
        for region in getattr(item, "prov", []):
            box = region.bbox.to_top_left_origin(page_height=doc.pages[region.page_no].size.height)
            locations.append({"page": region.page_no, "bbox": [box.l, box.t, box.r, box.b]})
        provenance["items"][item.self_ref] = locations
    (output / "provenance.json").write_text(json.dumps(provenance))


if __name__ == "__main__":
    import ctypes
    import os
    import signal
    import sys
    # Set this in the child before heavy imports, never via unsafe threaded preexec_fn.
    parent = os.getppid()
    if sys.platform == "linux":
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise RuntimeError("pdf_worker_parent_guard_failed")
        if parent == 1 or os.getppid() != parent:
            sys.exit(1)
    import importlib.util
    if any(importlib.util.find_spec(name) is None for name in ("docling", "docling_core")):
        print("pdf_parser_dependency_missing: install the pdf-reading extra in the parser environment", file=sys.stderr)
        sys.exit(78)
    args = sys.argv[1:]
    try:
        convert(*args[:3], page_range=(int(args[3]), int(args[4])) if len(args) > 3 else None,
                profile=args[5] if len(args) > 5 else "standard")
    except FileNotFoundError:
        # Splitting pages cannot repair a missing source/model file.
        print("pdf_parser_file_missing: verify the PDF and parser model assets", file=sys.stderr)
        sys.exit(79)
