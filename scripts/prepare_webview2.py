"""Restore fixed WebView2 SDK files; no Runtime install or global changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
import zipfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--proxy", default="")
parser.add_argument("--offline", action="store_true")
args = parser.parse_args()
info = json.loads((root / "toolchain.json").read_text())["webview2"]
archive = root / ".local/tool-downloads" / ("microsoft.web.webview2." + info["version"] + ".nupkg")
archive.parent.mkdir(parents=True, exist_ok=True)
if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != info["sha256"]:
    if args.offline:
        raise SystemExit("Verified SDK archive is unavailable in the private cache")
    download_env = {"PATH": "/usr/bin:/bin"}
    curl = "curl"
    if os.name == "nt":
        system_root = os.environ["SystemRoot"]
        download_env = {"SystemRoot": system_root, "PATH": str(Path(system_root) / "System32")}
        curl = str(Path(system_root) / "System32/curl.exe")
    command = [curl, "-q", "--fail", "--location", "--silent", "--show-error",
               "--proto", "=https", "--max-time", "120", "--retry", "1"]
    if args.proxy:
        proxy = urlsplit(args.proxy)
        if proxy.scheme not in ("http", "https") or not proxy.hostname or proxy.username or proxy.password:
            raise SystemExit("Use an HTTP(S) proxy without embedded credentials")
        command += ["--proxy", args.proxy]
    partial = archive.with_suffix(".partial")
    subprocess.run([*command, "--output", str(partial), info["url"]],
                   env=download_env, check=True, timeout=250)
    if hashlib.sha256(partial.read_bytes()).hexdigest() != info["sha256"]:
        raise SystemExit("SDK checksum mismatch; refusing extraction")
    partial.replace(archive)
destination = root / ".local/tools" / ("webview2-" + info["version"])
if destination.is_symlink():
    raise SystemExit("SDK directory must not be a link")
destination.mkdir(parents=True, exist_ok=True)
members = {"Microsoft.Web.WebView2.Core.dll": "lib/net462/Microsoft.Web.WebView2.Core.dll",
           "Microsoft.Web.WebView2.WinForms.dll": "lib/net462/Microsoft.Web.WebView2.WinForms.dll",
           "WebView2Loader.dll": "runtimes/win-x64/native/WebView2Loader.dll",
           "LICENSE.txt": "LICENSE.txt"}
files = {}
with zipfile.ZipFile(archive) as package:
    for name, member in members.items():
        target = destination / name
        if target.is_symlink():
            raise SystemExit("SDK output must not be a link")
        data = package.read(member)
        target.write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
manifest = {"package": "Microsoft.Web.WebView2", **info, "architecture": "win-x64",
            "files": files, "source": "Official NuGet HTTPS package, pinned after initial retrieval",
            "runtime_installed": False}
(destination / "sdk-files.json").write_text(json.dumps(manifest, indent=2) + "\n")
(root / ".local/preparation").mkdir(parents=True, exist_ok=True)
(root / ".local/preparation/webview2-sdk.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"sdk": str(destination), "version": info["version"],
                  "archive_sha256_verified": True, "runtime_installed": False}))
