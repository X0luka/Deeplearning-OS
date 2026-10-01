"""Public URL resolution. Every redirect/resource is validated before fetching."""
import hashlib
import ipaddress
import json
import os
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup


def config():
    path = Path(__file__).resolve().parents[1] / "var" / "reading-config.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def source_proxy():
    from . import llm_client
    if llm_client.managed():
        return llm_client.network()["proxy"]
    return os.getenv("PAPER_SOURCE_PROXY") or config().get("source_proxy")


def public_url(url):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("public_http_url_required")
    if p.port not in {None, 80, 443}:
        raise ValueError("public_http_url_required")
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("source_host_unavailable") from None
    proxy = source_proxy()
    # The configured local proxy maps public arXiv hosts into its fake-IP range.
    # Exception is restricted to these fixed public hosts, never arbitrary/local targets.
    proxy_arxiv = bool(proxy) and p.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
    def allowed(address):
        ip = ipaddress.ip_address(address)
        return ip.is_global or (proxy_arxiv and ip in ipaddress.ip_network("198.18.0.0/15"))
    if addresses and all(allowed(a[4][0]) for a in addresses):
        return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
    # For other public sites on fake-IP networks, verify with public DNS rather
    # than allowing the proxy's reserved subnet as an arbitrary fetch target.
    try:
        ipaddress.ip_address(p.hostname)
        literal = True
    except ValueError:
        literal = False
    fake = addresses and all(ipaddress.ip_address(a[4][0]) in ipaddress.ip_network("198.18.0.0/15") for a in addresses)
    if not proxy or literal or not fake:
        raise ValueError("private_source_not_allowed")
    resolved = []
    with httpx.Client(proxy=proxy, trust_env=False, timeout=15) as client:
        for kind in ("A", "AAAA"):
            answer = client.get("https://cloudflare-dns.com/dns-query", params={"name": p.hostname, "type": kind}, headers={"Accept": "application/dns-json"})
            answer.raise_for_status()
            resolved.extend(a["data"] for a in answer.json().get("Answer", []) if a["type"] in {1, 28})
    if not resolved or any(not ipaddress.ip_address(ip).is_global for ip in resolved):
        raise ValueError("private_source_not_allowed")
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def fetch(url, limit=30 * 1024 * 1024):
    # An explicit user-configured outbound proxy is supported; ambient proxies are ignored.
    with httpx.Client(timeout=httpx.Timeout(90, connect=20), trust_env=False,
                      proxy=source_proxy(), follow_redirects=False) as client:
        for _ in range(6):
            url = public_url(url)
            with client.stream("GET", url, headers={"User-Agent": "PaperReader/0.1"}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code != 200:
                    raise ValueError("source_http_" + str(response.status_code))
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("source_too_large")
                    chunks.append(chunk)
                return b"".join(chunks), str(response.url), response.headers.get("content-type", "")
    raise ValueError("source_redirect_limit")


def paper_title(text):
    """Extract declared paper metadata; do not ask a model to invent a title."""
    soup = BeautifulSoup(text, "html.parser")
    meta = soup.find("meta", attrs={"name": re.compile("^citation_title$", re.I)})
    heading = soup.select_one("h1.ltx_title_document, h1.title")
    title = meta.get("content", "") if meta else ""
    if not title and heading:
        heading = BeautifulSoup(str(heading), "html.parser")
        for descriptor in heading.select(".descriptor"):
            descriptor.decompose()
        title = heading.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", title).strip()[:300]


def full_html(text):
    soup = BeautifulSoup(text, "html.parser")
    root = soup.find("article") or soup.find("main")
    if not root:
        return False
    # Abstract/landing pages must not be passed off as the full paper.
    prose = root.find_all("p")
    return len(prose) >= 8 and sum(len(p.get_text()) for p in prose) > 3000 and bool(root.find(["h2", "section"]))


def resolve(url):
    url = public_url(url)
    p = urlsplit(url)
    arxiv = re.fullmatch(r"/(?:abs|html|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?)(?:\.pdf)?/?", p.path)
    if p.hostname in {"arxiv.org", "www.arxiv.org"} and arxiv:
        identity = arxiv[1]
        title = ""
        # Unversioned requests resolve the version before requesting either representation.
        if not re.search(r"v[0-9]+$", identity):
            raw, _, _ = fetch("https://arxiv.org/abs/" + identity)
            title = paper_title(raw)
            soup = BeautifulSoup(raw, "html.parser")
            match = re.search(re.escape(identity) + r"v[0-9]+", soup.get_text())
            if not match:
                raise ValueError("paper_version_not_confirmed")
            identity = match[0]
        html_url = "https://arxiv.org/html/" + identity
        source = None
        try:
            content, html_url, _ = fetch(html_url)
            if full_html(content):
                source = content.decode("utf-8")
                title = title or paper_title(source)
        except (ValueError, httpx.HTTPError):
            pass
        pdf_url = "https://arxiv.org/pdf/" + identity
        try:
            pdf, pdf_url, _ = fetch(pdf_url)
            if not pdf.startswith(b"%PDF-"):
                raise ValueError("source_is_not_pdf")
        except (ValueError, httpx.HTTPError):
            if source is None:
                raise
            pdf = None
        return {"html": source, "html_url": html_url, "pdf": pdf,
                "url": pdf_url, "name": identity + ".pdf", "title": title}
    content, url, mime = fetch(url)
    if content.startswith(b"%PDF-"):
        return {"pdf": content, "html": None, "url": url,
                "name": Path(urlsplit(url).path).name or "paper.pdf"}
    if "html" not in mime and b"<html" not in content[:1000].lower():
        raise ValueError("source_format_unsupported")
    text = content.decode("utf-8")
    soup = BeautifulSoup(text, "html.parser")
    meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
    if full_html(text):
        # Generic HTML is kept independently: do not pair unverified PDF versions.
        return {"pdf": None, "html": text, "html_url": url, "url": url,
                "name": (soup.title.get_text() if soup.title else "论文 HTML")[:180],
                "title": paper_title(text)}
    links = ([meta.get("content")] if meta else []) + [
        a["href"] for a in soup.select("a[href]")
        if urlsplit(a["href"]).path.lower().endswith(".pdf")]
    for link in links[:3]:
        data, final, _ = fetch(urljoin(url, link))
        if data.startswith(b"%PDF-"):
            return {"pdf": data, "html": None, "url": final, "name": "paper.pdf"}
    raise ValueError("full_text_unavailable_upload_pdf")


def localize(source, base, folder, identity):
    soup = BeautifulSoup(source, "html.parser")
    assets = {}
    for index, node in enumerate(soup.select("article img,main img")):
        src = node.get("src")
        if not src:
            raise ValueError("source_image_missing")
        raw, _, _ = fetch(urljoin(base, src), 15 * 1024 * 1024)
        if raw.startswith(b"\x89PNG"):
            ext = "png"
        elif raw.startswith(b"\xff\xd8"):
            ext = "jpg"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            ext = "webp"
        else:
            raise ValueError("source_image_format_unsupported")
        name = f"image-{index:04}.{ext}"
        (folder / name).write_bytes(raw)
        assets[name] = hashlib.sha256(raw).hexdigest()
        node["src"] = f"/api/reading-jobs/{identity}/assets/{name}"
        node.attrs.pop("srcset", None)
    for a in soup.select("a[href]"):
        if not a["href"].startswith("#"):
            a["href"] = urljoin(base, a["href"])
    return str(soup), assets
