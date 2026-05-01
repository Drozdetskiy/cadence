from __future__ import annotations

import subprocess

from cadence.git.backend import DiffStats, ExternalBackend
from cadence.git.service import Service


def is_git_repo(path: str = ".", *, vcs_command: str = "git") -> bool:
    try:
        result = subprocess.run(
            [vcs_command, "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def get_default_branch(path: str = ".", *, vcs_command: str = "git") -> str:
    try:
        result = subprocess.run(
            [vcs_command, "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ref = result.stdout.strip()
            prefix = "refs/remotes/origin/"
            if ref.startswith(prefix):
                branch = ref[len(prefix):]
                check = subprocess.run(
                    [vcs_command, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                    cwd=path,
                    capture_output=True,
                )
                if check.returncode == 0:
                    return branch
                return f"origin/{branch}"
    except FileNotFoundError:
        return "master"

    for candidate in ("main", "master", "trunk", "develop"):
        check = subprocess.run(
            [vcs_command, "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"],
            cwd=path,
            capture_output=True,
        )
        if check.returncode == 0:
            return candidate

    return "master"


def head_hash(path: str = ".", *, vcs_command: str = "git") -> str:
    result = subprocess.run(
        [vcs_command, "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


class GitChecker:
    def __init__(self, path: str = ".", *, vcs_command: str = "git") -> None:
        self._path = path
        self._vcs_command = vcs_command

    def head_hash(self) -> str:
        return head_hash(self._path, vcs_command=self._vcs_command)

    def diff_fingerprint(self) -> str:
        result = subprocess.run(
            [self._vcs_command, "diff", "HEAD"],
            cwd=self._path,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""


__all__ = [
    "DiffStats",
    "ExternalBackend",
    "GitChecker",
    "Service",
    "get_default_branch",
    "head_hash",
    "is_git_repo",
]
