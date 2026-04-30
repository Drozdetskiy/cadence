from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from rlx.git.backend import DiffStats, ExternalBackend
from rlx.plan import extract_branch_name


class Logger(Protocol):
    def print(self, fmt: str, *args: object) -> None: ...
    def warn(self, fmt: str, *args: object) -> None: ...
    def error(self, fmt: str, *args: object) -> None: ...


def completed_plan_path(plan_file: str) -> Path:
    p = Path(plan_file)
    return p.with_name(p.stem + "-completed" + p.suffix)


class Service:
    def __init__(self, path: str, log: Logger, *, command: str = "git") -> None:
        self._repo = ExternalBackend(path, command=command)
        self._log = log
        self._trailer: str = ""

    def set_commit_trailer(self, trailer: str) -> None:
        self._trailer = trailer or ""

    def _append_trailer(self, msg: str) -> str:
        if not self._trailer:
            return msg
        return msg + "\n\n" + self._trailer

    def root(self) -> str:
        return self._repo.root()

    def head_hash(self) -> str:
        return self._repo.head_hash()

    def diff_fingerprint(self) -> str:
        return self._repo.diff_fingerprint()

    def current_branch(self) -> str:
        return self._repo.current_branch()

    def get_default_branch(self) -> str:
        return self._repo.get_default_branch()

    def has_commits(self) -> bool:
        return self._repo.has_commits()

    def diff_stats(self, base_branch: str) -> DiffStats:
        return self._repo.diff_stats(base_branch)

    def file_has_changes(self, path: str) -> bool:
        return self._repo.file_has_changes(path)

    def is_default_branch(self, default_branch: str) -> bool:
        current = self._repo.current_branch()
        if not current:
            return False
        if default_branch:
            trimmed = default_branch
            if trimmed.startswith("origin/"):
                trimmed = trimmed[len("origin/"):]
            return current == trimmed
        return current in ("main", "master")

    def create_branch(self, name: str) -> None:
        self._repo.create_branch(name)

    def create_branch_for_plan(
        self, plan_file: str, default_branch: str
    ) -> None:
        resolved_plan = self._resolve_filesystem_case(plan_file)
        branch, needs_commit = self._prepare_plan_branch(
            resolved_plan, default_branch
        )
        if not branch:
            return

        if self._repo.branch_exists(branch):
            self._repo.checkout_branch(branch)
            self._log.print("switched to existing branch %s", branch)
        else:
            self._repo.create_branch(branch)
            self._log.print("created branch %s", branch)

        if needs_commit:
            self._repo.add(resolved_plan)
            self._repo.commit(self._append_trailer(f"add plan: {branch}"))
            self._log.print("committed plan file on %s", branch)

    def _prepare_plan_branch(
        self, plan_file: str, default_branch: str
    ) -> tuple[str, bool]:
        if not self.is_default_branch(default_branch):
            return "", False

        branch = extract_branch_name(plan_file)
        if not branch:
            raise RuntimeError(
                f"cannot extract branch name from plan: {plan_file}"
            )

        other = self._repo.has_changes_other_than(plan_file)
        if other:
            raise RuntimeError(
                "repository has uncommitted changes other than the plan file: "
                + ", ".join(other)
            )

        needs_commit = self._repo.file_has_changes(plan_file)
        return branch, needs_commit

    def commit_plan_file(self, plan_file: str) -> None:
        resolved = self._resolve_filesystem_case(plan_file)
        branch = extract_branch_name(resolved)
        self._repo.add(resolved)
        self._repo.commit(self._append_trailer(f"add plan: {branch}"))

    def mark_plan_completed(self, plan_file: str) -> None:
        resolved = self._resolve_filesystem_case(plan_file)
        src = Path(resolved)
        dst = completed_plan_path(resolved)

        if not src.exists():
            if dst.exists():
                self._log.print("plan already marked completed: %s", str(dst))
                return
            raise FileNotFoundError(f"plan file not found: {plan_file}")

        if dst.exists():
            raise FileExistsError(
                f"completed plan already exists, refusing to overwrite: {dst}"
            )

        os.rename(str(src), str(dst))
        self._log.print("marked plan completed: %s", str(dst))

    def ensure_has_commits(self, prompt_fn: Callable[[], bool]) -> None:
        if self._repo.has_commits():
            return
        if not prompt_fn():
            raise RuntimeError("repository has no commits; aborted by user")
        self._repo.create_initial_commit(
            self._append_trailer("initial commit")
        )
        self._log.print("created initial commit")

    def _resolve_filesystem_case(self, path: str) -> str:
        parent = os.path.dirname(path) or "."
        name = os.path.basename(path)
        try:
            entries = os.listdir(parent)
        except OSError:
            return path
        if name in entries:
            return path
        lowered = name.casefold()
        for entry in entries:
            if entry.casefold() == lowered:
                return os.path.join(parent, entry) if os.path.dirname(path) else entry
        return path
