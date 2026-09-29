"""Distribute the public appearance assets to explicitly named product checkouts."""
import argparse
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destinations", nargs="+", type=Path,
                        help="Product static directories; e.g. ../GitHub-Wiki/wiki_app/static")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "aos/host/static"
    for destination in args.destinations:
        if not destination.is_dir():
            parser.error(f"Missing static directory: {destination}")
        for name in ("appearance.css", "appearance.js"):
            shutil.copyfile(source / name, destination / name)


if __name__ == "__main__":
    main()
