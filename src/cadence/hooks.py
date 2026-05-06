from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class HookOutcome:
    ran: bool
    exit_code: int
    timed_out: bool

    @property
    def failed(self) -> bool:
        return self.ran and self.exit_code != 0


class HookLogger(Protocol):
    def print(self, fmt: str, *args: object) -> None: ...
    def warn(self, fmt: str, *args: object) -> None: ...


def _forward_output(
    stdout: str | None,
    stderr: str | None,
    *,
    kind: str,
    phase: str,
    logger: HookLogger,
) -> None:
    for stream in (stdout, stderr):
        if not stream:
            continue
        for line in stream.splitlines():
            if line:
                logger.print("[hook:%s-%s] %s", kind, phase, line)


def run_hook(
    *,
    phase: str,
    kind: Literal["pre", "post"],
    hooks_dir: str,
    enabled: bool,
    env: dict[str, str],
    cwd: str,
    logger: HookLogger,
    timeout: int,
) -> HookOutcome:
    if not enabled:
        return HookOutcome(ran=False, exit_code=0, timed_out=False)

    script_path = os.path.join(hooks_dir, f"{kind}-{phase}.sh")

    if not os.path.isfile(script_path):
        return HookOutcome(ran=False, exit_code=0, timed_out=False)

    if not os.access(script_path, os.X_OK):
        logger.warn("hook %s not executable; skipping", script_path)
        return HookOutcome(ran=False, exit_code=0, timed_out=False)

    full_env = {**os.environ, **env}

    try:
        proc = subprocess.run(
            [script_path],
            cwd=cwd,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _forward_output(
            exc.stdout if isinstance(exc.stdout, str) else None,
            exc.stderr if isinstance(exc.stderr, str) else None,
            kind=kind,
            phase=phase,
            logger=logger,
        )
        logger.warn("hook %s timed out after %ds", script_path, timeout)
        return HookOutcome(ran=True, exit_code=124, timed_out=True)

    _forward_output(proc.stdout, proc.stderr, kind=kind, phase=phase, logger=logger)
    return HookOutcome(ran=True, exit_code=proc.returncode, timed_out=False)
