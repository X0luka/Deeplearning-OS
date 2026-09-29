"""Export a committed local review snapshot, never runtime files or credentials."""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".local" / "deliveries"
FORBIDDEN = {".local", ".git", ".venv", "node_modules", "__pycache__", "var", "raw", "human"}
SECRET = re.compile(rb"(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")


def main():
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise SystemExit("Commit reviewed changes before exporting")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    payload = subprocess.check_output(["git", "archive", "--format=zip", "HEAD"], cwd=ROOT)
    records = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for entry in archive.infolist():
            path = Path(entry.filename)
            if path.is_absolute() or ".." in path.parts or FORBIDDEN.intersection(path.parts):
                raise SystemExit("Forbidden archive path")
            if entry.is_dir():
                continue
            if ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise SystemExit("Symlink in archive")
            if (path.name.startswith(".env") and path.name != ".env.example") or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pdf", ".log", ".pem", ".key"}:
                raise SystemExit("Runtime or sensitive file in archive")
            data = archive.read(entry)
            if SECRET.search(data):
                raise SystemExit("Credential-like content detected; no export")
            records.append({"path": entry.filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if archive.testzip() is not None:
            raise SystemExit("Archive CRC failure")
    report = {"kind": "LOCAL_REVIEW_NOT_PUBLIC_RELEASE", "commit": commit,
              "limitations": ["Developer source candidate; public destination and license need confirmation", "Heuristic credential scan only", "Not an installer; platform and optional-service limits are documented"],
              "archive_sha256": hashlib.sha256(payload).hexdigest(), "files": records}
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / ("review-" + commit[:12] + ".zip")
    manifest = destination.with_suffix(".manifest.json")
    for path, data in [(destination, payload), (manifest, (json.dumps(report, ensure_ascii=False, indent=2)+"\n").encode())]:
        if path.exists() and path.read_bytes() != data:
            raise SystemExit("Existing export differs")
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(data)
    print(json.dumps({"archive": str(destination), "manifest": str(manifest), "files": len(records), "bytes": len(payload), "sha256": report["archive_sha256"]}))


if __name__ == "__main__":
    main()
