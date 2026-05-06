from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import typer

from cadence.executor.claude_executor import Result
from cadence.processor.prompts import build_report_api_changes_prompt
from cadence.processor.signals import (
    is_report_failed,
    parse_report_body,
)


class Executor(Protocol):
    def run(self, prompt: str) -> Result: ...


class Logger(Protocol):
    def print(self, fmt: str, *args: object) -> None: ...
    def warn(self, fmt: str, *args: object) -> None: ...
    def error(self, fmt: str, *args: object) -> None: ...
    @property
    def path(self) -> str: ...


class GitService(Protocol):
    def diff_against(self, base: str, *, paths: list[str] | None = None) -> str: ...


def run_report(
    report_type: str,
    *,
    base: str,
    stdout_only: bool,
    executor: Executor,
    git_svc: GitService,
    logger: Logger,
    local_dir: Path | None,
    public_api_paths: list[str],
    branch: str,
    default_branch: str,
    report_path: str,
) -> bool:
    if report_type != "api-changes":
        raise ValueError(f"unknown report_type: {report_type!r}")

    del base, git_svc

    prompt = build_report_api_changes_prompt(
        local_dir=local_dir,
        branch=branch,
        default_branch=default_branch,
        public_api_paths=public_api_paths,
        progress_file=logger.path,
        warn=lambda msg: logger.warn("%s", msg),
    )

    result = executor.run(prompt)

    if result.error is not None:
        logger.error("claude error: %s", result.error)
        raise RuntimeError(f"claude error: {result.error}")

    if result.idle_timed_out and not result.signal:
        logger.error("claude idle-timed out before producing a report")
        raise RuntimeError("claude idle-timed out before producing a report")

    if is_report_failed(result.signal):
        logger.error("claude reported failure")
        raise RuntimeError("claude reported failure")

    body = parse_report_body(result.output or "")
    if body is None:
        logger.error("report body not found between markers")
        raise RuntimeError("report body not found between markers")

    if not stdout_only:
        parent = os.path.dirname(report_path)
        if parent:
            os.makedirs(parent, mode=0o750, exist_ok=True)
        with open(
            os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(body)

    typer.echo(body)
    return True


__all__ = ["run_report"]
