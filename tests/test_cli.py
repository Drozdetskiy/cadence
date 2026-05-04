from __future__ import annotations

import signal
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cadence.cli import (
    _parse_chain_file,
    _resolve_chain_default_branch,
    _setup_runtime,
    _sigint,
    _validate_chain_tasks,
    _validate_flags,
    check_claude_dep,
    derive_plan_path,
    determine_mode,
    display_stats,
    find_existing_plan,
    resolve_version,
    run_chain_mode,
    run_plan_mode,
    run_review_mode,
    run_run_mode,
    run_squash_mode,
    run_task_init_mode,
    run_task_mode,
    to_rel_path,
)
from cadence.executor.claude_executor import Result
from cadence.git import DiffStats
from cadence.processor.runner import UserAbortedError
from cadence.status import Mode, SignalCompleted, SignalPlanReady, SignalReviewDone


class TestResolveVersion:
    def test_returns_version_string(self) -> None:
        from cadence import __version__

        v = resolve_version()
        assert v == __version__

    def test_does_not_return_unknown(self) -> None:
        assert resolve_version() != "unknown"


class TestDetermineMode:
    def test_plan_mode(self, tmp_path: Path) -> None:
        assert determine_mode(tmp_path / "f.md", None, False) == Mode.PLAN

    def test_task_mode(self, tmp_path: Path) -> None:
        assert determine_mode(None, tmp_path / "f.md", False) == Mode.FULL

    def test_review_mode(self) -> None:
        assert determine_mode(None, None, True) == Mode.REVIEW

    def test_plan_takes_priority_over_review(self, tmp_path: Path) -> None:
        assert determine_mode(tmp_path / "f.md", None, True) == Mode.PLAN

    def test_plan_takes_priority_over_task(self, tmp_path: Path) -> None:
        result = determine_mode(tmp_path / "a.md", tmp_path / "b.md", False)
        assert result == Mode.PLAN


class TestValidateFlags:
    def test_no_flags_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, None)
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "--task-init" in err
        assert "--run" in err

    def test_task_init_alone_passes(self) -> None:
        _validate_flags(None, None, False, False, None, "feat-x")

    def test_task_init_with_plan_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(tmp_path / "p", None, False, False, None, "feat-x")
        assert excinfo.value.code == 1
        assert "--task-init is mutually exclusive" in capsys.readouterr().err

    def test_task_init_with_task_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, tmp_path / "p", False, False, None, "feat-x")
        assert excinfo.value.code == 1
        assert "--task-init is mutually exclusive" in capsys.readouterr().err

    def test_task_init_with_review_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, True, False, None, "feat-x")
        assert excinfo.value.code == 1
        assert "--task-init is mutually exclusive" in capsys.readouterr().err

    def test_task_init_with_impl_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, True, None, "feat-x")
        assert excinfo.value.code == 1
        assert "--task-init is incompatible with --impl" in capsys.readouterr().err

    def test_task_init_with_base_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, "main", "feat-x")
        assert excinfo.value.code == 1
        assert "--task-init is incompatible with --base" in capsys.readouterr().err

    def test_plan_and_task_mutually_exclusive(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(tmp_path / "a", tmp_path / "b", False, False, None)
        assert excinfo.value.code == 1
        assert "mutually exclusive" in capsys.readouterr().err

    def test_review_with_impl_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, True, True, None)
        assert excinfo.value.code == 1
        assert "--review is incompatible with --impl" in capsys.readouterr().err

    def test_impl_without_plan_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, True, None)
        assert excinfo.value.code == 1
        assert "--impl requires --plan" in capsys.readouterr().err

    def test_base_without_review_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, "main")
        assert excinfo.value.code == 1
        assert "--base is only valid with --review" in capsys.readouterr().err

    def test_valid_plan_alone_passes(self, tmp_path: Path) -> None:
        _validate_flags(tmp_path / "p", None, False, False, None)

    def test_valid_task_alone_passes(self, tmp_path: Path) -> None:
        _validate_flags(None, tmp_path / "p", False, False, None)

    def test_valid_review_alone_passes(self) -> None:
        _validate_flags(None, None, True, False, None)

    def test_valid_review_with_base_passes(self) -> None:
        _validate_flags(None, None, True, False, "develop")

    def test_valid_plan_with_impl_passes(self, tmp_path: Path) -> None:
        _validate_flags(tmp_path / "p", None, False, True, None)

    def test_run_alone_passes(self) -> None:
        _validate_flags(None, None, False, False, None, run=True)

    def test_run_with_impl_passes(self) -> None:
        _validate_flags(None, None, False, True, None, run=True)

    def test_run_with_plan_errors(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(tmp_path / "p", None, False, False, None, run=True)
        assert excinfo.value.code == 1
        assert "--run is mutually exclusive" in capsys.readouterr().err

    def test_run_with_task_errors(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, tmp_path / "p", False, False, None, run=True)
        assert excinfo.value.code == 1
        assert "--run is mutually exclusive" in capsys.readouterr().err

    def test_run_with_review_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, True, False, None, run=True)
        assert excinfo.value.code == 1
        assert "--run is mutually exclusive" in capsys.readouterr().err

    def test_run_with_task_init_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, None, "feat-x", run=True)
        assert excinfo.value.code == 1
        assert "--task-init is mutually exclusive" in capsys.readouterr().err

    def test_run_with_base_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, "main", run=True)
        assert excinfo.value.code == 1
        assert "--run is incompatible with --base" in capsys.readouterr().err

    def test_squash_alone_passes(self) -> None:
        _validate_flags(None, None, False, False, None, squash=True)

    def test_squash_with_task_passes(self, tmp_path: Path) -> None:
        _validate_flags(None, tmp_path / "p", False, False, None, squash=True)

    def test_squash_with_plan_impl_passes(self, tmp_path: Path) -> None:
        _validate_flags(tmp_path / "p", None, False, True, None, squash=True)

    def test_squash_with_run_impl_passes(self) -> None:
        _validate_flags(None, None, False, True, None, run=True, squash=True)

    def test_squash_with_task_init_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, None, "feat-x", squash=True)
        assert excinfo.value.code == 1
        assert "--squash is incompatible with --task-init" in capsys.readouterr().err

    def test_squash_with_review_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, True, False, None, squash=True)
        assert excinfo.value.code == 1
        assert "--squash is incompatible with --review" in capsys.readouterr().err

    def test_squash_with_plan_no_impl_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(tmp_path / "p", None, False, False, None, squash=True)
        assert excinfo.value.code == 1
        assert "--squash with --plan/--run requires --impl" in capsys.readouterr().err

    def test_squash_with_run_no_impl_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, None, run=True, squash=True)
        assert excinfo.value.code == 1
        assert "--squash with --plan/--run requires --impl" in capsys.readouterr().err

    def test_chain_alone_passes(self, tmp_path: Path) -> None:
        _validate_flags(None, None, False, False, None, chain=tmp_path / "c.txt")

    def test_chain_with_plan_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(tmp_path / "p", None, False, False, None, chain=tmp_path / "c.txt")
        assert excinfo.value.code == 1
        assert "--chain is mutually exclusive" in capsys.readouterr().err

    def test_chain_with_task_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, tmp_path / "p", False, False, None, chain=tmp_path / "c.txt")
        assert excinfo.value.code == 1
        assert "--chain is mutually exclusive" in capsys.readouterr().err

    def test_chain_with_review_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, True, False, None, chain=tmp_path / "c.txt")
        assert excinfo.value.code == 1
        assert "--chain is mutually exclusive" in capsys.readouterr().err

    def test_chain_with_run_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, None, run=True, chain=tmp_path / "c.txt")
        assert excinfo.value.code == 1
        assert "--chain is mutually exclusive" in capsys.readouterr().err

    def test_chain_with_task_init_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, None, "feat-x", chain=tmp_path / "c.txt")
        assert excinfo.value.code == 1
        assert "--chain is mutually exclusive" in capsys.readouterr().err

    def test_chain_with_impl_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, True, None, chain=tmp_path / "c.txt")
        assert excinfo.value.code == 1
        assert "--chain is mutually exclusive" in capsys.readouterr().err

    def test_chain_with_squash_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, None, squash=True, chain=tmp_path / "c.txt")
        assert excinfo.value.code == 1
        assert "--chain is mutually exclusive" in capsys.readouterr().err

    def test_chain_with_base_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _validate_flags(None, None, False, False, "main", chain=tmp_path / "c.txt")
        assert excinfo.value.code == 1
        assert "--chain is mutually exclusive" in capsys.readouterr().err


