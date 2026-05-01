from __future__ import annotations

import signal
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer import BadParameter

from rlx.cli import (
    _build_review_executor,
    _sigint,
    check_claude_dep,
    derive_plan_path,
    determine_mode,
    display_stats,
    resolve_version,
    run_plan_mode,
    run_review_mode,
    run_task_mode,
    to_rel_path,
)
from rlx.executor.claude_executor import Result
from rlx.git import DiffStats
from rlx.processor.runner import UserAbortedError
from rlx.status import Mode, SignalCompleted, SignalPlanReady, SignalReviewDone


class TestResolveVersion:
    def test_returns_version_string(self) -> None:
        from rlx import __version__

        v = resolve_version()
        assert v == __version__


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

        from rlx import __version__
        from rlx.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--version"])
        assert "rlx" in result.output
        assert __version__ in result.output

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

    @patch("rlx.cli.run_task_mode")
    def test_task_flag_calls_run_task_mode(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f)])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[0] == f

    @patch("rlx.cli.run_review_mode")
    def test_review_flag_calls_run_review_mode(
        self, mock_run: MagicMock
    ) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--review"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(None, config=None)

    def test_review_with_impl_errors(self) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--review", "--impl"])
        assert result.exit_code != 0
        assert "--review is incompatible with --impl" in result.output

    def test_review_with_task_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f), "--review"])
        assert result.exit_code != 0

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

    @patch("rlx.cli.run_review_mode")
    def test_base_with_review_passes(self, mock_run: MagicMock) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--review", "--base", "develop"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with("develop", config=None)

    def test_base_without_review_errors(self) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--base", "develop"])
        assert result.exit_code != 0
        assert "--base is only valid with --review" in result.output

    def test_base_with_plan_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")
        result = runner.invoke(app, ["--plan", str(f), "--base", "develop"])
        assert result.exit_code != 0
        assert "--base is only valid with --review" in result.output

    def test_base_with_task_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        f = tmp_path / "plan.md"
        f.write_text("task file")
        result = runner.invoke(app, ["--task", str(f), "--base", "develop"])
        assert result.exit_code != 0
        assert "--base is only valid with --review" in result.output


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

    def test_preprompt_bare_maps_to_plan(self, tmp_path: Path) -> None:
        prompt = tmp_path / "preprompt"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "plan")

    def test_preprompt_with_extension_maps_to_plan(self, tmp_path: Path) -> None:
        prompt = tmp_path / "preprompt.md"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "plan.md")

    def test_preprompt_substring_replaced(self, tmp_path: Path) -> None:
        prompt = tmp_path / "fix-preprompt.md"
        result = derive_plan_path(prompt)
        assert result == str(tmp_path / "fix-plan.md")


