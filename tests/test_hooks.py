from __future__ import annotations

import os
from pathlib import Path

from cadence.hooks import HookOutcome, run_hook


class CapturingLogger:
    def __init__(self) -> None:
        self.prints: list[str] = []
        self.warns: list[str] = []

    def print(self, fmt: str, *args: object) -> None:
        self.prints.append(fmt % args if args else fmt)

    def warn(self, fmt: str, *args: object) -> None:
        self.warns.append(fmt % args if args else fmt)


def _write_script(path: Path, body: str, *, executable: bool = True) -> Path:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    if executable:
        os.chmod(path, 0o755)
    else:
        os.chmod(path, 0o644)
    return path


class TestHookOutcome:
    def test_failed_true_when_ran_and_nonzero(self) -> None:
        assert HookOutcome(ran=True, exit_code=5, timed_out=False).failed is True

    def test_failed_false_when_not_ran(self) -> None:
        assert HookOutcome(ran=False, exit_code=0, timed_out=False).failed is False

    def test_failed_false_when_zero(self) -> None:
        assert HookOutcome(ran=True, exit_code=0, timed_out=False).failed is False

    def test_failed_false_for_skipped_with_nonzero(self) -> None:
        assert HookOutcome(ran=False, exit_code=99, timed_out=False).failed is False


