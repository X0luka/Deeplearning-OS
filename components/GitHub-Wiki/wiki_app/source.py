"""Bounded public GitHub snapshots. Never check out or execute repository code."""

import asyncio
import hashlib
import io
import os
import re
import tarfile
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from .rules import RuleError

URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
SKIP = {".git", "node_modules", ".venv", "venv", "vendor", "dist", "build", "__pycache__", ".next", ".cache"}
MAX_ARCHIVE = int(os.getenv("WIKI_MAX_ARCHIVE_BYTES", "268435456"))
MAX_TEXT = int(os.getenv("WIKI_MAX_TEXT_BYTES", "134217728"))
MAX_FILE = int(os.getenv("WIKI_MAX_FILE_BYTES", "4194304"))
MAX_SCAN = int(os.getenv("WIKI_MAX_SCAN_BYTES", "1073741824"))
MAX_ENTRIES = int(os.getenv("WIKI_MAX_ENTRIES", "100000"))
COLLECTOR = "snapshot-2-stream"

MAX_FILES = int(os.getenv("WIKI_MAX_FILES", "20000"))


def canonical(url):
    m = URL.fullmatch(url.strip())
    if not m or any(x in {".", ".."} for x in m.groups()):
        raise ValueError("invalid_github_url")
    return f"https://github.com/{m[1]}/{m[2]}"


class DiskFiles(Mapping):
    """One file resident at a time; private temporary storage has explicit ownership."""
    def __init__(self, root, paths, temporary=None):
        self.root, self.paths, self.temporary = Path(root), list(paths), temporary
        self.keys = set(self.paths)
    def __len__(self):
        return len(self.paths)
    def __iter__(self):
        return iter(self.paths)
    def __getitem__(self, key):
        if key not in self.keys:
            raise KeyError(key)
        with (self.root / key).open(encoding="utf-8", newline="") as stream:
            return stream.read()
    def close(self):
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None


class ScanReader:
    def __init__(self, raw):
        self.raw, self.count = raw, 0
    def read(self, size=-1):
        value = self.raw.read(size)
        self.count += len(value)
        if self.count > MAX_SCAN:
            raise RuleError("archive_scan_limit", stage="unpack", limit=MAX_SCAN, observed=self.count)
        return value


def unpack(data, destination=None, cancelled=None):
    import gzip
    files, skipped, total, entries = {}, [], 0, []
    raw = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    with gzip.GzipFile(fileobj=raw) as expanded:  # noqa: SIM117 -- stream wrapper lifetime
        with tarfile.open(fileobj=ScanReader(expanded), mode="r|") as archive:
            for index, member in enumerate(archive):
                if cancelled and cancelled.is_set():
                    raise InterruptedError("snapshot_cancelled")
                if index >= MAX_ENTRIES:
                    raise RuleError("archive_entry_limit", stage="unpack", limit=MAX_ENTRIES, observed=index+1)
                pieces = PurePosixPath(member.name).parts[1:]
                if not pieces or member.isdir():
                    continue
                path = "/".join(pieces)
                reason = None
                if (".." in pieces or member.name.startswith("/") or not member.isfile()
                        or any("\\" in x or ":" in x for x in pieces)):
                    reason = "unsafe_path_or_type"
                elif any(x in SKIP - {"build"} for x in pieces) or path.endswith((".lock", ".min.js", ".map")):
                    reason = "generated_or_dependency"
                elif member.size > MAX_FILE:
                    reason = "file_size_limit"
                elif len(entries) >= MAX_FILES:
                    reason = "file_count_limit"
                elif total + member.size > MAX_TEXT:
                    reason = "text_size_limit"
                if reason:
                    skipped.append({"path": path, "reason": reason, "bytes": member.size})
                    continue
                content = archive.extractfile(member).read(MAX_FILE + 1)
                if b"\0" in content:
                    skipped.append({"path": path, "reason": "binary", "bytes": member.size})
                    continue
                try:
                    text = content.decode("utf-8")
                except UnicodeError:
                    skipped.append({"path": path, "reason": "non_utf8", "bytes": member.size})
                    continue
                # Duplicated tar members must not replace an already indexed snapshot.
                if path in files:
                    skipped.append({"path": path, "reason": "duplicate_path", "bytes": member.size})
                    continue
                total += len(content)
                entries.append({"path": path, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                                "lines": len(text.splitlines()), "bytes": len(content)})
                if destination:
                    target = Path(destination) / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")
                    files[path] = None
                else:
                    files[path] = text
    if not entries:
        raise RuleError(next((x["reason"] for x in skipped if x["reason"].endswith("_limit")), "no_readable_files"), stage="unpack")
    manifest = {"included": len(entries), "bytes": total, "skipped": skipped, "files": entries,
                "collector": COLLECTOR, "limits": {"archive": MAX_ARCHIVE, "text": MAX_TEXT,
                "file": MAX_FILE, "files": MAX_FILES, "scan": MAX_SCAN, "entries": MAX_ENTRIES},
                "partial": any(x["reason"].endswith("_limit") for x in skipped)}
    return (DiskFiles(destination, files) if destination else files), manifest


