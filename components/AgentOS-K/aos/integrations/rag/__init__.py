"""Optional RAG integration; business code remains in its independent repository."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aos.adapters.process import Service
from aos.domain import Product
from aos.integrations import Integration, local_url
from aos.integrations.rag.http import RagHttpAdapter
from aos.integrations.rag.process import RagProcesses

STARTUP_ERRORS = {
    "docker_unavailable": "Docker 尚不可用，请先完成 WSL 的 Docker 配置。",
    "qdrant_container_unavailable": "无法访问已配置的 Qdrant 容器，请检查 rag_memory_qdrant。",
    "qdrant_ports_not_local": "Qdrant 容器端口未限制为本机访问，请修正部署配置。",
    "qdrant_ports_invalid": "Qdrant 容器没有配置本机 6333 端口。",
    "qdrant_container_invalid": "Qdrant 容器配置与登记服务不一致。",
}


def configure(parser):
    parser.add_argument("--rag-api", default=os.getenv("AOS_RAG_API", "http://127.0.0.1:8000"))
    parser.add_argument("--rag-ui", default=os.getenv("AOS_RAG_UI", "http://127.0.0.1:8501"))
    parser.add_argument("--rag-dir", type=Path,
                        default=Path(os.getenv("AOS_RAG_DIR", str(Path(__file__).resolve().parents[4] / "RAG-AGENT"))))


def create(args):
    product = Product("rag-agent", "RAG Memory Assistant", local_url(args.rag_api), local_url(args.rag_ui))
    rag_dir = args.rag_dir.resolve()
    python = str(rag_dir / ".venv/bin/python")
    api_port = str(urlsplit(product.api_url).port or 80)
    ui_port = str(urlsplit(product.ui_url).port or 80)
    services = [
        Service("qdrant", (), rag_dir, "http://127.0.0.1:6333/", "qdrant"),
        Service(
            "rag_api",
            (
                python,
                "-m",
                "uvicorn",
                "src.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                api_port,
            ),
            rag_dir,
            product.api_url.rstrip("/") + "/aos/v1/describe",
            "api",
            {"QDRANT_URL": "http://127.0.0.1:6333"}, product_id=product.id,
        ),
        Service(
            "rag_ui",
            (
                python,
                "-m",
                "streamlit",
                "run",
                "ui/app.py",
                "--server.address=127.0.0.1",
                f"--server.port={ui_port}",
                "--browser.gatherUsageStats=false",
            ),
            rag_dir,
            product.ui_url.rstrip("/") + "/_stcore/health",
            "ui",
            {
                "API_BASE": product.api_url,
                "NO_PROXY": "localhost,127.0.0.1",
                "no_proxy": "localhost,127.0.0.1",
            },
        ),
    ]
    client = httpx.AsyncClient(base_url=product.api_url, timeout=30, trust_env=False)
    return Integration(product, client, RagHttpAdapter(client), services,
                       supervisor=RagProcesses, manual_activation=True,
                       startup_errors=STARTUP_ERRORS)