class TestRunPlanModeImplFlag:
    @patch("rlx.cli.run_task_mode")
    @patch("rlx.cli.typer.echo")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_impl_flag_chains_to_task_mode(
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
        mock_run_task_mode: MagicMock,
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

        parent = MagicMock()
        parent.attach_mock(mock_log.close, "close")
        parent.attach_mock(mock_run_task_mode, "task")

        run_plan_mode(f, impl=True)

        echo_calls = [str(c) for c in mock_echo.call_args_list]
        assert any("rlx --task" in c for c in echo_calls)
        assert not any("not available in v0.1" in c for c in echo_calls)
        mock_run_task_mode.assert_called_once_with(
            Path(derive_plan_path(f)), config=None
        )
        ordered_names = [c[0] for c in parent.mock_calls]
        assert ordered_names.index("close") < ordered_names.index("task")

    @patch("rlx.cli.run_task_mode")
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
        mock_run_task_mode: MagicMock,
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
        mock_run_task_mode.assert_not_called()

    @patch("rlx.cli.run_task_mode")
    @patch("rlx.cli.typer.echo")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    @patch("rlx.cli.Runner")
    def test_impl_flag_does_not_chain_on_plan_failure(
        self,
        mock_runner_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _branch: MagicMock,
        _git: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_echo: MagicMock,
        mock_run_task_mode: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

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
        assert not any("rlx --task" in c for c in echo_calls)


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

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_happy_path_success(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m00s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats(
            files=2, additions=10, deletions=3
        )
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalCompleted
        )
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

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
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
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
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

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_service_init_failure(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)
        mock_service_cls.side_effect = RuntimeError("not a repo")

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1\n\n- [ ] do it\n")

        with pytest.raises(SystemExit):
            run_task_mode(f)

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_move_plan_failure_warns_but_succeeds(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m00s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_svc.mark_plan_completed.side_effect = RuntimeError("git failed")
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalCompleted
        )
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
        from rlx.cli import _install_sigquit

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


class TestBuildReviewExecutor:
    def test_returns_none_when_review_matches_task(self) -> None:
        from rlx.config import Config

        cfg = Config(task_model="sonnet", review_model="sonnet")
        result = _build_review_executor(
            cfg,
            activity_handler=lambda _t: None,
            output_handler=lambda _t: None,
            idle_timeout=0.0,
        )
        assert result is None

    @patch("rlx.cli.ClaudeExecutor")
    def test_builds_distinct_executor_when_review_differs(
        self, mock_executor_cls: MagicMock
    ) -> None:
        from rlx.config import Config

        cfg = Config(task_model="sonnet", review_model="opus")
        mock_executor_cls.return_value = MagicMock()
        result = _build_review_executor(
            cfg,
            activity_handler=lambda _t: None,
            output_handler=lambda _t: None,
            idle_timeout=0.0,
        )
        assert result is mock_executor_cls.return_value
        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "opus"
        assert kwargs["command"] == cfg.claude_command


class TestRunReviewMode:
    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_happy_path_success(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats(
            files=1, additions=4, deletions=2
        )
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalReviewDone
        )
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        with (
            patch("rlx.cli.display_stats") as mock_display,
            patch("rlx.cli.Runner") as mock_runner_cls,
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

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_review_mode_uses_single_executor_with_review_model(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

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
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        primary = MagicMock(name="primary")
        mock_executor_cls.return_value = primary

        mock_terminal_cls.return_value = MagicMock()

        with patch("rlx.cli.Runner") as mock_runner_cls:
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

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_review_model_equal_to_task_uses_single_executor(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

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
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        primary = MagicMock(name="primary")
        mock_executor_cls.return_value = primary

        mock_terminal_cls.return_value = MagicMock()

        with patch("rlx.cli.Runner") as mock_runner_cls:
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

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_base_arg_overrides_config_and_autodetect(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(
            iteration_delay_ms=0, default_branch="main"
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "should-not-be-used"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with (
            patch("rlx.cli.display_stats"),
            patch("rlx.cli.Runner") as mock_runner_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode(base="develop")

        mock_svc.diff_stats.assert_called_once_with("develop")
        mock_svc.get_default_branch.assert_not_called()
        mock_log.print.assert_any_call("base: %s", "develop")
        ctx_arg = mock_runner_cls.call_args.args[0]
        assert ctx_arg.default_branch == "develop"

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_base_none_uses_config_default_branch(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(
            iteration_delay_ms=0, default_branch="trunk"
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "should-not-be-used"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with (
            patch("rlx.cli.display_stats"),
            patch("rlx.cli.Runner") as mock_runner_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode(base=None)

        mock_svc.diff_stats.assert_called_once_with("trunk")
        mock_svc.get_default_branch.assert_not_called()
        mock_log.print.assert_any_call("base: %s", "trunk")
        ctx_arg = mock_runner_cls.call_args.args[0]
        assert ctx_arg.default_branch == "trunk"

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_base_none_falls_back_to_autodetect_when_config_empty(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(
            iteration_delay_ms=0, default_branch=""
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with (
            patch("rlx.cli.display_stats"),
            patch("rlx.cli.Runner") as mock_runner_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

        mock_svc.diff_stats.assert_called_once_with("main")
        mock_svc.get_default_branch.assert_called_once()
        mock_log.print.assert_any_call("base: %s", "main")
        ctx_arg = mock_runner_cls.call_args.args[0]
        assert ctx_arg.default_branch == "main"

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_runner_returns_false_skips_diff_stats(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with patch("rlx.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = False
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

        mock_svc.diff_stats.assert_not_called()
        mock_log.close.assert_called_once()

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
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
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with patch("rlx.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.side_effect = UserAbortedError("aborted")
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

        mock_log.print.assert_any_call("aborted by user")
        mock_log.close.assert_called_once()
        mock_svc.diff_stats.assert_not_called()


class TestRunTaskModeReviewExecutor:
    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_distinct_review_model_passes_review_executor(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

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
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        primary = MagicMock(name="primary")
        secondary = MagicMock(name="secondary")
        mock_executor_cls.side_effect = [primary, secondary]

        mock_terminal_cls.return_value = MagicMock()

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")

        with patch("rlx.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_task_mode(f)

            assert mock_executor_cls.call_count == 2
            deps = mock_runner_cls.call_args.args[2]
            assert deps.executor is primary
            assert deps.review_executor is secondary

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_matching_review_model_leaves_review_executor_none(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

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
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        primary = MagicMock(name="primary")
        mock_executor_cls.return_value = primary

        mock_terminal_cls.return_value = MagicMock()

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")

        with patch("rlx.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_task_mode(f)

            assert mock_executor_cls.call_count == 1
            deps = mock_runner_cls.call_args.args[2]
            assert deps.executor is primary
            assert deps.review_executor is None


class TestConfigFlag:
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_plan_explicit_config_applies_overrides(
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

    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_plan_autodiscovers_yaml_next_to_plan(
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
        mock_terminal_cls.return_value = MagicMock()

        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")
        yaml_path = tmp_path / "rlx-config.yaml"
        yaml_path.write_text("plan:\n  model: discovered-plan\n")

        run_plan_mode(plan_file)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "discovered-plan"

    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_plan_no_yaml_no_overrides(
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

        mock_config.return_value = Config(
            iteration_delay_ms=0, plan_model="toml-plan-default"
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalPlanReady
        )
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")

        run_plan_mode(plan_file)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "toml-plan-default"

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_task_explicit_config_applies_overrides(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
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

        with patch("rlx.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_task_mode(task_file, config=yaml_path)

        primary_kwargs = mock_executor_cls.call_args_list[0].kwargs
        assert primary_kwargs["model"] == "yaml-task"

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_task_autodiscovers_yaml_next_to_task(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        task_file = tmp_path / "plan.md"
        task_file.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")
        yaml_path = tmp_path / "rlx-config.yaml"
        yaml_path.write_text("task:\n  model: discovered-task\n")

        with patch("rlx.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_task_mode(task_file)

        primary_kwargs = mock_executor_cls.call_args_list[0].kwargs
        assert primary_kwargs["model"] == "discovered-task"

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_review_explicit_config_applies_overrides(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        yaml_path = tmp_path / "explicit.yaml"
        yaml_path.write_text("review:\n  model: yaml-review\n")

        with patch("rlx.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode(config=yaml_path)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "yaml-review"

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    @patch("rlx.cli.find_yaml_config")
    def test_review_skips_autodiscovery(
        self,
        mock_find: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(
            iteration_delay_ms=0, review_model="toml-review-default"
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor_cls.return_value = MagicMock()
        mock_terminal_cls.return_value = MagicMock()

        with patch("rlx.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_review_mode()

        mock_find.assert_not_called()
        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "toml-review-default"

    def test_explicit_missing_config_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")
        missing = tmp_path / "missing.yaml"

        result = runner.invoke(
            app, ["--plan", str(plan_file), "--config", str(missing)]
        )
        assert result.exit_code != 0
        assert "config file not found" in result.output

    def test_invalid_yaml_errors(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from rlx.cli import app

        runner = CliRunner()
        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("not-valid-yaml-no-colon\n")

        result = runner.invoke(
            app, ["--plan", str(plan_file), "--config", str(bad_yaml)]
        )
        assert result.exit_code != 0
        assert "error" in result.output.lower()

    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_explicit_config_wins_over_autodiscovery(
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
        mock_terminal_cls.return_value = MagicMock()

        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")

        sibling_yaml = tmp_path / "rlx-config.yaml"
        sibling_yaml.write_text("plan:\n  model: sibling-plan\n")

        explicit_yaml = tmp_path / "explicit.yaml"
        explicit_yaml.write_text("plan:\n  model: explicit-plan\n")

        run_plan_mode(plan_file, config=explicit_yaml)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "explicit-plan"

    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_autodiscovery_does_not_walk_to_parent(
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

        mock_config.return_value = Config(
            iteration_delay_ms=0, plan_model="toml-plan-default"
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalPlanReady
        )
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        parent_yaml = tmp_path / "rlx-config.yaml"
        parent_yaml.write_text("plan:\n  model: parent-yaml\n")

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        plan_file = subdir / "prompt.md"
        plan_file.write_text("implement feature X")

        run_plan_mode(plan_file)

        kwargs = mock_executor_cls.call_args.kwargs
        assert kwargs["model"] == "toml-plan-default"

    @patch("rlx.cli._install_sigquit")
    @patch("rlx.cli.TerminalCollector")
    @patch("rlx.cli.ClaudeExecutor")
    @patch("rlx.cli.Service")
    @patch("rlx.cli.is_git_repo", return_value=True)
    @patch("rlx.cli.get_default_branch", return_value="main")
    @patch("rlx.cli.load_config")
    @patch("rlx.cli.detect_local_dir", return_value=None)
    @patch("rlx.cli.check_claude_dep")
    @patch("rlx.cli.Logger")
    def test_impl_propagates_config_to_task_mode(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        _branch: MagicMock,
        _git: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from rlx.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.get_default_branch.return_value = "main"
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output="done", signal=SignalPlanReady
        )
        mock_executor_cls.return_value = mock_executor
        mock_terminal_cls.return_value = MagicMock()

        plan_file = tmp_path / "prompt.md"
        plan_file.write_text("implement feature X")
        derived_plan = tmp_path / "plan.md"
        derived_plan.write_text("# plan\n\n### Task 1: x\n\n- [x] done\n")

        explicit_yaml = tmp_path / "explicit.yaml"
        explicit_yaml.write_text(
            "plan:\n  model: yaml-plan\ntask:\n  model: yaml-task\n"
        )

        with patch("rlx.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner

            run_plan_mode(plan_file, impl=True, config=explicit_yaml)

        models_used = [
            call.kwargs["model"] for call in mock_executor_cls.call_args_list
        ]
        assert "yaml-plan" in models_used
        assert "yaml-task" in models_used
