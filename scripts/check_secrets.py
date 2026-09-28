"""Small, offline high-confidence scan. Findings print locations, never secret values."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
    re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
findings = []
paths = [*ROOT.glob("*.md"), *ROOT.glob("*.yml"), ROOT / ".env.example"]
for folder in ("app", "deploy", "scripts", "alembic"):
    paths.extend((ROOT / folder).rglob("*"))
for path in paths:
    if not path.is_file() or path.suffix in {".pyc", ".db"} or "__pycache__" in path.parts:
        continue
    text = path.read_text(encoding="utf-8-sig")
    for number, line in enumerate(text.splitlines(), 1):
        if any(pattern.search(line) for pattern in PATTERNS):
            findings.append(f"{path.relative_to(ROOT)}:{number}")
if findings:
    print("\n".join(findings))
    sys.exit(1)
print("No known credential patterns found in distributable source files.")
