from __future__ import annotations

import glob
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import IO, Protocol

from cadence.config import ColorConfig

_DATE_PREFIX_RE = re.compile(r"^[\d-]+")


class NoPlansFoundError(Exception):
    pass


def extract_branch_name(plan_file: str) -> str:
    stem = Path(plan_file).stem
    stripped = _DATE_PREFIX_RE.sub("", stem)
    stripped = stripped.lstrip("-")
    if not stripped:
        return stem
    return stripped


def _is_completed_plan(path: str) -> bool:
    return Path(path).stem.endswith("-completed")


class _StdIO(Protocol):
    def readline(self) -> str: ...


class Selector:
    def __init__(
        self,
        plans_dir: str,
        colors: ColorConfig,
        *,
        stdin: IO[str] | None = None,
        stdout: IO[str] | None = None,
    ) -> None:
        self.plans_dir = plans_dir
        self.colors = colors
        self._stdin: IO[str] = stdin if stdin is not None else sys.stdin
        self._stdout: IO[str] = stdout if stdout is not None else sys.stdout

    def select(self, plan_file: str, optional: bool) -> str:
        if plan_file:
            p = Path(plan_file)
            if not p.exists():
                raise FileNotFoundError(f"plan file not found: {plan_file}")
            return str(p.resolve())

        if optional:
            return ""

        return self._select_with_numbers()

    def _select_with_numbers(self) -> str:
        plans_dir = self.plans_dir
        if not os.path.isdir(plans_dir):
            raise NoPlansFoundError(f"plans directory not found: {plans_dir}")

        files = sorted(
            f
            for f in glob.glob(os.path.join(plans_dir, "*.md"))
            if not _is_completed_plan(f)
        )
        if not files:
            raise NoPlansFoundError(f"no plan files found in {plans_dir}")

        if len(files) == 1:
            return str(Path(files[0]).resolve())

        self._stdout.write("\nAvailable plans:\n")
        for i, f in enumerate(files, 1):
            self._stdout.write(f"  {i}. {os.path.basename(f)}\n")
        self._stdout.flush()

        while True:
            self._stdout.write(f"Enter number (1-{len(files)}): ")
            self._stdout.flush()
            raw = self._stdin.readline()
            if not raw:
                raise RuntimeError("no plan selected")
            raw = raw.strip()
            if not raw:
                continue
            try:
                num = int(raw)
            except ValueError:
                continue
            if 1 <= num <= len(files):
                return str(Path(files[num - 1]).resolve())

    def find_recent(self, start_time: datetime) -> str:
        plans_dir = self.plans_dir
        if not os.path.isdir(plans_dir):
            return ""

        start_ts = start_time.timestamp()
        best: tuple[float, str] | None = None
        for f in glob.glob(os.path.join(plans_dir, "*.md")):
            if _is_completed_plan(f):
                continue
            try:
                mtime = os.path.getmtime(f)
            except OSError:
                continue
            if mtime < start_ts:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, f)

        if best is None:
            return ""
        return str(Path(best[1]).resolve())