class TestRunHook:
    def test_disabled_returns_not_ran(self, tmp_path: Path) -> None:
        marker = tmp_path / "ran.txt"
        _write_script(tmp_path / "pre-task.sh", f"echo ran > {marker}")
        logger = CapturingLogger()

        outcome = run_hook(
            phase="task",
            kind="pre",
            hooks_dir=str(tmp_path),
            enabled=False,
            env={},
            cwd=str(tmp_path),
            logger=logger,
            timeout=10,
        )

        assert outcome == HookOutcome(ran=False, exit_code=0, timed_out=False)
        assert not marker.exists()
        assert logger.prints == []
        assert logger.warns == []

    def test_missing_script_silent_skip(self, tmp_path: Path) -> None:
        logger = CapturingLogger()

        outcome = run_hook(
            phase="task",
            kind="pre",
            hooks_dir=str(tmp_path),
            enabled=True,
            env={},
            cwd=str(tmp_path),
            logger=logger,
            timeout=10,
        )

        assert outcome.ran is False
        assert outcome.exit_code == 0
        assert outcome.timed_out is False
        assert logger.prints == []
        assert logger.warns == []

    def test_not_executable_warns_and_skips(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path / "pre-task.sh", "echo hi", executable=False)
        logger = CapturingLogger()

        outcome = run_hook(
            phase="task",
            kind="pre",
            hooks_dir=str(tmp_path),
            enabled=True,
            env={},
            cwd=str(tmp_path),
            logger=logger,
            timeout=10,
        )

        assert outcome.ran is False
        assert len(logger.warns) == 1
        assert "not executable" in logger.warns[0]
        assert str(script) in logger.warns[0]

    def test_successful_pre_hook_forwards_stdout(self, tmp_path: Path) -> None:
        _write_script(tmp_path / "pre-task.sh", "echo first\necho second")
        logger = CapturingLogger()

        outcome = run_hook(
            phase="task",
            kind="pre",
            hooks_dir=str(tmp_path),
            enabled=True,
            env={},
            cwd=str(tmp_path),
            logger=logger,
            timeout=10,
        )

        assert outcome == HookOutcome(ran=True, exit_code=0, timed_out=False)
        assert "[hook:pre-task] first" in logger.prints
        assert "[hook:pre-task] second" in logger.prints
        assert logger.warns == []

    def test_failing_pre_hook_returns_exit_code(self, tmp_path: Path) -> None:
        _write_script(tmp_path / "pre-task.sh", "exit 5")
        logger = CapturingLogger()

        outcome = run_hook(
            phase="task",
            kind="pre",
            hooks_dir=str(tmp_path),
            enabled=True,
            env={},
            cwd=str(tmp_path),
            logger=logger,
            timeout=10,
        )

        assert outcome.ran is True
        assert outcome.exit_code == 5
        assert outcome.timed_out is False
        assert outcome.failed is True

    def test_stderr_forwarded_with_prefix(self, tmp_path: Path) -> None:
        _write_script(
            tmp_path / "post-plan.sh",
            'echo "to stdout"\necho "to stderr" >&2',
        )
        logger = CapturingLogger()

        outcome = run_hook(
            phase="plan",
            kind="post",
            hooks_dir=str(tmp_path),
            enabled=True,
            env={},
            cwd=str(tmp_path),
            logger=logger,
            timeout=10,
        )

        assert outcome.ran is True
        assert "[hook:post-plan] to stdout" in logger.prints
        assert "[hook:post-plan] to stderr" in logger.prints

    def test_env_passthrough(self, tmp_path: Path) -> None:
        out_file = tmp_path / "env-out.txt"
        body = (
            f"{{\n"
            f'  echo "PHASE=$CADENCE_PHASE"\n'
            f'  echo "HOOK=$CADENCE_HOOK"\n'
            f'  echo "BRANCH=$CADENCE_BRANCH"\n'
            f'  echo "TASK_NAME=$CADENCE_TASK_NAME"\n'
            f'  echo "TASKS_ROOT=$CADENCE_TASKS_ROOT"\n'
            f'  echo "PHASE_RESULT=$CADENCE_PHASE_RESULT"\n'
            f'  echo "PHASE_DURATION_MS=$CADENCE_PHASE_DURATION_MS"\n'
            f'  echo "REPORT_TYPE=$CADENCE_REPORT_TYPE"\n'
            f"}} > {out_file}\n"
        )
        _write_script(tmp_path / "post-report.sh", body)
        logger = CapturingLogger()

        env = {
            "CADENCE_PHASE": "report",
            "CADENCE_HOOK": "post",
            "CADENCE_BRANCH": "0042-feature",
            "CADENCE_TASK_NAME": "0042-feature",
            "CADENCE_TASKS_ROOT": "/abs/cdc-tasks",
            "CADENCE_PHASE_RESULT": "success",
            "CADENCE_PHASE_DURATION_MS": "1234",
            "CADENCE_REPORT_TYPE": "api-changes",
        }
        outcome = run_hook(
            phase="report",
            kind="post",
            hooks_dir=str(tmp_path),
            enabled=True,
            env=env,
            cwd=str(tmp_path),
            logger=logger,
            timeout=10,
        )

        assert outcome.ran is True
        assert outcome.exit_code == 0
        contents = out_file.read_text()
        assert "PHASE=report" in contents
        assert "HOOK=post" in contents
        assert "BRANCH=0042-feature" in contents
        assert "TASK_NAME=0042-feature" in contents
        assert "TASKS_ROOT=/abs/cdc-tasks" in contents
        assert "PHASE_RESULT=success" in contents
        assert "PHASE_DURATION_MS=1234" in contents
        assert "REPORT_TYPE=api-changes" in contents

    def test_timeout(self, tmp_path: Path) -> None:
        _write_script(tmp_path / "pre-task.sh", "sleep 2")
        logger = CapturingLogger()

        outcome = run_hook(
            phase="task",
            kind="pre",
            hooks_dir=str(tmp_path),
            enabled=True,
            env={},
            cwd=str(tmp_path),
            logger=logger,
            timeout=1,
        )

        assert outcome.ran is True
        assert outcome.timed_out is True
        assert outcome.exit_code == 124
        assert len(logger.warns) == 1
        assert "timed out" in logger.warns[0]

    def test_cwd_is_passed_to_hook(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        out_file = repo_root / "pwd.txt"
        _write_script(hooks_dir / "pre-task.sh", f"pwd > {out_file}")
        logger = CapturingLogger()

        outcome = run_hook(
            phase="task",
            kind="pre",
            hooks_dir=str(hooks_dir),
            enabled=True,
            env={},
            cwd=str(repo_root),
            logger=logger,
            timeout=10,
        )

        assert outcome.ran is True
        assert outcome.exit_code == 0
        assert out_file.read_text().strip() == str(repo_root)

    def test_path_passthrough_allows_basic_commands(self, tmp_path: Path) -> None:
        _write_script(tmp_path / "pre-task.sh", "echo path-ok")
        logger = CapturingLogger()

        outcome = run_hook(
            phase="task",
            kind="pre",
            hooks_dir=str(tmp_path),
            enabled=True,
            env={},
            cwd=str(tmp_path),
            logger=logger,
            timeout=10,
        )

        assert outcome.ran is True
        assert outcome.exit_code == 0
        assert "[hook:pre-task] path-ok" in logger.prints