class TestCheckClaudeDep:
    def test_passes_when_found(self) -> None:
        from cadence.config import Config

        with patch("cadence.cli.shutil.which", return_value="/usr/bin/claude"):
            check_claude_dep(Config())

    def test_fails_when_not_found(self) -> None:
        from cadence.config import Config

        with (
            patch("cadence.cli.shutil.which", return_value=None),
            pytest.raises(SystemExit),
        ):
            check_claude_dep(Config())


class TestToRelPath:
    def test_relative_path(self, tmp_path: Path) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        target = cwd / "src" / "file.py"
        target.parent.mkdir(parents=True)
        target.touch()
        with patch("cadence.cli.Path.cwd", return_value=cwd):
            result = to_rel_path(target)
        assert result == "src/file.py"

    def test_absolute_fallback(self, tmp_path: Path) -> None:
        target = tmp_path / "other" / "file.py"
        with patch("cadence.cli.Path.cwd", return_value=tmp_path / "project"):
            result = to_rel_path(target)
        assert str(target) in result


class TestSigintHandler:
    def setup_method(self) -> None:
        _sigint.reset()

    def test_first_sigint_sets_event(self) -> None:
        with pytest.raises(KeyboardInterrupt):
            _sigint(signal.SIGINT, None)
        assert _sigint.shutdown_event.is_set()

    def test_second_sigint_within_5s_exits(self) -> None:
        with pytest.raises(KeyboardInterrupt):
            _sigint(signal.SIGINT, None)
        with pytest.raises(SystemExit):
            _sigint(signal.SIGINT, None)


class TestRunPlanMode:
    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_plan_mode(tmp_path / "nonexistent.md")

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.md"
        f.write_text("")
        with pytest.raises(SystemExit):
            run_plan_mode(f)

    @patch("cadence.cli.Service", side_effect=RuntimeError("not a repo"))
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_not_git_repo(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _svc: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()
        f = tmp_path / "plan.md"
        f.write_text("implement feature X")
        with pytest.raises(SystemExit):
            run_plan_mode(f)

    @patch("cadence.cli.typer.echo")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_full_wiring_plan_ready(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_echo: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor

        mock_terminal = MagicMock()
        mock_terminal_cls.return_value = mock_terminal

        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")

        run_plan_mode(f)

        mock_executor.run.assert_called_once()
        mock_log.close.assert_called_once()
        mock_log.print.assert_any_call("plan is ready")
        mock_echo.assert_any_call(f"run: cadence --task {tmp_path / 'plan.md'}")

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_executor_created_with_handlers(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor

        mock_terminal = MagicMock()
        mock_terminal_cls.return_value = mock_terminal

        f = tmp_path / "plan.md"
        f.write_text("implement feature X")

        run_plan_mode(f)

        call_kwargs = mock_executor_cls.call_args
        assert call_kwargs.kwargs.get("activity_handler") is not None
        assert call_kwargs.kwargs.get("output_handler") is not None

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_user_aborted(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.side_effect = UserAbortedError("aborted")
        mock_executor_cls.return_value = mock_executor

        mock_terminal = MagicMock()
        mock_terminal_cls.return_value = mock_terminal

        f = tmp_path / "plan.md"
        f.write_text("implement feature X")

        run_plan_mode(f)

        mock_log.print.assert_any_call("aborted by user")
        mock_log.close.assert_called_once()


class TestMainCommand:
    def test_version_flag(self) -> None:
        from typer.testing import CliRunner

        from cadence import __version__
        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--version"])
        assert "cadence" in result.output
        assert __version__ in result.output

    def test_help_lists_chain_flag(self) -> None:
        import re

        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--chain" in plain
        assert "sequence of tasks" in plain

    def test_mutual_exclusivity(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f1 = tmp_path / "a.md"
        f1.write_text("plan")
        f2 = tmp_path / "b.md"
        f2.write_text("task")
        result = runner.invoke(app, ["--plan", str(f1), "--task", str(f2)])
        assert result.exit_code != 0

    @patch("cadence.cli.run_task_mode")
    def test_task_flag_calls_run_task_mode(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f)])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[0] == f

    @patch("cadence.cli.run_review_mode")
    def test_review_flag_calls_run_review_mode(self, mock_run: MagicMock) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--review"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(None, config=None)

    def test_review_with_impl_errors(self) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--review", "--impl"])
        assert result.exit_code != 0
        assert "--review is incompatible with --impl" in result.output

    def test_review_with_task_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f), "--review"])
        assert result.exit_code != 0

    def test_no_args_shows_error(self) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, [])
        assert result.exit_code != 0

    def test_impl_without_plan_errors(self) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--impl"])
        assert result.exit_code != 0
        assert "--impl requires --plan" in result.output

    def test_impl_with_task_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f), "--impl"])
        assert result.exit_code != 0

    @patch("cadence.cli.run_plan_mode")
    def test_impl_with_plan_passes(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")
        result = runner.invoke(app, ["--plan", str(f), "--impl"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["impl"] is True

    @patch("cadence.cli.run_review_mode")
    def test_base_with_review_passes(self, mock_run: MagicMock) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--review", "--base", "develop"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with("develop", config=None)

    def test_base_without_review_errors(self) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--base", "develop"])
        assert result.exit_code != 0
        assert "--base is only valid with --review" in result.output

    def test_base_with_plan_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")
        result = runner.invoke(app, ["--plan", str(f), "--base", "develop"])
        assert result.exit_code != 0
        assert "--base is only valid with --review" in result.output

    def test_base_with_task_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f), "--base", "develop"])
        assert result.exit_code != 0
        assert "--base is only valid with --review" in result.output

    @patch("cadence.cli.run_squash_mode")
    def test_squash_alone_calls_run_squash_mode(self, mock_squash: MagicMock) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--squash"])
        assert result.exit_code == 0
        mock_squash.assert_called_once_with(config=None)

    @patch("cadence.cli.run_task_mode")
    def test_squash_with_task_propagates(self, mock_task: MagicMock, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f), "--squash"])
        assert result.exit_code == 0
        mock_task.assert_called_once()
        _, kwargs = mock_task.call_args
        assert kwargs["squash"] is True

    @patch("cadence.cli.run_plan_mode")
    def test_squash_with_plan_impl_propagates(self, mock_plan: MagicMock, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "prompt.md"
        f.write_text("implement X")
        result = runner.invoke(app, ["--plan", str(f), "--impl", "--squash"])
        assert result.exit_code == 0
        mock_plan.assert_called_once()
        _, kwargs = mock_plan.call_args
        assert kwargs["squash"] is True
        assert kwargs["impl"] is True

    @patch("cadence.cli.run_run_mode")
    def test_squash_with_run_impl_propagates(self, mock_run: MagicMock) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--run", "--impl", "--squash"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(impl=True, squash=True, config=None)

    def test_squash_with_run_no_impl_errors(self) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--run", "--squash"])
        assert result.exit_code != 0
        assert "--squash with --plan/--run requires --impl" in result.output

    def test_squash_with_plan_no_impl_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "prompt.md"
        f.write_text("implement X")
        result = runner.invoke(app, ["--plan", str(f), "--squash"])
        assert result.exit_code != 0
        assert "--squash with --plan/--run requires --impl" in result.output

    def test_squash_with_review_errors(self) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--review", "--squash"])
        assert result.exit_code != 0
        assert "--squash is incompatible with --review" in result.output

    def test_squash_with_task_init_errors(self) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--task-init", "feat-x", "--squash"])
        assert result.exit_code != 0
        assert "--squash is incompatible with --task-init" in result.output

    @patch("cadence.cli.run_chain_mode")
    def test_chain_flag_calls_run_chain_mode(self, mock_chain: MagicMock, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "chain.txt"
        f.write_text("alpha\n")
        result = runner.invoke(app, ["--chain", str(f)])
        assert result.exit_code == 0
        mock_chain.assert_called_once_with(f, config=None)

    @patch("cadence.cli.run_chain_mode")
    def test_chain_with_config_passes(self, mock_chain: MagicMock, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        f = tmp_path / "chain.txt"
        f.write_text("alpha\n")
        cfg = tmp_path / "override.yaml"
        cfg.write_text("default_branch: main\n")
        result = runner.invoke(app, ["--chain", str(f), "--config", str(cfg)])
        assert result.exit_code == 0
        mock_chain.assert_called_once_with(f, config=cfg)

    def test_chain_with_plan_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("alpha\n")
        plan_file = tmp_path / "p.md"
        plan_file.write_text("plan")
        result = runner.invoke(app, ["--chain", str(chain_file), "--plan", str(plan_file)])
        assert result.exit_code != 0
        assert "--chain is mutually exclusive" in result.output

    def test_chain_with_task_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("alpha\n")
        task_file = tmp_path / "t.md"
        task_file.write_text("task")
        result = runner.invoke(app, ["--chain", str(chain_file), "--task", str(task_file)])
        assert result.exit_code != 0
        assert "--chain is mutually exclusive" in result.output

    def test_chain_with_review_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("alpha\n")
        result = runner.invoke(app, ["--chain", str(chain_file), "--review"])
        assert result.exit_code != 0
        assert "--chain is mutually exclusive" in result.output

    def test_chain_with_run_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("alpha\n")
        result = runner.invoke(app, ["--chain", str(chain_file), "--run"])
        assert result.exit_code != 0
        assert "--chain is mutually exclusive" in result.output

    def test_chain_with_task_init_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("alpha\n")
        result = runner.invoke(app, ["--chain", str(chain_file), "--task-init", "feat-x"])
        assert result.exit_code != 0
        assert "--chain is mutually exclusive" in result.output

    def test_chain_with_impl_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("alpha\n")
        result = runner.invoke(app, ["--chain", str(chain_file), "--impl"])
        assert result.exit_code != 0
        assert "--chain is mutually exclusive" in result.output

    def test_chain_with_squash_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("alpha\n")
        result = runner.invoke(app, ["--chain", str(chain_file), "--squash"])
        assert result.exit_code != 0
        assert "--chain is mutually exclusive" in result.output

    def test_chain_with_base_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("alpha\n")
        result = runner.invoke(app, ["--chain", str(chain_file), "--base", "develop"])
        assert result.exit_code != 0
        assert "--chain is mutually exclusive" in result.output


class TestDerivePlanPath:
    def test_replaces_prompt_with_plan(self, tmp_path: Path) -> None:
        prompt = tmp_path / "my-feature-prompt.md"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "my-feature-plan.md")

    def test_no_prompt_in_name_appends_plan(self, tmp_path: Path) -> None:
        prompt = tmp_path / "feature.md"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "feature-plan.md")

    def test_preserves_directory(self, tmp_path: Path) -> None:
        prompt = tmp_path / "tasks" / "v01" / "prompt.md"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "tasks" / "v01" / "plan.md")

    def test_replaces_only_last_prompt(self, tmp_path: Path) -> None:
        prompt = tmp_path / "fix-prompt-validation-prompt.md"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "fix-prompt-validation-plan.md")

    def test_init_bare_maps_to_plan(self, tmp_path: Path) -> None:
        prompt = tmp_path / "init"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "plan")

    def test_init_with_extension_maps_to_plan(self, tmp_path: Path) -> None:
        prompt = tmp_path / "init.md"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "plan.md")

    def test_init_substring_replaced(self, tmp_path: Path) -> None:
        prompt = tmp_path / "fix-init.md"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "fix-plan.md")

    def test_custom_init_prompt_name_maps_to_plan(self, tmp_path: Path) -> None:
        prompt = tmp_path / "preprompt.md"
        result = derive_plan_path(prompt, init_prompt_name="preprompt")
        assert result == str(tmp_path / "plan.md")


