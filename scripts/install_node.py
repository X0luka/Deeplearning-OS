"""Install a checksum-pinned Node runtime inside this workspace, never globally."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--proxy", default="")
args = parser.parse_args()
downloads = root / ".local/tool-downloads"
tools = root / ".local/tools"
downloads.mkdir(parents=True, exist_ok=True)
tools.mkdir(parents=True, exist_ok=True)
env = {"PATH": "/usr/bin:/bin", "HOME": str(root / ".local/setup-home")}
def download(url, target):
    cmd = ["/usr/bin/curl", "-q", "--fail", "--location", "--silent", "--show-error",
           "--proto", "=https", "--max-time", "180", "--retry", "1", "--output", str(target)]
    if args.proxy:
        cmd += ["--proxy", args.proxy, "--noproxy", ""]
    subprocess.run(cmd + [url], check=True, env=env, timeout=370)
manifest = root / "toolchain.json"
if manifest.exists():
    info = json.loads(manifest.read_text())["node"]
else:
    checksums = downloads / "node22-SHASUMS256.txt"
    download("https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt", checksums)
    match = re.search(r"^([a-f0-9]{64})\s+(node-(v22\.\d+\.\d+)-linux-x64\.tar\.xz)$",
                      checksums.read_text(), re.M)
    if not match:
        raise SystemExit("Expected official Linux x64 Node 22 checksum entry.")
    info = {"version": match[3], "sha256": match[1],
            "url": "https://nodejs.org/dist/" + match[3] + "/" + match[2]}
    manifest.write_text(json.dumps({"node": info, "python": "3.12"}, indent=2) + "\n")
filename = info["url"].rsplit("/", 1)[1]
archive = downloads / filename
if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != info["sha256"]:
    download(info["url"], archive.with_suffix(".partial"))
    archive.with_suffix(".partial").replace(archive)
if hashlib.sha256(archive.read_bytes()).hexdigest() != info["sha256"]:
    raise SystemExit("Node archive checksum mismatch; refusing to extract.")
folder_name = filename.removesuffix(".tar.xz")
destination = tools / folder_name
if not destination.exists():
    with tarfile.open(archive, "r:xz") as stream:
        for member in stream.getmembers():
            if not member.name.startswith(folder_name + "/") and member.name != folder_name:
                raise SystemExit("Unexpected archive prefix.")
        stream.extractall(tools, filter="data")
link = tools / "node"
if link.is_symlink():
    if link.resolve() != destination:
        raise SystemExit("Different runtime already selected.")
elif link.exists():
    raise SystemExit("Runtime selection path already occupied.")
else:
    link.symlink_to(folder_name, target_is_directory=True)
result = subprocess.check_output([str(link / "bin/node"), "--version"], env=env, text=True).strip()
assert result == info["version"]
print(json.dumps({"version": result, "sha256_verified": True, "runtime": str(link)}))
