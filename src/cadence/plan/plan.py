from __future__ import annotations

import re
from pathlib import Path

_DATE_PREFIX_RE = re.compile(r"^[\d-]+")


def extract_branch_name(plan_file: str) -> str:
    stem = Path(plan_file).stem
    stripped = _DATE_PREFIX_RE.sub("", stem)
    stripped = stripped.lstrip("-")
    if not stripped:
        return stem
    return stripped