class TestRunPlanModeImplFlag:
    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.typer.echo")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_impl_flag_chains_to_task_mode(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_echo: MagicMock,
        mock_run_task_mode: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor

        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")

        parent = MagicMock()
        parent.attach_mock(mock_log.close, "close")
        parent.attach_mock(mock_run_task_mode, "task")

        run_plan_mode(f, impl=True)

        echo_calls = [str(c) for c in mock_echo.call_args_list]
        assert any("cadence --task" in c for c in echo_calls)
        assert not any("not available in v0.1" in c for c in echo_calls)
        mock_run_task_mode.assert_called_once_with(
            Path(derive_plan_path(f)), squash=False, config=None
        )
        ordered_names = [c[0] for c in parent.mock_calls]
        assert ordered_names.index("close") < ordered_names.index("task")

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.typer.echo")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_no_impl_flag_no_not_available_message(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_echo: MagicMock,
        mock_run_task_mode: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor

        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")

        run_plan_mode(f, impl=False)

        echo_calls = [str(c) for c in mock_echo.call_args_list]
        assert any("cadence --task" in c for c in echo_calls)
        assert not any("not available in v0.1" in c for c in echo_calls)
        mock_run_task_mode.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.typer.echo")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Runner")
    @patch("cadence.cli.Service")
    def test_impl_flag_does_not_chain_on_plan_failure(
        self,
        _svc: MagicMock,
        mock_runner_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_echo: MagicMock,
        mock_run_task_mode: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_runner = MagicMock()
        mock_runner.run.return_value = False
        mock_runner_cls.return_value = mock_runner

        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")

        run_plan_mode(f, impl=True)

        mock_run_task_mode.assert_not_called()
        echo_calls = [str(c) for c in mock_echo.call_args_list]
        assert not any("cadence --task" in c for c in echo_calls)


class TestDisplayStats:
    def test_formats_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = DiffStats(files=3, additions=42, deletions=7)
        display_stats(stats, "2m30s", "my-branch")
        captured = capsys.readouterr()
        assert "my-branch" in captured.out
        assert "2m30s" in captured.out
        assert "files: 3" in captured.out
        assert "+42" in captured.out
        assert "-7" in captured.out

    def test_zero_stats(self, capsys: pytest.CaptureFixture[str]) -> None:
        display_stats(DiffStats(), "0m00s", "feature")
        captured = capsys.readouterr()
        assert "feature" in captured.out
        assert "files: 0" in captured.out


class TestRunTaskMode:
    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_task_mode(tmp_path / "nonexistent.md")

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_happy_path_success(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m00s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats(files=2, additions=10, deletions=3)
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalCompleted)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1: done\n\n- [x] done item\n")

        run_task_mode(f)

        mock_svc.set_commit_trailer.assert_called()
        mock_svc.ensure_has_commits.assert_called_once()
        mock_svc.create_branch_for_plan.assert_called_once()
        mock_svc.diff_stats.assert_called_once_with("main")
        mock_svc.mark_plan_completed.assert_called_once()
        mock_log.close.assert_called_once()

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_user_aborted(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.side_effect = UserAbortedError("aborted")
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1\n\n- [ ] do it\n")

        run_task_mode(f)

        mock_log.print.assert_any_call("aborted by user")
        mock_log.close.assert_called_once()
        mock_svc.diff_stats.assert_not_called()
        mock_svc.mark_plan_completed.assert_not_called()

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_service_init_failure(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)
        mock_service_cls.side_effect = RuntimeError("not a repo")

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1\n\n- [ ] do it\n")

        with pytest.raises(SystemExit):
            run_task_mode(f)

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_move_plan_failure_warns_but_succeeds(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m00s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_svc.mark_plan_completed.side_effect = RuntimeError("git failed")
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalCompleted)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1: done\n\n- [x] done item\n")

        run_task_mode(f)

        mock_log.warn.assert_any_call(
            "could not mark plan completed: %s",
            mock_svc.mark_plan_completed.side_effect,
        )
        mock_log.close.assert_called_once()


class TestInstallSigquit:
    def test_install_sigquit_sets_event_when_signal_fires(self) -> None:
        from cadence.cli import _install_sigquit

        event = threading.Event()
        sigquit = getattr(signal, "SIGQUIT", None)
        if sigquit is None:
            pytest.skip("SIGQUIT not available on this platform")

        prior = signal.getsignal(sigquit)
        try:
            _install_sigquit(event)
            handler = signal.getsignal(sigquit)
            assert callable(handler)
            handler(sigquit, None)
            assert event.is_set()
        finally:
            signal.signal(sigquit, prior)


class TestSetupRuntime:
    @patch("cadence.cli.Service")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_returns_expected_tuple(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        _check: MagicMock,
        mock_service_cls: MagicMock,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(default_branch="trunk")
        mock_svc = MagicMock()
        mock_service_cls.return_value = mock_svc

        cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(
            None, None
        )

        assert isinstance(cfg, Config)
        assert holder is not None
        assert colors is not None
        assert git_svc is mock_svc
        assert callable(factory)
        assert default_branch == "trunk"
        assert local_dir is None

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_factory_creates_executor_with_supplied_model(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        _check: MagicMock,
        _svc: MagicMock,
        mock_executor_cls: MagicMock,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()

        _, _, _, _, factory, _, _ = _setup_runtime(None, None)

        log = MagicMock()
        factory(log, "opus")

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "opus"
        assert kwargs["activity_handler"] is not None
        assert kwargs["output_handler"] is not None

    @patch("cadence.cli.Service", side_effect=RuntimeError("not a repo"))
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_service_init_failure_exits(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        _check: MagicMock,
        _svc: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()

        with pytest.raises(SystemExit) as excinfo:
            _setup_runtime(None, None)
        assert excinfo.value.code == 1
        assert "not a repo" in capsys.readouterr().err


class TestFindExistingPlan:
    def test_only_plan_exists_returns_plan_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / "cdc-tasks" / "feat-x"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan").write_text("body")

        result = find_existing_plan("cdc-tasks", "feat-x", "main")

        assert result == "cdc-tasks/feat-x/plan"

    def test_only_completed_returns_completed_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / "cdc-tasks" / "feat-x"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan-completed").write_text("body")

        result = find_existing_plan("cdc-tasks", "feat-x", "main")

        assert result == "cdc-tasks/feat-x/plan-completed"

    def test_both_exist_returns_plan_not_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / "cdc-tasks" / "feat-x"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan").write_text("active")
        (plan_dir / "plan-completed").write_text("old")

        result = find_existing_plan("cdc-tasks", "feat-x", "main")

        assert result == "cdc-tasks/feat-x/plan"

    def test_neither_exists_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        result = find_existing_plan("cdc-tasks", "feat-x", "main")

        assert result == ""

    def test_branch_equals_default_returns_empty_even_with_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / "cdc-tasks" / "main"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan").write_text("body")

        result = find_existing_plan("cdc-tasks", "main", "main")

        assert result == ""

    def test_origin_prefix_default_branch_trimmed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / "cdc-tasks" / "main"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan").write_text("body")

        result = find_existing_plan("cdc-tasks", "main", "origin/main")

        assert result == ""

    def test_empty_branch_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        result = find_existing_plan("cdc-tasks", "", "main")

        assert result == ""

    def test_branch_with_slash_uses_sanitized_segment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / "cdc-tasks" / "feat-foo"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan").write_text("body")

        result = find_existing_plan("cdc-tasks", "feat/foo", "main")

        assert result == "cdc-tasks/feat-foo/plan"


class TestRunReviewMode:
    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_happy_path_success(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats(files=1, additions=4, deletions=2)
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalReviewDone)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        with (
            patch("cadence.cli.display_stats") as mock_display,
            patch("cadence.cli.Runner") as mock_runner_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

        mock_svc.set_commit_trailer.assert_called()
        mock_svc.diff_stats.assert_called_once_with("main")
        mock_svc.mark_plan_completed.assert_not_called()
        mock_svc.create_branch_for_plan.assert_not_called()
        mock_svc.ensure_has_commits.assert_not_called()
        mock_display.assert_called_once()
        mock_log.close.assert_called_once()

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_review_mode_uses_single_executor_with_review_model(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(
            iteration_delay_ms=0,
            task_model="sonnet",
            review_model="opus",
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        primary = MagicMock(name="primary")
        mock_executor_cls.return_value = primary

        mock_terminal_cls.return_value = MagicMock()

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

            assert mock_executor_cls.call_count == 1
            kwargs = mock_executor_cls.call_args.kwargs
            assert kwargs["model"] == "opus"

            deps = mock_runner_cls.call_args.args[2]
            assert deps.executor is primary
            assert deps.review_executor is None

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_review_model_equal_to_task_uses_single_executor(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(
            iteration_delay_ms=0,
            task_model="sonnet",
            review_model="sonnet",
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        primary = MagicMock(name="primary")
        mock_executor_cls.return_value = primary

        mock_terminal_cls.return_value = MagicMock()

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

            assert mock_executor_cls.call_count == 1
            kwargs = mock_executor_cls.call_args.kwargs
            assert kwargs["model"] == "sonnet"

            deps = mock_runner_cls.call_args.args[2]
            assert deps.executor is primary
            assert deps.review_executor is None

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_base_arg_overrides_config_and_autodetect(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, default_branch="main")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with (
            patch("cadence.cli.display_stats"),
            patch("cadence.cli.Runner") as mock_runner_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode(base="develop")

        mock_svc.diff_stats.assert_called_once_with("develop")
        mock_log.print.assert_any_call("base: %s", "develop")
        ctx_arg = mock_runner_cls.call_args.args[0]
        assert ctx_arg.default_branch == "develop"

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_base_none_uses_config_default_branch(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, default_branch="trunk")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with (
            patch("cadence.cli.display_stats"),
            patch("cadence.cli.Runner") as mock_runner_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode(base=None)

        mock_svc.diff_stats.assert_called_once_with("trunk")
        mock_log.print.assert_any_call("base: %s", "trunk")
        ctx_arg = mock_runner_cls.call_args.args[0]
        assert ctx_arg.default_branch == "trunk"

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_runner_returns_false_skips_diff_stats(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = False
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

        mock_svc.diff_stats.assert_not_called()
        mock_log.close.assert_called_once()

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_user_aborted(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.side_effect = UserAbortedError("aborted")
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

        mock_log.print.assert_any_call("aborted by user")
        mock_log.close.assert_called_once()
        mock_svc.diff_stats.assert_not_called()

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_review_mode_discovers_existing_plan(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_svc.head_hash.return_value = "abc123"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        plan_dir = tmp_path / "cdc-tasks" / "feat-x"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan").write_text("plan body")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with (
                patch("cadence.cli.display_stats"),
                patch("cadence.cli.Runner") as mock_runner_cls,
            ):
                mock_runner = MagicMock()
                mock_runner.run.return_value = True
                mock_runner_cls.return_value = mock_runner

                run_review_mode()

                ctx_arg = mock_runner_cls.call_args.args[0]
                assert ctx_arg.plan_file == "cdc-tasks/feat-x/plan"
        finally:
            os.chdir(original_cwd)

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_review_mode_no_plan_results_in_empty_plan_file(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_svc.head_hash.return_value = "abc123"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with (
                patch("cadence.cli.display_stats"),
                patch("cadence.cli.Runner") as mock_runner_cls,
            ):
                mock_runner = MagicMock()
                mock_runner.run.return_value = True
                mock_runner_cls.return_value = mock_runner

                run_review_mode()

                ctx_arg = mock_runner_cls.call_args.args[0]
                assert ctx_arg.plan_file == ""
        finally:
            os.chdir(original_cwd)

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_review_mode_falls_back_to_plan_completed(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_svc.head_hash.return_value = "abc123"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        plan_dir = tmp_path / "cdc-tasks" / "feat-x"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan-completed").write_text("done")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with (
                patch("cadence.cli.display_stats"),
                patch("cadence.cli.Runner") as mock_runner_cls,
            ):
                mock_runner = MagicMock()
                mock_runner.run.return_value = True
                mock_runner_cls.return_value = mock_runner

                run_review_mode()

                ctx_arg = mock_runner_cls.call_args.args[0]
                assert ctx_arg.plan_file == "cdc-tasks/feat-x/plan-completed"
        finally:
            os.chdir(original_cwd)


class TestRunTaskModeReviewExecutor:
    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_distinct_review_model_passes_review_executor(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(
            iteration_delay_ms=0,
            task_model="sonnet",
            review_model="opus",
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        primary = MagicMock(name="primary")
        secondary = MagicMock(name="secondary")
        mock_executor_cls.side_effect = [primary, secondary]

        mock_terminal_cls.return_value = MagicMock()

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_task_mode(f)

            assert mock_executor_cls.call_count == 2
            deps = mock_runner_cls.call_args.args[2]
            assert deps.executor is primary
            assert deps.review_executor is secondary

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_matching_review_model_leaves_review_executor_none(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(
            iteration_delay_ms=0,
            task_model="sonnet",
            review_model="sonnet",
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        primary = MagicMock(name="primary")
        mock_executor_cls.return_value = primary

        mock_terminal_cls.return_value = MagicMock()

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_task_mode(f)

            assert mock_executor_cls.call_count == 1
            deps = mock_runner_cls.call_args.args[2]
            assert deps.executor is primary
            assert deps.review_executor is None


class TestConfigFlag:
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_plan_explicit_config_applies_overrides(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")

        yaml_dir = tmp_path / "elsewhere"
        yaml_dir.mkdir()
        yaml_path = yaml_dir / "explicit.yaml"
        yaml_path.write_text("plan:\n  model: yaml-plan\n")

        run_plan_mode(plan_file, config=yaml_path)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "yaml-plan"

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_plan_autodiscovers_yaml_next_to_plan(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("plan:\n  model: discovered-plan\n")

        run_plan_mode(plan_file)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "discovered-plan"

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_plan_no_yaml_no_overrides(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, plan_model="toml-plan-default")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")

        run_plan_mode(plan_file)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "toml-plan-default"

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_task_explicit_config_applies_overrides(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        task_file = tmp_path / "plan.md"
        task_file.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")

        yaml_dir = tmp_path / "elsewhere"
        yaml_dir.mkdir()
        yaml_path = yaml_dir / "explicit.yaml"
        yaml_path.write_text("task:\n  model: yaml-task\n")

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_task_mode(task_file, config=yaml_path)

        primary_kwargs = mock_executor_cls.call_args_list[0].kwargs
        assert primary_kwargs["model"] == "yaml-task"

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_task_autodiscovers_yaml_next_to_task(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        task_file = tmp_path / "plan.md"
        task_file.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("task:\n  model: discovered-task\n")

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_task_mode(task_file)

        primary_kwargs = mock_executor_cls.call_args_list[0].kwargs
        assert primary_kwargs["model"] == "discovered-task"

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_review_explicit_config_applies_overrides(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        yaml_path = tmp_path / "explicit.yaml"
        yaml_path.write_text("review:\n  model: yaml-review\n")

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode(config=yaml_path)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "yaml-review"

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.find_yaml_config")
    def test_review_skips_autodiscovery(
        self,
        mock_find: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, review_model="toml-review-default")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

        mock_find.assert_not_called()
        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "toml-review-default"

    def test_explicit_missing_config_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")
        missing = tmp_path / "missing.yaml"

        result = runner.invoke(app, ["--plan", str(plan_file), "--config", str(missing)])
        assert result.exit_code != 0
        assert "config file not found" in result.output

    def test_invalid_yaml_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from cadence.cli import app

        runner = CliRunner()
        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("not-valid-yaml-no-colon\n")

        result = runner.invoke(app, ["--plan", str(plan_file), "--config", str(bad_yaml)])
        assert result.exit_code != 0
        assert "error" in result.output.lower()

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_explicit_config_wins_over_autodiscovery(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")

        sibling_yaml = tmp_path / "config.yaml"
        sibling_yaml.write_text("plan:\n  model: sibling-plan\n")

        explicit_yaml = tmp_path / "explicit.yaml"
        explicit_yaml.write_text("plan:\n  model: explicit-plan\n")

        run_plan_mode(plan_file, config=explicit_yaml)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "explicit-plan"

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_autodiscovery_does_not_walk_to_parent(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, plan_model="toml-plan-default")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        parent_yaml = tmp_path / "config.yaml"
        parent_yaml.write_text("plan:\n  model: parent-yaml\n")

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        plan_file = subdir / "prompt.md"
        plan_file.write_text("implement feature X")

        run_plan_mode(plan_file)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "toml-plan-default"

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_impl_propagates_config_to_task_mode(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")
        derived_plan = tmp_path / "plan.md"
        derived_plan.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")

        explicit_yaml = tmp_path / "explicit.yaml"
        explicit_yaml.write_text("plan:\n  model: yaml-plan\ntask:\n  model: yaml-task\n")

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_plan_mode(plan_file, impl=True, config=explicit_yaml)

        models_used = [call.kwargs["model"] for call in mock_executor_cls.call_args_list]
        assert "yaml-plan" in models_used
        assert "yaml-task" in models_used


class TestProgressPathWiring:
    """Verify CLI plumbs tasks_root, default_branch, and head_hash through to the Logger."""

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_review_mode_on_default_branch_uses_head_hash_segment(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.head_hash.return_value = "abc123def456"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with (
                patch("cadence.cli.display_stats"),
                patch("cadence.cli.Runner") as mock_runner_cls,
            ):
                mock_runner = MagicMock()
                mock_runner.run.return_value = True
                mock_runner_cls.return_value = mock_runner

                run_review_mode()
        finally:
            os.chdir(original_cwd)

        expected = tmp_path / "cdc-tasks" / "abc123def456" / "progress-review.txt"
        assert expected.is_file()

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_review_mode_on_feature_branch_uses_branch_segment(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat/foo"
        mock_svc.head_hash.return_value = "abc123"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with (
                patch("cadence.cli.display_stats"),
                patch("cadence.cli.Runner") as mock_runner_cls,
            ):
                mock_runner = MagicMock()
                mock_runner.run.return_value = True
                mock_runner_cls.return_value = mock_runner

                run_review_mode()
        finally:
            os.chdir(original_cwd)

        expected = tmp_path / "cdc-tasks" / "feat-foo" / "progress-review.txt"
        assert expected.is_file()

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Service")
    def test_plan_mode_writes_progress_plan_next_to_plan_file(
        self,
        _svc: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        plan_dir = tmp_path / "tasks" / "0001-foo"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "preprompt.md"
        plan_file.write_text("implement feature X")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_plan_mode(plan_file)
        finally:
            os.chdir(original_cwd)

        expected = plan_dir / "progress-plan.txt"
        assert expected.is_file()

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_task_mode_writes_progress_task_next_to_plan_file(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalCompleted)
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        plan_dir = tmp_path / "tasks" / "0002-bar"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "plan.md"
        plan_file.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with patch("cadence.cli.Runner") as mock_runner_cls:
                mock_runner = MagicMock()
                mock_runner.run.return_value = True
                mock_runner_cls.return_value = mock_runner

                run_task_mode(plan_file)
        finally:
            os.chdir(original_cwd)

        expected = plan_dir / "progress-task.txt"
        assert expected.is_file()

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_review_mode_no_branch_no_hash_exits_with_error(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = ""
        mock_svc.head_hash.return_value = ""
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_review_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "no branch and no head hash" in captured.err


class TestRunTaskInitMode:
    @pytest.mark.parametrize("name", ["", "/foo", "foo/bar", "back\\slash", "-foo", ".hidden"])
    def test_invalid_name_exits(self, name: str, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            run_task_init_mode(name)
        assert excinfo.value.code == 1
        assert "invalid task name" in capsys.readouterr().err

    @patch("cadence.cli.Service", side_effect=RuntimeError("not a repo"))
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_not_a_git_repo(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        _svc: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()

        with pytest.raises(SystemExit) as excinfo:
            run_task_init_mode("feat-x")
        assert excinfo.value.code == 1
        assert "not a repo" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_ensure_has_commits_failure(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()

        mock_svc = MagicMock()
        mock_svc.ensure_has_commits.side_effect = RuntimeError("aborted by user")
        mock_service_cls.return_value = mock_svc

        with pytest.raises(SystemExit) as excinfo:
            run_task_init_mode("feat-x")
        assert excinfo.value.code == 1
        assert "aborted by user" in capsys.readouterr().err
        mock_svc.create_branch.assert_not_called()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_detached_head_exits(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = ""
        mock_service_cls.return_value = mock_svc

        with pytest.raises(SystemExit) as excinfo:
            run_task_init_mode("feat-x")
        assert excinfo.value.code == 1
        assert "detached HEAD" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_directory_already_exists(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_service_cls.return_value = mock_svc

        (tmp_path / "cdc-tasks" / "feat-x").mkdir(parents=True)

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_task_init_mode("feat-x")
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "task directory already exists" in capsys.readouterr().err
        mock_svc.create_branch.assert_not_called()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_branch_already_exists(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = True
        mock_service_cls.return_value = mock_svc

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_task_init_mode("feat-x")
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "branch already exists" in capsys.readouterr().err
        mock_svc.create_branch.assert_not_called()
        assert not (tmp_path / "cdc-tasks" / "feat-x").exists()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_create_branch_failure_leaves_no_dir(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_svc.create_branch.side_effect = RuntimeError("git checkout -b failed")
        mock_service_cls.return_value = mock_svc

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_task_init_mode("feat-x")
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "git checkout -b failed" in capsys.readouterr().err
        assert not (tmp_path / "cdc-tasks" / "feat-x").exists()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_happy_path_on_default_branch_no_config(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_task_init_mode("feat-x")
        finally:
            os.chdir(original_cwd)

        mock_svc.create_branch.assert_called_once_with("feat-x")
        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        assert task_dir.is_dir()
        assert (task_dir / "init").is_file()
        assert (task_dir / "init").read_text() == ""
        assert not (task_dir / "config.yaml").exists()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_happy_path_on_non_default_branch_writes_config(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        import yaml

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature-parent"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_task_init_mode("child-task")
        finally:
            os.chdir(original_cwd)

        mock_svc.create_branch.assert_called_once_with("child-task")
        task_dir = tmp_path / "cdc-tasks" / "child-task"
        assert (task_dir / "init").is_file()
        config_path = task_dir / "config.yaml"
        assert config_path.is_file()
        loaded = yaml.safe_load(config_path.read_text())
        assert loaded == {"default_branch": "feature-parent"}

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_origin_prefix_on_default_branch_treated_as_default(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="origin/main")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_task_init_mode("scaffold")
        finally:
            os.chdir(original_cwd)

        assert not (tmp_path / "cdc-tasks" / "scaffold" / "config.yaml").exists()


class TestRunRunMode:
    @patch("cadence.cli.Service", side_effect=RuntimeError("not a repo"))
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_not_a_git_repo(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        _svc: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()

        with pytest.raises(SystemExit) as excinfo:
            run_run_mode()
        assert excinfo.value.code == 1
        assert "not a repo" in capsys.readouterr().err

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_detached_head_exits(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = ""
        mock_service_cls.return_value = mock_svc

        with pytest.raises(SystemExit) as excinfo:
            run_run_mode()
        assert excinfo.value.code == 1
        assert "detached HEAD" in capsys.readouterr().err
        mock_run_plan.assert_not_called()
        mock_run_task.assert_not_called()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_task_directory_missing(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_run_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "task directory not found" in capsys.readouterr().err

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_plan_completed_message_no_impl(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=False)
        finally:
            os.chdir(original_cwd)

        assert "plan already completed" in capsys.readouterr().out
        mock_run_plan.assert_not_called()
        mock_run_task.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_plan_completed_message_with_impl(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=True)
        finally:
            os.chdir(original_cwd)

        assert "plan already completed" in capsys.readouterr().out
        mock_run_plan.assert_not_called()
        mock_run_task.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_plan_exists_no_impl_prints_hint(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan").write_text("# plan", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=False)
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "plan ready" in out
        assert "cadence --task" in out
        mock_run_task.assert_not_called()
        mock_run_plan.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_plan_exists_with_impl_calls_run_task_mode(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        plan_path = task_dir / "plan"
        plan_path.write_text("# plan", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=True)
        finally:
            os.chdir(original_cwd)

        mock_run_task.assert_called_once()
        args, kwargs = mock_run_task.call_args
        assert args[0] == Path("cdc-tasks/feat-x/plan")
        assert kwargs == {"squash": False, "config": None}
        mock_run_plan.assert_not_called()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_init_missing_exits(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        (tmp_path / "cdc-tasks" / "feat-x").mkdir(parents=True)

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_run_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "init file not found" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_init_empty_exits(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "init").write_text("   \n\t\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_run_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "init file is empty" in capsys.readouterr().err

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_init_non_empty_no_impl_calls_run_plan_mode(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "init").write_text("do the thing", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=False)
        finally:
            os.chdir(original_cwd)

        mock_run_plan.assert_called_once()
        args, kwargs = mock_run_plan.call_args
        assert args[0] == Path("cdc-tasks/feat-x/init")
        assert kwargs == {"impl": False, "squash": False, "config": None}
        mock_run_task.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_init_non_empty_with_impl_calls_run_plan_mode_with_impl_true(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "init").write_text("do the thing", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=True)
        finally:
            os.chdir(original_cwd)

        mock_run_plan.assert_called_once()
        _args, kwargs = mock_run_plan.call_args
        assert kwargs == {"impl": True, "squash": False, "config": None}
        mock_run_task.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_uses_init_prompt_name_override(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", init_prompt_name="prompt")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "prompt").write_text("do it", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=False)
        finally:
            os.chdir(original_cwd)

        mock_run_plan.assert_called_once()
        args, _kwargs = mock_run_plan.call_args
        assert args[0] == Path("cdc-tasks/feat-x/prompt")
        mock_run_task.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_plan_takes_precedence_over_init(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "init").write_text("do it", encoding="utf-8")
        (task_dir / "plan").write_text("# plan", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=False)
        finally:
            os.chdir(original_cwd)

        assert "plan ready" in capsys.readouterr().out
        mock_run_plan.assert_not_called()
        mock_run_task.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_plan_completed_takes_precedence_over_plan_and_init(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "init").write_text("do it", encoding="utf-8")
        (task_dir / "plan").write_text("# plan", encoding="utf-8")
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=True)
        finally:
            os.chdir(original_cwd)

        assert "plan already completed" in capsys.readouterr().out
        mock_run_plan.assert_not_called()
        mock_run_task.assert_not_called()


class TestRunSquashMode:
    @staticmethod
    def _setup_mock_service(
        *,
        branch: str = "feat-x",
        is_default: bool = False,
        is_dirty: bool = False,
        commits_ahead: int = 3,
    ) -> MagicMock:
        svc = MagicMock()
        svc.current_branch.return_value = branch
        svc.is_default_branch.return_value = is_default
        svc.is_dirty.return_value = is_dirty
        svc.commits_ahead.return_value = commits_ahead
        svc.diff_stats.return_value = DiffStats(files=2, additions=5, deletions=1)
        svc.head_hash.return_value = "deadbeefcafe"
        return svc

    @patch("cadence.cli.Service", side_effect=RuntimeError("not a repo"))
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_not_a_git_repo(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _svc: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()

        with pytest.raises(SystemExit) as excinfo:
            run_squash_mode()
        assert excinfo.value.code == 1
        assert "not a repo" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_detached_head_exits(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()
        mock_service_cls.return_value = self._setup_mock_service(branch="")

        with pytest.raises(SystemExit) as excinfo:
            run_squash_mode()
        assert excinfo.value.code == 1
        assert "detached HEAD" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_default_branch_exits(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service(branch="main", is_default=True)

        with pytest.raises(SystemExit) as excinfo:
            run_squash_mode()
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "default branch main" in err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_task_directory_missing(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_service_cls.return_value = self._setup_mock_service()

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "task directory not found" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_plan_not_completed(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_service_cls.return_value = self._setup_mock_service()

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan").write_text("# plan", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "plan not completed" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_uncommitted_changes_exits(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_service_cls.return_value = self._setup_mock_service(is_dirty=True)

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "uncommitted changes" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_zero_commits_ahead_exits(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service(commits_ahead=0)

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert "no commits ahead" in capsys.readouterr().err

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_one_commit_ahead_is_noop(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_svc = self._setup_mock_service(commits_ahead=1)
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_squash_mode()
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "single commit already" in out
        mock_executor_cls.assert_not_called()
        mock_svc.squash_commits.assert_not_called()

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_happy_path_squashes_with_claude_message(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        claude_output = (
            "<<<CADENCE:COMMIT_MSG_BEGIN>>>\n"
            "feat-x.\n\nAdded: a thing.\n"
            "<<<CADENCE:COMMIT_MSG_END>>>"
        )
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=claude_output)
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with patch("cadence.cli.display_stats") as mock_display:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        mock_svc.squash_commits.assert_called_once_with("main", "feat-x.\n\nAdded: a thing.")
        mock_svc.diff_stats.assert_called_with("main")
        mock_display.assert_called_once()
        mock_log.close.assert_called_once_with(success=True)

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_claude_returns_no_markers_aborts(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="just some prose, no markers")
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        mock_svc.squash_commits.assert_not_called()
        mock_log.error.assert_called()
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_claude_executor_error_aborts(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(error=Exception("claude crashed"))
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        mock_svc.squash_commits.assert_not_called()
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_squash_runtime_error_aborts(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_svc = self._setup_mock_service()
        mock_svc.squash_commits.side_effect = RuntimeError("merge-base not found")
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        claude_output = "<<<CADENCE:COMMIT_MSG_BEGIN>>>\nmsg body\n<<<CADENCE:COMMIT_MSG_END>>>"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=claude_output)
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_idle_timeout_aborts_before_squash(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        # Even if Claude's partial output happens to contain valid markers,
        # an idle timeout must abort before any history rewrite.
        partial = "<<<CADENCE:COMMIT_MSG_BEGIN>>>\ntruncated message\n<<<CADENCE:COMMIT_MSG_END>>>"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=partial, idle_timed_out=True)
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        mock_svc.squash_commits.assert_not_called()
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_keyboard_interrupt_returns_cleanly(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.side_effect = KeyboardInterrupt
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_squash_mode()
        finally:
            os.chdir(original_cwd)

        mock_svc.squash_commits.assert_not_called()
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_unexpected_exception_logged_and_exits_cleanly(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.side_effect = OSError("disk full")
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        mock_svc.squash_commits.assert_not_called()
        mock_log.error.assert_called()
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_per_task_config_yaml_overrides_default_branch(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        # Global config says "main"; per-task config says "parent-branch".
        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        claude_output = "<<<CADENCE:COMMIT_MSG_BEGIN>>>\nmsg body\n<<<CADENCE:COMMIT_MSG_END>>>"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=claude_output)
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")
        (task_dir / "config.yaml").write_text("default_branch: parent-branch\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with patch("cadence.cli.display_stats"):
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        mock_svc.squash_commits.assert_called_once_with("parent-branch", "msg body")
        mock_svc.commits_ahead.assert_called_with("parent-branch")

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_aborts_when_working_tree_dirty_after_claude(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        # Clean before claude.run, dirty after — simulating Claude staging or
        # modifying tracked files during its run.
        mock_svc.is_dirty.side_effect = [False, True]
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        claude_output = "<<<CADENCE:COMMIT_MSG_BEGIN>>>\nmsg body\n<<<CADENCE:COMMIT_MSG_END>>>"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=claude_output)
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        mock_svc.squash_commits.assert_not_called()
        mock_log.error.assert_called()
        mock_log.close.assert_called_once_with(success=False)


class TestSquashPipelineIntegration:
    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_run_task_mode_squash_chains_after_success(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        mock_run_squash: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m00s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats(files=2, additions=10, deletions=3)
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalCompleted)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1: done\n\n- [x] done item\n")

        parent = MagicMock()
        parent.attach_mock(mock_log.close, "close")
        parent.attach_mock(mock_run_squash, "squash")

        run_task_mode(f, squash=True)

        mock_run_squash.assert_called_once_with(config=None)
        ordered_names = [c[0] for c in parent.mock_calls]
        assert ordered_names.index("close") < ordered_names.index("squash")

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Runner")
    def test_run_task_mode_squash_skipped_on_failure(
        self,
        mock_runner_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        mock_run_squash: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_service_cls.return_value = mock_svc

        mock_runner = MagicMock()
        mock_runner.run.return_value = False
        mock_runner_cls.return_value = mock_runner

        f = tmp_path / "plan.md"
        f.write_text("# plan\n")

        run_task_mode(f, squash=True)

        mock_run_squash.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_run_run_mode_plan_completed_with_impl_squash_calls_squash(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        mock_run_squash: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=True, squash=True)
        finally:
            os.chdir(original_cwd)

        mock_run_squash.assert_called_once_with(config=None)
        mock_run_task.assert_not_called()
        mock_run_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_run_run_mode_plan_exists_with_impl_squash_propagates(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        mock_run_squash: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan").write_text("# plan", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=True, squash=True)
        finally:
            os.chdir(original_cwd)

        mock_run_task.assert_called_once()
        _, kwargs = mock_run_task.call_args
        assert kwargs["squash"] is True
        mock_run_plan.assert_not_called()
        mock_run_squash.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_run_run_mode_init_with_impl_squash_propagates(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        mock_run_squash: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feat-x"
        mock_service_cls.return_value = mock_svc

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "init").write_text("do it", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_run_mode(impl=True, squash=True)
        finally:
            os.chdir(original_cwd)

        mock_run_plan.assert_called_once()
        _, kwargs = mock_run_plan.call_args
        assert kwargs["squash"] is True
        assert kwargs["impl"] is True
        mock_run_task.assert_not_called()
        mock_run_squash.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.typer.echo")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_run_plan_mode_propagates_squash_to_task(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _echo: MagicMock,
        mock_run_task_mode: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor

        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")

        run_plan_mode(f, impl=True, squash=True)

        mock_run_task_mode.assert_called_once()
        _, kwargs = mock_run_task_mode.call_args
        assert kwargs["squash"] is True


class TestParseChainFile:
    def test_missing_file_exits(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        missing = tmp_path / "absent.txt"
        with pytest.raises(SystemExit) as excinfo:
            _parse_chain_file(missing)
        assert excinfo.value.code == 1
        assert f"file not found: {missing}" in capsys.readouterr().err

    def test_empty_file_exits(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        chain = tmp_path / "chain.txt"
        chain.write_text("")
        with pytest.raises(SystemExit) as excinfo:
            _parse_chain_file(chain)
        assert excinfo.value.code == 1
        assert "chain file is empty" in capsys.readouterr().err

    def test_only_blanks_and_comments_exits(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        chain = tmp_path / "chain.txt"
        chain.write_text("\n\n# comment\n   \n# another\n")
        with pytest.raises(SystemExit) as excinfo:
            _parse_chain_file(chain)
        assert excinfo.value.code == 1
        assert "chain file is empty" in capsys.readouterr().err

    def test_happy_path_strips_and_skips(self, tmp_path: Path) -> None:
        chain = tmp_path / "chain.txt"
        chain.write_text("  task-1  \n# skip\n\ntask-2\n   task-3\n# trailing\n")
        names = _parse_chain_file(chain)
        assert names == ["task-1", "task-2", "task-3"]

    @pytest.mark.parametrize("name", ["foo/bar", "back\\slash", ".hidden", "-leading-dash", "/abs"])
    def test_invalid_names_exit(
        self, name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        chain = tmp_path / "chain.txt"
        chain.write_text(f"valid-1\n{name}\n")
        with pytest.raises(SystemExit) as excinfo:
            _parse_chain_file(chain)
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "invalid task name in chain file" in err
        assert name in err


class TestValidateChainTasks:
    def test_all_present_returns_empty(self, tmp_path: Path) -> None:
        for name in ("a", "b"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()
        import os

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            warnings = _validate_chain_tasks("cdc-tasks", ["a", "b"])
        finally:
            os.chdir(original_cwd)
        assert warnings == []

    def test_missing_directory_warning(self, tmp_path: Path) -> None:
        import os

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            warnings = _validate_chain_tasks("cdc-tasks", ["nope"])
        finally:
            os.chdir(original_cwd)
        assert len(warnings) == 1
        assert "task directory not found" in warnings[0]
        assert "nope" in warnings[0]

    def test_missing_init_warning(self, tmp_path: Path) -> None:
        import os

        (tmp_path / "cdc-tasks" / "a").mkdir(parents=True)
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            warnings = _validate_chain_tasks("cdc-tasks", ["a"])
        finally:
            os.chdir(original_cwd)
        assert len(warnings) == 1
        assert "init file not found" in warnings[0]

    def test_multiple_warnings_collected(self, tmp_path: Path) -> None:
        import os

        (tmp_path / "cdc-tasks" / "with-init").mkdir(parents=True)
        (tmp_path / "cdc-tasks" / "with-init" / "init").touch()
        (tmp_path / "cdc-tasks" / "no-init").mkdir(parents=True)
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            warnings = _validate_chain_tasks("cdc-tasks", ["with-init", "no-init", "missing"])
        finally:
            os.chdir(original_cwd)
        assert len(warnings) == 2
        assert any("no-init" in w and "init file not found" in w for w in warnings)
        assert any("missing" in w and "task directory not found" in w for w in warnings)


class TestResolveChainDefaultBranch:
    def test_no_per_task_config_returns_global(self, tmp_path: Path) -> None:
        import os

        (tmp_path / "cdc-tasks" / "task-1").mkdir(parents=True)
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = _resolve_chain_default_branch("cdc-tasks", "task-1", "main")
        finally:
            os.chdir(original_cwd)
        assert result == "main"

    def test_per_task_config_overrides_global(self, tmp_path: Path) -> None:
        import os

        task_dir = tmp_path / "cdc-tasks" / "task-1"
        task_dir.mkdir(parents=True)
        (task_dir / "config.yaml").write_text("default_branch: feature-parent\n")
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = _resolve_chain_default_branch("cdc-tasks", "task-1", "main")
        finally:
            os.chdir(original_cwd)
        assert result == "feature-parent"

    def test_per_task_config_without_default_branch_falls_back(self, tmp_path: Path) -> None:
        import os

        task_dir = tmp_path / "cdc-tasks" / "task-1"
        task_dir.mkdir(parents=True)
        (task_dir / "config.yaml").write_text("plan:\n  model: claude-opus-4-7\n")
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = _resolve_chain_default_branch("cdc-tasks", "task-1", "main")
        finally:
            os.chdir(original_cwd)
        assert result == "main"

    def test_invalid_per_task_yaml_exits(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import os

        task_dir = tmp_path / "cdc-tasks" / "task-1"
        task_dir.mkdir(parents=True)
        (task_dir / "config.yaml").write_text("not-a-mapping\n")
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                _resolve_chain_default_branch("cdc-tasks", "task-1", "main")
        finally:
            os.chdir(original_cwd)
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "invalid config.yaml for task task-1" in err


class TestRunChainMode:
    def _make_setup_patch(
        self,
        mock_setup: MagicMock,
        mock_svc: MagicMock,
        default_branch: str = "main",
        tasks_root: str = "cdc-tasks",
    ) -> None:
        from cadence.config import Config

        cfg = Config(tasks_root=tasks_root, default_branch=default_branch)
        mock_setup.return_value = (
            cfg,
            MagicMock(),
            MagicMock(),
            mock_svc,
            MagicMock(),
            default_branch,
            None,
        )

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_chain_file_missing_exits(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        self._make_setup_patch(mock_setup, mock_svc)

        missing = tmp_path / "no-such.txt"
        with pytest.raises(SystemExit) as excinfo:
            run_chain_mode(missing)
        assert excinfo.value.code == 1
        assert f"file not found: {missing}" in capsys.readouterr().err
        mock_run_run.assert_not_called()

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_chain_file_empty_exits(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        self._make_setup_patch(mock_setup, mock_svc)

        chain = tmp_path / "chain.txt"
        chain.write_text("\n# only comment\n   \n")
        with pytest.raises(SystemExit) as excinfo:
            run_chain_mode(chain)
        assert excinfo.value.code == 1
        assert "chain file is empty" in capsys.readouterr().err
        mock_run_run.assert_not_called()

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_invalid_task_name_exits(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        self._make_setup_patch(mock_setup, mock_svc)

        chain = tmp_path / "chain.txt"
        chain.write_text("ok-name\n../evil\n")
        with pytest.raises(SystemExit) as excinfo:
            run_chain_mode(chain)
        assert excinfo.value.code == 1
        assert "invalid task name in chain file" in capsys.readouterr().err
        mock_run_run.assert_not_called()

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_missing_task_dir_emits_warning_and_exits(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        self._make_setup_patch(mock_setup, mock_svc)

        chain = tmp_path / "chain.txt"
        chain.write_text("ghost-task\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "warn:" in err
        assert "task directory not found" in err
        assert "ghost-task" in err
        mock_run_run.assert_not_called()

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_missing_init_file_emits_warning_and_exits(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        self._make_setup_patch(mock_setup, mock_svc)

        (tmp_path / "cdc-tasks" / "no-init-task").mkdir(parents=True)
        chain = tmp_path / "chain.txt"
        chain.write_text("no-init-task\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "init file not found" in err
        mock_run_run.assert_not_called()

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_dirty_working_tree_exits(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = True
        mock_svc.current_branch.return_value = "main"
        self._make_setup_patch(mock_setup, mock_svc)

        chain = tmp_path / "chain.txt"
        chain.write_text("task-a\n")
        with pytest.raises(SystemExit) as excinfo:
            run_chain_mode(chain)
        assert excinfo.value.code == 1
        assert "uncommitted changes present" in capsys.readouterr().err
        mock_run_run.assert_not_called()

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_detached_head_exits(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = ""
        self._make_setup_patch(mock_setup, mock_svc)

        chain = tmp_path / "chain.txt"
        chain.write_text("task-a\n")
        with pytest.raises(SystemExit) as excinfo:
            run_chain_mode(chain)
        assert excinfo.value.code == 1
        assert "cannot --chain from a detached HEAD" in capsys.readouterr().err
        mock_run_run.assert_not_called()

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_happy_path_three_tasks(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        self._make_setup_patch(mock_setup, mock_svc, default_branch="main")

        for name in ("alpha", "beta", "gamma"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()
        (tmp_path / "cdc-tasks" / "beta" / "config.yaml").write_text(
            "default_branch: feature-parent\n"
        )

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\nbeta\ngamma\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)

        assert mock_run_run.call_count == 3
        for call in mock_run_run.call_args_list:
            _, kwargs = call
            assert kwargs["impl"] is True
            assert kwargs["squash"] is True

        create_calls = mock_svc.create_branch_from.call_args_list
        assert [c.args for c in create_calls] == [
            ("alpha", "main"),
            ("beta", "feature-parent"),
            ("gamma", "main"),
        ]
        mock_svc.checkout_branch.assert_not_called()

        out = capsys.readouterr().out
        assert "[chain 1/3] alpha" in out
        assert "[chain 2/3] beta" in out
        assert "[chain 3/3] gamma" in out
        assert "chain complete: 3 task(s)" in out

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_existing_branch_uses_checkout(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.side_effect = lambda name: name == "alpha"
        self._make_setup_patch(mock_setup, mock_svc)

        for name in ("alpha", "beta"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\nbeta\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)

        mock_svc.checkout_branch.assert_called_once_with("alpha")
        mock_svc.create_branch_from.assert_called_once_with("beta", "main")
        assert mock_run_run.call_count == 2

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_fail_fast_stops_chain(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        self._make_setup_patch(mock_setup, mock_svc)

        for name in ("a", "b", "c"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("a\nb\nc\n")

        def side_effect(*, impl: bool, squash: bool, config: Path | None) -> None:
            if mock_run_run.call_count == 2:
                raise SystemExit(1)

        mock_run_run.side_effect = side_effect

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert mock_run_run.call_count == 2
        err = capsys.readouterr().err
        assert "chain failed at task 2/3: b" in err

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_config_propagated_to_run_run_mode(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        self._make_setup_patch(mock_setup, mock_svc)

        (tmp_path / "cdc-tasks" / "only").mkdir(parents=True)
        (tmp_path / "cdc-tasks" / "only" / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("only\n")
        cfg_path = tmp_path / "override.yaml"
        cfg_path.write_text("default_branch: main\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_mode(chain, config=cfg_path)
        finally:
            os.chdir(original_cwd)

        mock_run_run.assert_called_once_with(impl=True, squash=True, config=cfg_path)

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_git_op_runtime_error_reports_chain_position(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_svc.create_branch_from.side_effect = RuntimeError("base ref does not resolve")
        self._make_setup_patch(mock_setup, mock_svc)

        for name in ("a", "b"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("a\nb\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "error: base ref does not resolve" in err
        assert "chain failed at task 1/2: a" in err
        mock_run_run.assert_not_called()

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_invalid_per_task_yaml_reports_chain_position(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        self._make_setup_patch(mock_setup, mock_svc)

        for name in ("first", "second"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()
        (tmp_path / "cdc-tasks" / "second" / "config.yaml").write_text("not-a-mapping\n")

        chain = tmp_path / "chain.txt"
        chain.write_text("first\nsecond\n")

        def side_effect(*, impl: bool, squash: bool, config: Path | None) -> None:
            return None

        mock_run_run.side_effect = side_effect

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "invalid config.yaml for task second" in err
        assert "chain failed at task 2/2: second" in err

    @patch("cadence.cli.run_run_mode")
    @patch("cadence.cli._setup_runtime")
    def test_keyboard_interrupt_swallowed_by_inner_mode_stops_chain(
        self,
        mock_setup: MagicMock,
        mock_run_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.cli import _sigint

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        self._make_setup_patch(mock_setup, mock_svc)

        for name in ("a", "b", "c"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("a\nb\nc\n")

        def side_effect(*, impl: bool, squash: bool, config: Path | None) -> None:
            if mock_run_run.call_count == 2:
                _sigint.shutdown_event.set()

        mock_run_run.side_effect = side_effect

        _sigint.reset()
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)
            _sigint.reset()

        assert excinfo.value.code == 1
        assert mock_run_run.call_count == 2
        err = capsys.readouterr().err
        assert "chain interrupted at task 2/3: b" in err
