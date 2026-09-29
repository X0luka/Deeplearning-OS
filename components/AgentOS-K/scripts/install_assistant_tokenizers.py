"""Install checksum-pinned, official DeepSeek tokenizer data (no remote code)."""
import argparse
import hashlib
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/deepseek-ai/deepseek-recipe/main/static/tokenizers/"
FILES = {
    "deepseek-v4.json": ("v4/tokenizer.json", "97d2f31b020d18b5aee5c9b3d5b4efb10ea210f3fe3f7dffe3f1cd90542d6b19"),
    "deepseek-v41.json": ("v41/tokenizer.json", "81f64d1248a68ce3663e07ab3ee48b851e5df0e32d27cb98e4c9a268151e8d99"),
    "LICENSE": ("LICENSE", "f2c6c602815669d292889e5be8c802f2ed950653b77999b1584e8e6aed25d040"),
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.proxy:
        from urllib.parse import urlsplit
        parsed = urlsplit(args.proxy)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("invalid_proxy")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {"http": args.proxy, "https": args.proxy} if args.proxy else {}))
    target = Path(__file__).resolve().parents[1] / "var/tokenizers"
    target.mkdir(parents=True, exist_ok=True)
    for name, (remote, checksum) in FILES.items():
        path = target / name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
            print("Verified", name)
            continue
        if args.offline:
            raise RuntimeError("Tokenizer missing or checksum mismatch: " + name)
        with opener.open(BASE + remote, timeout=60) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if hashlib.sha256(data).hexdigest() != checksum:
            raise RuntimeError("Official tokenizer checksum mismatch: " + name)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        print("Installed", name)

if __name__ == "__main__":
    main()
