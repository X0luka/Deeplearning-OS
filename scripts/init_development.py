"""Initialize a developer checkout without installing global tools or copying user data."""
import json
import platform
from pathlib import Path
import sys

def initialize(root):
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError("Use a checkout path without symbolic links")
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise ValueError("This developer setup currently requires Linux x86_64 (WSL2 Ubuntu supported)")
    if sys.version_info[:2] != (3, 12):
        raise ValueError("Use Python 3.12 for the pinned dependency environment")
    required = ["toolchain.json", "components/AgentOS-K/pyproject.toml",
                "components/PaperReader/pyproject.toml", "components/GitHub-Wiki/pyproject.toml"]
    if any(not (root / name).is_file() for name in required):
        raise ValueError("Run from a complete Deeplearning-OS source checkout")
    local = root / ".local"
    paths = [local, local/"preparation", local/"setup-home", local/"tools", local/"tmp"]
    for path in paths:
        if path.is_symlink():
            raise ValueError("Developer state paths must not be symbolic links")
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    marker = local/"development.json"
    if not marker.exists():
        marker.write_text(json.dumps({"schema":1,"purpose":"developer-source-checkout"},indent=2)+"\n")
    return {"status":"initialized","root":str(root),"global_changes":False}

if __name__ == "__main__":
    print(json.dumps(initialize(Path(__file__).resolve().parents[1])))