async def fetch(url, ref="", transport=None, proxy=None, staging=None):
    url = canonical(url)
    owner_repo = url.removeprefix("https://github.com/")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "AgentOS-GitHub-Wiki"}
    if staging:
        Path(staging).mkdir(parents=True, exist_ok=True)
    temp = tempfile.TemporaryDirectory(prefix="wiki-snapshot-", dir=staging)
    try:
        async with asyncio.timeout(600):
            async with httpx.AsyncClient(trust_env=False, timeout=30, follow_redirects=False, headers=headers,
                                         transport=transport, proxy=proxy) as client:
                commit_url = f"https://api.github.com/repos/{owner_repo}/commits/{quote(ref or 'HEAD', safe='')}"
                response = await client.get(commit_url)
                if response.status_code == 404:
                    raise ValueError("repository_or_ref_not_found")
                if response.status_code in (403, 429):
                    raise ValueError("github_rate_limited")
                response.raise_for_status()
                sha = response.json().get("sha", "")
                if not re.fullmatch("[0-9a-f]{40}", sha):
                    raise ValueError("invalid_commit")
                archive_path = Path(temp.name) / "archive.tgz"
                size = 0
                with archive_path.open("wb") as output:
                    async with client.stream("GET", f"https://codeload.github.com/{owner_repo}/tar.gz/{sha}") as response:
                        response.raise_for_status()
                        async for part in response.aiter_bytes():
                            size += len(part)
                            if size > MAX_ARCHIVE:
                                raise RuleError("archive_size_limit", stage="download",
                                                limit=MAX_ARCHIVE, observed=size)
                            output.write(part)
            cancelled = threading.Event()
            def extract():
                with archive_path.open("rb") as raw:
                    return unpack(raw, Path(temp.name) / "text", cancelled)
            extraction = asyncio.create_task(asyncio.to_thread(extract))
            try:
                files, manifest = await asyncio.shield(extraction)
            except BaseException:
                cancelled.set()
                await asyncio.gather(extraction, return_exceptions=True)
                raise
            archive_path.unlink()
        files.temporary = temp
        manifest.update(url=url, commit=sha, archive_bytes=size)
        return sha, files, manifest
    except BaseException:
        temp.cleanup()
        raise


async def check_update(url, ref="", transport=None, proxy=None):
    url = canonical(url)
    if re.fullmatch("[0-9a-f]{40}", ref):
        return {"commit": ref, "pinned": True}
    owner_repo = url.removeprefix("https://github.com/")
    async with httpx.AsyncClient(
        trust_env=False,
        timeout=30,
        follow_redirects=False,
        transport=transport,
        proxy=proxy,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "AgentOS-GitHub-Wiki"},
    ) as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner_repo}/commits/{quote(ref or 'HEAD', safe='')}"
        )
        if response.status_code in (403, 429):
            raise ValueError("github_rate_limited")
        if response.status_code == 404:
            raise ValueError("repository_or_ref_not_found")
        response.raise_for_status()
        sha = response.json().get("sha", "")
        if not re.fullmatch("[0-9a-f]{40}", sha):
            raise ValueError("invalid_commit")
        return {"commit": sha, "pinned": False}
