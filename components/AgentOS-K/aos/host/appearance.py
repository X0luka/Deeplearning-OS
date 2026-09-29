"""Small public appearance preference, independent of product business storage."""
import json
import os
import tempfile
from pathlib import Path


class Appearance:
    def __init__(self, path: Path):
        self.path = path

    def read(self):
        try:
            value = json.loads(self.path.read_text())
            if isinstance(value, dict) and value.get("mode") in ("light", "dark", "system"):
                return {"mode": value["mode"]}
        except (OSError, ValueError):
            pass
        return {"mode": "system"}

    def write(self, mode):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".appearance-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"mode": mode}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        return {"mode": mode}
