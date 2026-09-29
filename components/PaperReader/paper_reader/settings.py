"""Reuse an existing local DeepSeek credential without executing shell startup code."""

import os
import re
import shlex
from pathlib import Path


def load_existing_environment():
    existing = {key for key in ("DEEPSEEK_API_KEY", "GEMINI_API_KEY") if os.getenv(key)}
    profile = Path.home() / ".bashrc"
    if not profile.exists():
        return
    for line in profile.read_text().splitlines():
        if not re.match(r"^\s*(?:export\s+)?(?:DEEPSEEK_API_KEY|GEMINI_API_KEY)=", line):
            continue
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            continue
        if parts and parts[0] == "export":
            parts = parts[1:]
        if len(parts) == 1:
            value = parts[0].split("=", 1)[1]
            if value and not any(c in value for c in ("$", chr(96), "\n", "\r")):
                key = parts[0].split("=", 1)[0]
                if key not in existing:
                    os.environ[key] = value
