from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DiffStats:
    files: int = 0
    additions: int = 0
    deletions: int = 0


class ExternalBackend:
    def __init__(self, path: str, *, command: str = "git") -> None:
        self._command = command
        resolved = str(Path(path).resolve())
        try:
            result = subprocess.run(
                [self._command, "rev-parse", "--show-toplevel"],
                cwd=resolved,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"git not found: {self._command}") from exc
        if result.returncode != 0:
            stderr = result.stderr.strip() or "not a git repository"
            raise RuntimeError(f"git: {stderr}")
        root = result.stdout.strip()
        if not root:
            raise RuntimeError("git rev-parse returned empty toplevel")
        self._path = os.path.realpath(root)

    def _run(self, *args: str, env_extra: dict[str, str] | None = None) -> str:
        env = None
        if env_extra:
            env = {**os.environ, **env_extra}
        try:
            result = subprocess.run(
                [self._command, *args],
                cwd=self._path,
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"git not found: {self._command}") from exc
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise RuntimeError(
                f"git {' '.join(args)} failed (exit {result.returncode}): {stderr}"
            )
        return result.stdout.rstrip()

    def _run_with_status(
        self, *args: str, env_extra: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        env = None
        if env_extra:
            env = {**os.environ, **env_extra}
        try:
            result = subprocess.run(
                [self._command, *args],
                cwd=self._path,
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"git not found: {self._command}") from exc
        return result.returncode, result.stdout.rstrip(), result.stderr.strip()

    def root(self) -> str:
        return self._path

    def head_hash(self) -> str:
        code, stdout, _ = self._run_with_status("rev-parse", "HEAD")
        if code != 0:
            return ""
        return stdout.strip()

    def has_commits(self) -> bool:
        code, _, stderr = self._run_with_status(
            "rev-parse", "HEAD", env_extra={"LC_ALL": "C"}
        )
        if code == 0:
            return True
        if code == 128 and "ambiguous argument" in stderr.lower():
            return False
        raise RuntimeError(f"git rev-parse HEAD failed (exit {code}): {stderr}")

    def current_branch(self) -> str:
        code, stdout, stderr = self._run_with_status(
            "symbolic-ref", "--short", "HEAD", env_extra={"LC_ALL": "C"}
        )
        if code == 0:
            return stdout.strip()
        if code == 128 and "not a symbolic ref" in stderr.lower():
            return ""
        raise RuntimeError(f"git symbolic-ref failed (exit {code}): {stderr}")

    def get_default_branch(self) -> str:
        code, stdout, _ = self._run_with_status(
            "symbolic-ref", "refs/remotes/origin/HEAD"
        )
        if code == 0:
            ref = stdout.strip()
            prefix = "refs/remotes/origin/"
            if ref.startswith(prefix):
                name = ref[len(prefix):]
                if self._ref_exists(f"refs/heads/{name}"):
                    return name
                return f"origin/{name}"

        for candidate in ("main", "master", "trunk", "develop"):
            if self._ref_exists(f"refs/heads/{candidate}"):
                return candidate

        return "master"

    def branch_exists(self, name: str) -> bool:
        return self._ref_exists(f"refs/heads/{name}")

    def create_branch(self, name: str) -> None:
        self._run("checkout", "-b", name)

    def checkout_branch(self, name: str) -> None:
        self._run("checkout", name)

    def diff_fingerprint(self) -> str:
        hasher = hashlib.sha256()
        code, diff_out, _ = self._run_with_status("diff", "HEAD")
        if code == 0:
            hasher.update(diff_out.encode("utf-8"))

        code, untracked, _ = self._run_with_status(
            "ls-files", "-z", "--others", "--exclude-standard"
        )
        if code == 0 and untracked:
            names = [n for n in untracked.split("\0") if n]
            for name in names:
                hasher.update(b"\0U\0")
                hasher.update(name.encode("utf-8"))
                hcode, blob, _ = self._run_with_status("hash-object", "--", name)
                if hcode == 0:
                    hasher.update(b"\0H\0")
                    hasher.update(blob.strip().encode("utf-8"))
        return hasher.hexdigest()

    def is_dirty(self) -> bool:
        code, stdout, _ = self._run_with_status("status", "--porcelain")
        if code != 0:
            return False
        for line in stdout.splitlines():
            if not line:
                continue
            if line.startswith("??"):
                continue
            return True
        return False

    def file_has_changes(self, path: str) -> bool:
        rel = self._to_relative(path)
        code, stdout, _ = self._run_with_status(
            "status", "--porcelain", "-uall", "--", rel
        )
        if code != 0:
            return False
        return bool(stdout.strip())

    def has_changes_other_than(self, path: str) -> list[str]:
        rel = self._to_relative(path)
        rel_cf = rel.casefold()
        code, stdout, _ = self._run_with_status("status", "--porcelain", "-uall")
        if code != 0:
            return []
        out: list[str] = []
        for line in stdout.splitlines():
            if not line:
                continue
            extracted = self._extract_path_from_porcelain(line)
            if not extracted:
                continue
            if extracted.casefold() == rel_cf:
                continue
            out.append(extracted)
        return out

    def add(self, path: str) -> None:
        rel = self._to_relative(path)
        self._run("add", "--", rel)

    def move_file(self, src: str, dst: str) -> None:
        src_rel = self._to_relative(src)
        dst_rel = self._to_relative(dst)
        self._run("mv", "--", src_rel, dst_rel)

    def commit(self, msg: str) -> None:
        self._run("commit", "-m", msg)

    def commit_files(self, msg: str, *paths: str) -> None:
        rels = [self._to_relative(p) for p in paths]
        self._run("commit", "-m", msg, "--", *rels)

    def create_initial_commit(self, msg: str) -> None:
        self._run("add", "-A")
        code, stdout, _ = self._run_with_status(
            "diff", "--cached", "--name-only"
        )
        if code != 0 or not stdout.strip():
            raise RuntimeError("nothing to commit for initial commit")
        self._run("commit", "-m", msg)

    def diff_stats(self, base_branch: str) -> DiffStats:
        ref = self._resolve_ref(base_branch)
        if not ref:
            return DiffStats()

        code, base_hash, _ = self._run_with_status("rev-parse", ref)
        if code != 0 or not base_hash.strip():
            return DiffStats()
        head = self.head_hash()
        if head and head == base_hash.strip():
            return DiffStats()

        code, stdout, _ = self._run_with_status(
            "diff", "--numstat", f"{ref}...HEAD"
        )
        if code != 0:
            return DiffStats()

        stats = DiffStats()
        for line in stdout.splitlines():
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            adds_s, dels_s = parts[0], parts[1]
            stats.files += 1
            if adds_s == "-" or dels_s == "-":
                continue
            try:
                stats.additions += int(adds_s)
                stats.deletions += int(dels_s)
            except ValueError:
                continue
        return stats

    def _resolve_ref(self, branch_name: str) -> str:
        if not branch_name:
            return ""
        local = f"refs/heads/{branch_name}"
        if self._ref_exists(local):
            return local
        remote = f"refs/remotes/origin/{branch_name}"
        if self._ref_exists(remote):
            return remote
        if branch_name.startswith("origin/"):
            remote_name = branch_name[len("origin/"):]
            alt = f"refs/remotes/origin/{remote_name}"
            if self._ref_exists(alt):
                return alt
        code, _, _ = self._run_with_status(
            "rev-parse", "--verify", "--quiet", branch_name
        )
        if code == 0:
            return branch_name
        return ""

    def _ref_exists(self, ref: str) -> bool:
        code, _, _ = self._run_with_status(
            "show-ref", "--verify", "--quiet", ref
        )
        return code == 0

    def _to_relative(self, path: str) -> str:
        if not os.path.isabs(path):
            norm = os.path.normpath(path)
            parts = norm.split(os.sep)
            if ".." in parts:
                raise ValueError(f"path escapes repository root: {path}")
            return norm
        abs_path = os.path.realpath(os.path.dirname(path))
        base = os.path.basename(path)
        full = os.path.join(abs_path, base)
        rel = os.path.relpath(full, self._path)
        parts = rel.split(os.sep)
        if ".." in parts:
            raise ValueError(f"path outside repository: {path}")
        return rel

    def _extract_path_from_porcelain(self, line: str) -> str:
        if len(line) < 4:
            return ""
        rest = line[3:]
        if " -> " in rest:
            _, _, new = rest.partition(" -> ")
            return new.strip().strip('"')
        return rest.strip().strip('"')
