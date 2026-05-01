from __future__ import annotations

import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer import BadParameter

from rlx.cli import (
    _sigint,
    check_claude_dep,
    derive_plan_path,
    determine_mode,
    resolve_version,
    run_plan_mode,
    to_rel_path,
)
from rlx.executor.claude_executor import Result
from rlx.processor.runner import UserAbortedError
from rlx.status import Mode, SignalPlanReady


class TestResolveVersion:
    def test_returns_version_string(self) -> None:
        v = resolve_version()
        assert v == "0.1.0"


class TestDetermineMode:
    def test_plan_mode(self, tmp_path: Path) -> None:
        assert determine_mode(tmp_path / "f.md", None, False) == Mode.PLAN

    def test_task_mode(self, tmp_path: Path) -> None:
        assert determine_mode(None, tmp_path / "f.md", False) == Mode.FULL

    def test_review_mode(self) -> None:
        assert determine_mode(None, None, True) == Mode.REVIEW

    def test_plan_takes_priority_over_review(self, tmp_path: Path) -> None:
        assert determine_mode(tmp_path / "f.md", None, True) == Mode.PLAN

    def test_no_mode_raises(self) -> None:
        with pytest.raises(BadParameter):
            determine_mode(None, None, False)

    def test_plan_takes_priority_over_task(self, tmp_path: Path) -> None:
        result = determine_mode(tmp_path / "a.md", tmp_path / "b.md", False)
        assert result == Mode.PLAN


class TestCheckClaudeDep:
    def test_passes_when_found(self) -> None:
        from rlx.config import Config

        with patch("rlx.cli.shutil.which", return_value="/usr/bin/claude"):
            check_claude_dep(Config())

    def test_fails_when_not_found(self) -> None:
        from rlx.config import Config

        with (
            patch("rlx.cli.shutil.which", return_value=None),
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
        with patch("rlx.cli.Path.cwd", return_value=cwd):
            result = to_rel_path(target)
        assert result == "src/file.py"

    def test_absolute_fallback(self, tmp_path: Path) -> None:
        target = tmp_path / "other" / "file.py"
        with patch("rlx.cli.Path.cwd", return_value=tmp_path / "project"):
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

    @patch("rlx.cli.is_git_repo", return_value=False)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    def test_not_git_repo(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config()
        f = tmp_path / "plan.md"
        f.write_text("implement feature X")
        with pytest.raises(SystemExit):
            run_plan_mode(f)

    @patch("rlx.cli.typer.echo")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_full_wiring_plan_ready(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _branch: MagicMock,
        _git: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_echo: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalPlanReady
        )
        mock_executor_cls.return_value = mock_executor

        mock_terminal = MagicMock()
        mock_terminal_cls.return_value = mock_terminal

        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")

        run_plan_mode(f)

        mock_executor.run.assert_called_once()
        mock_log.close.assert_called_once()
        mock_log.print.assert_any_call("plan is ready")
        mock_echo.assert_any_call(f"run: rlx --task {tmp_path / 'plan.md'}")

    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_executor_created_with_handlers(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _branch: MagicMock,
        _git: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalPlanReady
        )
        mock_executor_cls.return_value = mock_executor

        mock_terminal = MagicMock()
        mock_terminal_cls.return_value = mock_terminal

        f = tmp_path / "plan.md"
        f.write_text("implement feature X")

        run_plan_mode(f)

        call_kwargs = mock_executor_cls.call_args
        assert call_kwargs.kwargs.get("activity_handler") is not None
        assert call_kwargs.kwargs.get("output_handler") is not None

    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_user_aborted(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _branch: MagicMock,
        _git: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

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

        from rlx.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--version"])
        assert "rlx" in result.output
        assert "0.1.0" in result.output

    def test_mutual_exclusivity(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        f1 = tmp_path / "a.md"
        f1.write_text("plan")
        f2 = tmp_path / "b.md"
        f2.write_text("task")
        result = runner.invoke(
            app, ["--plan", str(f1), "--task", str(f2)]
        )
        assert result.exit_code != 0

    def test_task_mode_not_implemented(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f)])
        assert result.exit_code != 0
        assert "not implemented" in result.output

    def test_review_mode_not_implemented(self) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--review"])
        assert result.exit_code != 0
        assert "not implemented" in result.output

    def test_no_args_shows_error(self) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        result = runner.invoke(app, [])
        assert result.exit_code != 0

    def test_impl_without_plan_errors(self) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--impl"])
        assert result.exit_code != 0
        assert "--impl requires --plan" in result.output

    def test_impl_with_task_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f), "--impl"])
        assert result.exit_code != 0

    @patch("rlx.cli.run_plan_mode")
    def test_impl_with_plan_passes(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")
        result = runner.invoke(app, ["--plan", str(f), "--impl"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["impl"] is True


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


class TestRunPlanModeImplFlag:
    @patch("rlx.cli.typer.echo")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_impl_flag_shows_not_available(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _branch: MagicMock,
        _git: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_echo: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalPlanReady
        )
        mock_executor_cls.return_value = mock_executor

        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")

        run_plan_mode(f, impl=True)

        echo_calls = [str(c) for c in mock_echo.call_args_list]
        assert any("rlx --task" in c for c in echo_calls)
        assert any("not available in v0.1" in c for c in echo_calls)

    @patch("rlx.cli.typer.echo")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_no_impl_flag_no_not_available_message(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _branch: MagicMock,
        _git: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_echo: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalPlanReady
        )
        mock_executor_cls.return_value = mock_executor

        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")

        run_plan_mode(f, impl=False)

        echo_calls = [str(c) for c in mock_echo.call_args_list]
        assert any("rlx --task" in c for c in echo_calls)
        assert not any("not available in v0.1" in c for c in echo_calls)
