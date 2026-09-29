"""Independent PaperReader UI and history; no paper business logic in the host."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aos.adapters.history import HistoryHttpAdapter
from aos.adapters.process import Service
from aos.domain import Product
from aos.integrations import Integration, local_url


def configure(parser):
    parser.add_argument("--paper-url", default=os.getenv("AOS_PAPER_URL", "http://127.0.0.1:8602"))
    parser.add_argument("--paper-data-dir", type=Path, default=None)
    parser.add_argument("--paper-dir", type=Path,
                        default=Path(os.getenv("AOS_PAPER_DIR", str(Path(__file__).resolve().parents[4] / "PaperReader"))))


def create(args):
    url = local_url(args.paper_url)
    directory = args.paper_dir.resolve()
    product = Product("paper", "PaperReader · 论文阅读", url, url, navigation_label="Reader", navigation_icon="paper")
    service = Service("paper_reader", (str(directory / ".venv/bin/python"), "-m",
                      "paper_reader.web", "--port", str(urlsplit(url).port or 80),
                      "--data-dir", str(args.paper_data_dir.resolve() if args.paper_data_dir is not None else directory / "var/reader")), directory,
                      url.rstrip("/") + "/aos/v1/describe", "api", product_id=product.id)
    client = httpx.AsyncClient(base_url=url, timeout=10, trust_env=False)
    return Integration(product, client, HistoryHttpAdapter(client), [service], autostart=False, assistant_section="paper", model_tasks={"chat": "论文对话", "reading": "中文通读"})
