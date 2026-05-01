from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO

from rich.console import Console
from rich.style import Style
from rich.text import Text

from cadence.progress.colors import Colors
from cadence.progress.flock import lock_file, unlock_file
from cadence.status import Mode, PhaseHolder, Section


@dataclass
class ProgressLoggerConfig:
    progress_path: str
    plan_file: str = ""
    branch: str = ""
    mode: Mode = Mode.PLAN
    plan_description: str = ""
    no_color: bool = False


_DASHES = "-" * 60


def sanitize_plan_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[\\/]+", "-", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name[:50]
    name = name.strip("-")
    return name or "unnamed"


def _is_progress_completed(f: IO[str]) -> bool:
    f.seek(0, 2)
    size = f.tell()
    if size == 0:
        return False
    read_size = min(size, 256)
    f.seek(size - read_size)
    tail = f.read(read_size)
    return _DASHES in tail and "Completed:" in tail


def _timestamp() -> str:
    return datetime.now(tz=UTC).strftime("[%y-%m-%d %H:%M:%S]")


def _now_str() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


class _ProgressFile:
    """Owns the file handle, lock, header/restart markers."""

    def __init__(self, cfg: ProgressLoggerConfig) -> None:
        progress_dir = os.path.dirname(cfg.progress_path)
        if progress_dir:
            os.makedirs(progress_dir, mode=0o750, exist_ok=True)
        self._path = os.path.abspath(cfg.progress_path)

        self._file: IO[str] = open(self._path, "a+", encoding="utf-8")  # noqa: SIM115
        try:
            os.chmod(self._path, 0o600)
            lock_file(self._file)

            if _is_progress_completed(self._file):
                self._file.seek(0)
                self._file.truncate()
                self._write_header(cfg)
            elif self._file.tell() > 0:
                self._write_restart()
            else:
                self._write_header(cfg)
        except Exception:
            self._file.close()
            raise

    @property
    def path(self) -> str:
        return self._path

    def write(self, line: str) -> None:
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        try:
            unlock_file(self._file)
        finally:
            self._file.close()

    def _write_header(self, cfg: ProgressLoggerConfig) -> None:
        self.write("# CADENCE Progress Log")
        if cfg.plan_file:
            self.write(f"Plan: {cfg.plan_file}")
        self.write(f"Branch: {cfg.branch}")
        self.write(f"Mode: {cfg.mode}")
        self.write(f"Started: {_now_str()}")
        self.write(_DASHES)
        self.write("")

    def _write_restart(self) -> None:
        self.write("")
        self.write("")
        self.write(f"--- restarted at {_now_str()} ---")
        self.write("")
        self.write("")


class _PartialLineBuffer:
    """Tracks whether stdout is at line-start for correct timestamp placement."""

    def __init__(self) -> None:
        self._at_line_start = True

    @property
    def at_line_start(self) -> bool:
        return self._at_line_start

    def mark_line_start(self) -> None:
        self._at_line_start = True

    def mark_mid_line(self) -> None:
        self._at_line_start = False

    def ensure_newline(self, out: IO[str]) -> None:
        if not self._at_line_start:
            out.write("\n")
            self._at_line_start = True


class Logger:
    def __init__(self, cfg: ProgressLoggerConfig, colors: Colors, holder: PhaseHolder) -> None:
        self._colors = colors
        self._holder = holder
        self._start_time = datetime.now(tz=UTC)
        self._no_color = cfg.no_color
        self._console = Console(file=sys.stdout, no_color=cfg.no_color, highlight=False)
        self._buffer = _PartialLineBuffer()
        self._file = _ProgressFile(cfg)

    @property
    def path(self) -> str:
        return self._file.path

    def _emit(self, prefix: str, msg: str, style: Style) -> None:
        ts = _timestamp()
        self._file.write(f"{ts} {prefix}{msg}")
        self._buffer.ensure_newline(sys.stdout)
        text = Text()
        text.append(ts, style=self._colors.timestamp())
        text.append(f" {prefix}{msg}", style=style)
        self._console.print(text)
        self._buffer.mark_line_start()

    def print(self, fmt: str, *args: object) -> None:
        msg = fmt % args if args else fmt
        self._emit("", msg, self._colors.for_phase(self._holder.get()))

    def print_section(self, section: Section) -> None:
        ts = _timestamp()
        self._file.write(f"\n{ts} --- {section.label} ---\n")
        self._buffer.ensure_newline(sys.stdout)
        text = Text()
        text.append("\n")
        text.append(ts, style=self._colors.timestamp())
        text.append(f" --- {section.label} ---\n")
        self._console.print(text)
        self._buffer.mark_line_start()

    def error(self, fmt: str, *args: object) -> None:
        msg = fmt % args if args else fmt
        self._emit("ERROR: ", msg, self._colors.error())

    def warn(self, fmt: str, *args: object) -> None:
        msg = fmt % args if args else fmt
        self._emit("WARN: ", msg, self._colors.warn())

    def print_aligned(self, text: str) -> None:
        if not text:
            return
        ts = _timestamp()
        for line in text.rstrip("\n").split("\n"):
            if not line:
                continue
            self._file.write(f"{ts} {line}")
        self._buffer.ensure_newline(sys.stdout)
        style = self._colors.for_phase(self._holder.get())
        for line in text.rstrip("\n").split("\n"):
            if not line:
                continue
            t = Text()
            t.append(ts, style=self._colors.timestamp())
            t.append(f" {line}", style=style)
            self._console.print(t)
        self._buffer.mark_line_start()

    def log_question(self, question: str, options: list[str]) -> None:
        ts = _timestamp()
        self._file.write(f"{ts} QUESTION: {question}")
        self._file.write(f"{ts} OPTIONS: {', '.join(options)}")

    def log_answer(self, answer: str) -> None:
        ts = _timestamp()
        self._file.write(f"{ts} ANSWER: {answer}")

    def log_claude_output(self, text: str) -> None:
        if not text:
            return
        ts = _timestamp()
        for line in text.rstrip("\n").split("\n"):
            self._file.write(f"{ts} {line}")
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if self._buffer.at_line_start and part:
                self._console.print(Text(f"{ts} ", style=self._colors.timestamp()), end="")
                self._buffer.mark_mid_line()
            sys.stdout.write(part)
            if i < len(parts) - 1:
                sys.stdout.write("\n")
                self._buffer.mark_line_start()
        sys.stdout.flush()

    def elapsed(self) -> str:
        delta = datetime.now(tz=UTC) - self._start_time
        total_seconds = int(delta.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours}h{minutes:02d}m"
        return f"{minutes}m{seconds:02d}s"

    def close(self, *, success: bool = True) -> None:
        try:
            elapsed = self.elapsed()
            now = _now_str()
            self._file.write(_DASHES)
            if success:
                self._file.write(f"Completed: {now} ({elapsed})")
            else:
                self._file.write(f"Failed: {now} ({elapsed})")
        finally:
            self._file.close()
