import json
from pathlib import Path

import pypdfium2

root = Path("var/evaluation")
queries = {
    "attention": ["warmup", "Label Smoothing", "single-head", "28.4", "layer normalization"],
    "bert": ["deep bidirectional", "80%", "50%", "BooksCorpus", "110M", "fine-tuning", "WordPiece"],
    "resnet": ["degradation", "identity mapping", "zero", "3.57", "He", "34-layer", "projection"],
    "layernorm": [
        "single training",
        "recurrent",
        "mini-batch",
        "gain",
        "test time",
        "convolutional",
    ],
    "lora": ["frozen", "rank", "zero", "latency", "10,000", "3 times", "tasks"],
}
for alias, needles in queries.items():
    parsed = json.loads((root / (alias + ".json")).read_text())
    print("\n###", alias)
    selected = []
    for needle in needles:
        hits = [
            b for b in parsed["blocks"] if needle.lower() in b["text"].lower() and b["page"] <= 8
        ]
        for b in hits[:2]:
            if b["id"] not in selected:
                selected.append(b["id"])
                print(b["id"], b["section"], "\n" + b["text"] + "\n")
qa = root / "qa"
qa.mkdir(exist_ok=True)
for alias, pages in {
    "attention": [2, 4, 6],
    "bert": [2],
    "resnet": [2],
    "layernorm": [1],
    "lora": [3],
}.items():
    doc = pypdfium2.PdfDocument(root / (alias + ".pdf"))
    for i in pages:
        doc[i].render(scale=1.2).to_pil().save(qa / f"{alias}-{i + 1}.png")
    doc.close()
