"""Fetch public primary sources for local evaluation; never calls a model."""

import hashlib
import json
from pathlib import Path

import httpx

from paper_reader.store import Store

PAPERS = [
    ("attention", "dev", "1706.03762v1", "Attention Is All You Need"),
    ("bert", "dev", "1810.04805v2", "BERT: Pre-training of Deep Bidirectional Transformers"),
    ("resnet", "dev", "1512.03385v1", "Deep Residual Learning for Image Recognition"),
    ("layernorm", "holdout", "1607.06450v1", "Layer Normalization"),
    ("lora", "holdout", "2106.09685v1", "LoRA: Low-Rank Adaptation of Large Language Models"),
]


def main():
    root = Path("var/evaluation")
    root.mkdir(parents=True, exist_ok=True)
    store = Store("var/data")
    manifest = []
    with httpx.Client(timeout=90, proxy="http://127.0.0.1:7897", follow_redirects=True) as client:
        for alias, split, arxiv, title in PAPERS:
            path = root / (alias + ".pdf")
            url = "https://arxiv.org/pdf/" + arxiv
            if not path.exists():
                response = client.get(url)
                response.raise_for_status()
                if not response.content.startswith(b"%PDF-"):
                    raise ValueError("not_pdf:" + alias)
                with path.open("xb") as f:
                    f.write(response.content)
            document = store.import_pdf(path)
            if document["state"] != "ready":
                raise ValueError("parse_failed:" + alias)
            parsed = document["parsed"]
            (root / (alias + ".json")).write_text(json.dumps(parsed, ensure_ascii=False, indent=2))
            text = "\n\n".join(
                f"[{b['id']}] [{b['section']}]\n{b['text']}" for b in parsed["blocks"]
            )
            (root / (alias + ".txt")).write_text(text)
            manifest.append(
                {
                    "alias": alias,
                    "split": split,
                    "url": url,
                    "title": title,
                    "sha256": document["id"],
                    "parse_sha256": parsed["parse_sha256"],
                    "pages": len(parsed["pages"]),
                    "path": str(path),
                }
            )
            print(alias, split, len(parsed["pages"]), "pages", len(text), "chars", flush=True)
    value = json.dumps(manifest, ensure_ascii=False, indent=2)
    path = root / "corpus.json"
    if path.exists() and path.read_text() != value:
        raise ValueError("existing_corpus_manifest_changed")
    path.write_text(value)
    print("manifest_sha256", hashlib.sha256(value.encode()).hexdigest())


if __name__ == "__main__":
    main()
