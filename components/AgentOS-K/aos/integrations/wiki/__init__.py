"""Wiki service configuration and public HTTP adapter."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aos.adapters.process import Service
from aos.domain import Product
from aos.integrations import Integration, local_url
from aos.integrations.wiki.http import WikiHttpAdapter


def configure(parser):
    parser.add_argument("--wiki-url", default=os.getenv("AOS_WIKI_URL", "http://127.0.0.1:8601"))
    parser.add_argument("--wiki-data-dir", type=Path, default=None)
    parser.add_argument("--wiki-dir", type=Path,
                        default=Path(os.getenv("AOS_WIKI_DIR", str(Path(__file__).resolve().parents[4] / "GitHub-Wiki"))))
    parser.add_argument("--no-wiki", action="store_true", help="Disable this extension (legacy option)")
    parser.add_argument("--start-wiki", action="store_true", help="Start this product on Host boot")


def create(args):
    if args.no_wiki:
        return None
    url = local_url(args.wiki_url)
    product = Product("github-wiki", "GitHub Wiki · 源码阅读", url, url, navigation_label="Wiki", navigation_icon="wiki")
    directory = args.wiki_dir.resolve()
    service = Service("github_wiki",
                      (str(directory / ".venv/bin/python"), "-m", "wiki_app.main", "--port",
                       str(urlsplit(url).port or 80), "--data-dir", str(args.wiki_data_dir.resolve() if args.wiki_data_dir is not None else directory / "var")),
                      directory, url.rstrip('/') + "/aos/v1/describe", "api", product_id=product.id)
    client = httpx.AsyncClient(base_url=url, timeout=10, trust_env=False)
    return Integration(product, client, WikiHttpAdapter(client), [service],
                       preferred=True, autostart=args.autostart or args.start_wiki,
                       assistant_section="wiki", model_tasks={"chat": "源码对话", "structured": "证据与架构标注", "knowledge": "Wiki 知识生成"})
