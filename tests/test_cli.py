from __future__ import annotations

import datetime
import re
import signal
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cadence.cli import (
    _auto_detect_and_run,
    _build_logger,
    _parse_chain_file,
    _resolve_chain_default_branch,
    _run_plan_on_current_branch,
    _run_task_on_current_branch,
    _setup_runtime,
    _sigint,
    _validate_chain_tasks,
    app,
    check_claude_dep,
    compute_progress_path,
    compute_report_path,
    derive_plan_path,
    display_stats,
    find_existing_plan,
    resolve_version,
    run_chain_mode,
    run_chain_parallel,
    run_plan_mode,
    run_report_api_changes_mode,
    run_report_test_cases_mode,
    run_review_mode,
    run_squash_mode,
    run_status_mode,
    run_task_init_mode,
    run_task_mode,
    to_rel_path,
)
from cadence.executor.claude_executor import Result
from cadence.git import DiffStats
from cadence.processor.runner import UserAbortedError
from cadence.status import (
    Mode,
    SignalCompleted,
    SignalPlanReady,
    SignalReportDone,
    SignalReviewDone,
)


class TestResolveVersion:
    def test_returns_version_string(self) -> None:
        from cadence import __version__

        v = resolve_version()
        assert v == __version__

    def test_does_not_return_unknown(self) -> None:
        assert resolve_version() != "unknown"


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
        with pytest.raises(SystemExit) as excinfo:
            run_plan_mode(tmp_path / "nonexistent.md")
        assert excinfo.value.code == 2

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.md"
        f.write_text("")
        with pytest.raises(SystemExit) as excinfo:
            run_plan_mode(f)
        assert excinfo.value.code == 2

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
        with pytest.raises(SystemExit) as excinfo:
            run_plan_mode(f)
        assert excinfo.value.code == 1

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
        mock_echo.assert_any_call(f"run: cadence task {tmp_path / 'plan.md'}")

    @patch("cadence.cli.typer.echo")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_repo_path_suppresses_run_task_hint(
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

        worktree = tmp_path / "wt"
        worktree.mkdir()
        f = tmp_path / "prompt.md"
        f.write_text("implement feature X")

        run_plan_mode(f, repo_path=str(worktree))

        for call in mock_echo.call_args_list:
            args, _ = call
            if args and isinstance(args[0], str):
                assert not args[0].startswith("run: cadence task ")

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

    def test_repo_path_plumbs_to_setup_runtime(self, tmp_path: Path) -> None:
        f = tmp_path / "init"
        f.write_text("implement feature X")
        worktree = str(tmp_path / "wt")

        with patch("cadence.cli._setup_runtime") as mock_setup:
            mock_setup.side_effect = SystemExit(99)
            with pytest.raises(SystemExit) as excinfo:
                run_plan_mode(f, repo_path=worktree)
            assert excinfo.value.code == 99

        kwargs = mock_setup.call_args.kwargs
        assert kwargs["repo_path"] == worktree
        assert kwargs["claude_cwd"] == worktree

    def test_no_repo_path_passes_dot_and_none(self, tmp_path: Path) -> None:
        f = tmp_path / "init"
        f.write_text("implement feature X")

        with patch("cadence.cli._setup_runtime") as mock_setup:
            mock_setup.side_effect = SystemExit(99)
            with pytest.raises(SystemExit):
                run_plan_mode(f)

        kwargs = mock_setup.call_args.kwargs
        assert kwargs["repo_path"] == "."
        assert kwargs["claude_cwd"] is None

    @patch("cadence.cli.Runner")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_injected_input_collector_is_used(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_runner_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)
        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log
        mock_runner = MagicMock()
        mock_runner.run.return_value = True
        mock_runner_cls.return_value = mock_runner

        f = tmp_path / "init"
        f.write_text("implement feature X")

        sentinel_collector = MagicMock(name="sentinel_collector")

        run_plan_mode(f, input_collector=sentinel_collector)

        deps = mock_runner_cls.call_args.args[2]
        assert deps.input_collector is sentinel_collector
        mock_terminal_cls.assert_not_called()

    @patch("cadence.cli.Runner")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_default_input_collector_is_terminal(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_runner_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)
        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log
        mock_runner = MagicMock()
        mock_runner.run.return_value = True
        mock_runner_cls.return_value = mock_runner
        terminal_instance = MagicMock(name="terminal_instance")
        mock_terminal_cls.return_value = terminal_instance

        f = tmp_path / "init"
        f.write_text("implement feature X")

        run_plan_mode(f)

        mock_terminal_cls.assert_called_once_with()
        deps = mock_runner_cls.call_args.args[2]
        assert deps.input_collector is terminal_instance

    @patch("cadence.cli.Runner")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.compute_progress_path")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_repo_path_resolves_plan_file_to_absolute(
        self,
        _svc: MagicMock,
        mock_logger_cls: MagicMock,
        mock_progress: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_runner_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import os as _os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)
        mock_log = MagicMock()
        absolute_progress = str(tmp_path / "abs-progress.txt")
        mock_log.path = absolute_progress
        mock_logger_cls.return_value = mock_log

        captured_progress: list[str] = []

        def progress_side_effect(_mode: object, **kwargs: object) -> str:
            pf = kwargs.get("plan_file")
            assert isinstance(pf, str)
            assert _os.path.isabs(pf)
            assert str(tmp_path) in pf
            result = _os.path.join(_os.path.dirname(pf), "progress-plan.txt")
            captured_progress.append(result)
            return result

        mock_progress.side_effect = progress_side_effect
        mock_runner = MagicMock()
        mock_runner.run.return_value = True
        mock_runner_cls.return_value = mock_runner

        worktree_dir = tmp_path / "wt"
        worktree_dir.mkdir()
        plan_dir = tmp_path / "main-repo" / "cdc-tasks" / "feat"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "init"
        plan_file.write_text("implement X")

        monkeypatch.chdir(tmp_path / "main-repo")

        run_plan_mode(plan_file, repo_path=str(worktree_dir))

        assert captured_progress
        assert _os.path.isabs(captured_progress[0])
        assert str(tmp_path) in captured_progress[0]


class TestRunPlanModeImport:
    def test_missing_import_path_exits_2_before_executor(self, tmp_path: Path) -> None:
        from cadence.config import Config

        plan_file = tmp_path / "init"
        plan_file.write_text("brief")

        missing = tmp_path / "no-such.md"

        with (
            patch("cadence.cli.ClaudeExecutor") as mock_executor_cls,
            patch("cadence.cli._setup_runtime") as mock_setup,
            patch("cadence.cli.Logger"),
        ):
            mock_setup.return_value = (
                Config(iteration_delay_ms=0),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                "main",
                None,
            )
            with pytest.raises(SystemExit) as excinfo:
                run_plan_mode(plan_file, import_path=missing)
            assert excinfo.value.code == 2
            mock_executor_cls.assert_not_called()

    def test_oversized_import_exits_2_before_executor(self, tmp_path: Path) -> None:
        from cadence.config import Config

        plan_file = tmp_path / "init"
        plan_file.write_text("brief")

        big = tmp_path / "big.md"
        big.write_bytes(b"x" * 100)

        with (
            patch("cadence.cli.ClaudeExecutor") as mock_executor_cls,
            patch("cadence.cli._setup_runtime") as mock_setup,
            patch("cadence.cli.Logger"),
        ):
            mock_setup.return_value = (
                Config(iteration_delay_ms=0, import_max_bytes=10),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                "main",
                None,
            )
            with pytest.raises(SystemExit) as excinfo:
                run_plan_mode(plan_file, import_path=big)
            assert excinfo.value.code == 2
            mock_executor_cls.assert_not_called()

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_path_bound_import_includes_external_brief_in_prompt(
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

        brief = tmp_path / "brief.md"
        brief.write_text("EXTERNAL_BRIEF_BODY")

        run_plan_mode(brief, import_path=brief, init_content_override="")

        prompt = mock_executor.run.call_args.args[0]
        abs_brief = str(brief.resolve())
        assert f"# External brief (imported from {abs_brief})" in prompt
        assert "EXTERNAL_BRIEF_BODY" in prompt
        assert "# Task brief (init)" not in prompt

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_branch_bound_style_includes_both_init_and_external(
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

        init_file = tmp_path / "init"
        init_file.write_text("INIT_CONTENT_BODY")

        brief = tmp_path / "brief.md"
        brief.write_text("EXTERNAL_BRIEF_BODY")

        run_plan_mode(init_file, import_path=brief)

        prompt = mock_executor.run.call_args.args[0]
        assert "# Task brief (init)" in prompt
        assert "INIT_CONTENT_BODY" in prompt
        assert "# External brief" in prompt
        assert "EXTERNAL_BRIEF_BODY" in prompt

    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_qa_loop_still_triggers_with_import(
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

        question_payload = (
            '<<<CADENCE:QUESTION>>>\n{"question": "DB?", "options": ["pg", "sqlite"]}\n'
            "<<<CADENCE:END>>>"
        )
        mock_executor = MagicMock()
        mock_executor.run.side_effect = [
            Result(output=question_payload, signal=""),
            Result(output="done", signal=SignalPlanReady),
        ]
        mock_executor_cls.return_value = mock_executor

        mock_collector = MagicMock()
        mock_collector.ask_question.return_value = "pg"
        mock_terminal_cls.return_value = mock_collector

        brief = tmp_path / "brief.md"
        brief.write_text("EXTERNAL")

        run_plan_mode(brief, import_path=brief, init_content_override="")

        assert mock_collector.ask_question.called
        assert mock_executor.run.call_count >= 2


class TestSubcommandRouting:
    @staticmethod
    def _runner() -> Any:
        from typer.testing import CliRunner

        return CliRunner()

    def test_no_args_shows_help(self) -> None:
        result = self._runner().invoke(app, [])
        assert result.exit_code == 2

    def test_version_flag_prints_version(self) -> None:
        from cadence import __version__

        result = self._runner().invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "cadence" in result.output
        assert __version__ in result.output

    def test_help_lists_subcommands(self) -> None:
        import re

        result = self._runner().invoke(app, ["--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        for name in ("init", "run", "plan", "task", "review", "squash", "chain"):
            assert name in plain
        assert "--install-completion" in plain

    def test_run_help_lists_plan_and_task(self) -> None:
        result = self._runner().invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "plan" in result.output
        assert "task" in result.output

    def test_unknown_subcommand_exits_2(self) -> None:
        result = self._runner().invoke(app, ["bogus"])
        assert result.exit_code == 2

    @patch("cadence.cli.run_task_init_mode")
    def test_init_calls_run_task_init_mode(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["init", "feat-x"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with("feat-x", config=None, template=None)

    @patch("cadence.cli.run_task_init_mode")
    def test_init_forwards_template_flag(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["init", "feat-x", "--template", "feature"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with("feat-x", config=None, template="feature")

    @patch("cadence.cli._auto_detect_and_run")
    def test_run_no_subcommand_calls_auto_detect(self, mock_auto: MagicMock) -> None:
        result = self._runner().invoke(app, ["run"])
        assert result.exit_code == 0
        mock_auto.assert_called_once_with(config=None)

    @patch("cadence.cli._run_plan_on_current_branch")
    def test_run_plan_calls_branch_plan(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["run", "plan"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(config=None, import_path=None)

    @patch("cadence.cli._run_task_on_current_branch")
    def test_run_task_calls_branch_task(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["run", "task"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(config=None)

    @patch("cadence.cli.run_plan_mode")
    def test_plan_subcommand_calls_run_plan_mode(self, mock_run: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "prompt.md"
        f.write_text("implement X")
        result = self._runner().invoke(app, ["plan", str(f)])
        assert result.exit_code == 0
        args, kwargs = mock_run.call_args
        assert args[0] == f
        assert kwargs == {"config": None}

    @patch("cadence.cli.run_task_mode")
    def test_task_subcommand_calls_run_task_mode(self, mock_run: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "plan.md"
        f.write_text("# plan\n")
        result = self._runner().invoke(app, ["task", str(f)])
        assert result.exit_code == 0
        args, kwargs = mock_run.call_args
        assert args[0] == f
        assert kwargs == {"config": None}

    @patch("cadence.cli.run_review_mode")
    def test_review_no_base(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["review"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(None, config=None)

    @patch("cadence.cli.run_review_mode")
    def test_review_with_base(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["review", "--base", "origin/develop"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with("origin/develop", config=None)

    @patch("cadence.cli.run_squash_mode")
    def test_squash_calls_run_squash_mode(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["squash"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(config=None)

    @patch("cadence.cli.run_chain_mode")
    def test_chain_calls_run_chain_mode(self, mock_run: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "chain.txt"
        f.write_text("a\n")
        result = self._runner().invoke(app, ["chain", str(f)])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(f, config=None)

    @patch("cadence.cli.run_chain_mode")
    def test_global_config_propagates_to_chain(self, mock_run: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "chain.txt"
        f.write_text("a\n")
        cfg = tmp_path / "override.yaml"
        cfg.write_text("default_branch: main\n")
        result = self._runner().invoke(app, ["--config", str(cfg), "chain", str(f)])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(f, config=cfg)

    @patch("cadence.cli.run_plan_mode")
    def test_global_config_propagates_to_plan(self, mock_run: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "prompt.md"
        f.write_text("implement X")
        cfg = tmp_path / "override.yaml"
        cfg.write_text("default_branch: main\n")
        result = self._runner().invoke(app, ["--config", str(cfg), "plan", str(f)])
        assert result.exit_code == 0
        _args, kwargs = mock_run.call_args
        assert kwargs == {"config": cfg}

    @patch("cadence.cli.run_plan_mode")
    def test_plan_with_import_flag_passes_path_and_empty_init(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        f = tmp_path / "brief.md"
        f.write_text("external doc")
        result = self._runner().invoke(app, ["plan", str(f), "--import"])
        assert result.exit_code == 0
        args, kwargs = mock_run.call_args
        assert args[0] == f
        assert kwargs == {"config": None, "import_path": f, "init_content_override": ""}

    @patch("cadence.cli.run_plan_mode")
    def test_plan_without_import_keeps_default_kwargs(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        f = tmp_path / "prompt.md"
        f.write_text("implement X")
        result = self._runner().invoke(app, ["plan", str(f)])
        assert result.exit_code == 0
        args, kwargs = mock_run.call_args
        assert args[0] == f
        assert kwargs == {"config": None}

    @patch("cadence.cli._run_plan_on_current_branch")
    def test_run_plan_with_import_propagates_path(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        f = tmp_path / "brief.md"
        f.write_text("external doc")
        result = self._runner().invoke(app, ["run", "plan", "--import", str(f)])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(config=None, import_path=f)

    @patch("cadence.cli._run_plan_on_current_branch")
    def test_run_plan_without_import_propagates_none(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["run", "plan"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(config=None, import_path=None)

    def test_run_plan_import_without_value_exits_nonzero(self) -> None:
        result = self._runner().invoke(app, ["run", "plan", "--import"])
        assert result.exit_code != 0

    def test_plan_with_missing_path_exits_2(self, tmp_path: Path) -> None:
        result = self._runner().invoke(app, ["plan", str(tmp_path / "no-such.md")])
        assert result.exit_code == 2

    def test_task_with_missing_path_exits_2(self, tmp_path: Path) -> None:
        result = self._runner().invoke(app, ["task", str(tmp_path / "no-such.md")])
        assert result.exit_code == 2

    def test_explicit_missing_config_exits_2(self, tmp_path: Path) -> None:
        f = tmp_path / "prompt.md"
        f.write_text("implement X")
        missing = tmp_path / "missing.yaml"
        result = self._runner().invoke(app, ["--config", str(missing), "plan", str(f)])
        assert result.exit_code == 2
        assert "config file not found" in result.output


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
        with pytest.raises(SystemExit) as excinfo:
            run_task_mode(tmp_path / "nonexistent.md")
        assert excinfo.value.code == 2

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

        with pytest.raises(SystemExit) as excinfo:
            run_task_mode(f)
        assert excinfo.value.code == 1

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

    def test_repo_path_plumbs_to_setup_runtime(self, tmp_path: Path) -> None:
        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1\n\n- [ ] do it\n")
        worktree = str(tmp_path / "wt")

        with patch("cadence.cli._setup_runtime") as mock_setup:
            mock_setup.side_effect = SystemExit(99)
            with pytest.raises(SystemExit) as excinfo:
                run_task_mode(f, repo_path=worktree)
            assert excinfo.value.code == 99

        kwargs = mock_setup.call_args.kwargs
        assert kwargs["repo_path"] == worktree
        assert kwargs["claude_cwd"] == worktree

    def test_no_repo_path_passes_dot_and_none(self, tmp_path: Path) -> None:
        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1\n\n- [ ] do it\n")

        with patch("cadence.cli._setup_runtime") as mock_setup:
            mock_setup.side_effect = SystemExit(99)
            with pytest.raises(SystemExit):
                run_task_mode(f)

        kwargs = mock_setup.call_args.kwargs
        assert kwargs["repo_path"] == "."
        assert kwargs["claude_cwd"] is None

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.Runner")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.compute_progress_path")
    @patch("cadence.cli.Logger")
    def test_repo_path_resolves_plan_file_to_absolute(
        self,
        mock_logger_cls: MagicMock,
        mock_progress: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_runner_cls: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import os as _os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0)
        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "abs-progress.txt")
        mock_log.elapsed.return_value = "1m00s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_service_cls.return_value = mock_svc

        captured_plan_files: list[str] = []

        def progress_side_effect(_mode: object, **kwargs: object) -> str:
            pf = kwargs.get("plan_file")
            assert isinstance(pf, str)
            captured_plan_files.append(pf)
            return _os.path.join(_os.path.dirname(pf) or ".", "progress-task.txt")

        mock_progress.side_effect = progress_side_effect

        mock_runner = MagicMock()
        mock_runner.run.return_value = True
        mock_runner_cls.return_value = mock_runner

        worktree_dir = tmp_path / "wt"
        worktree_dir.mkdir()
        plan_dir = tmp_path / "main-repo" / "cdc-tasks" / "feat"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "plan"
        plan_file.write_text("# plan\n")

        monkeypatch.chdir(tmp_path / "main-repo")

        run_task_mode(plan_file, repo_path=str(worktree_dir))

        assert captured_plan_files
        assert _os.path.isabs(captured_plan_files[0])
        assert str(tmp_path) in captured_plan_files[0]


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

    @patch("cadence.cli.Service")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_default_repo_path_is_dot(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        _check: MagicMock,
        mock_service_cls: MagicMock,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()

        _setup_runtime(None, None)

        assert mock_service_cls.call_args.kwargs.get("path") == "."

    @patch("cadence.cli.Service")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_repo_path_passed_to_service(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        _check: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()
        worktree = str(tmp_path / "wt")

        _setup_runtime(None, None, repo_path=worktree, claude_cwd=worktree)

        assert mock_service_cls.call_args.kwargs["path"] == worktree

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_factory_passes_claude_cwd(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        _check: MagicMock,
        _svc: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config()
        worktree = str(tmp_path / "wt")

        _, _, _, _, factory, _, _ = _setup_runtime(
            None, None, repo_path=worktree, claude_cwd=worktree
        )
        factory(MagicMock(), "opus")

        assert mock_executor_cls.call_args.kwargs.get("cwd") == worktree

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_factory_default_claude_cwd_is_none(
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
        factory(MagicMock(), "opus")

        assert mock_executor_cls.call_args.kwargs.get("cwd") is None


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

        result = runner.invoke(app, ["--config", str(missing), "plan", str(plan_file)])
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

        result = runner.invoke(app, ["--config", str(bad_yaml), "plan", str(plan_file)])
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
        assert excinfo.value.code == 2
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
        assert excinfo.value.code == 2
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

        assert excinfo.value.code == 2
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

        assert excinfo.value.code == 2
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

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_template_not_found_exits_2_no_side_effects(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks", templates_dir=".cadence/templates"
        )

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_task_init_mode("feat-x", template="missing")
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert 'template "missing" not found' in err
        assert str(Path(".cadence/templates/missing.txt")) in err
        mock_svc.create_branch.assert_not_called()
        assert not (tmp_path / "cdc-tasks" / "feat-x").exists()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.subprocess.run")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_template_pre_fills_init(
        self,
        _detect: MagicMock,
        mock_subproc_run: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks", templates_dir=".cadence/templates"
        )

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        author_proc = MagicMock()
        author_proc.returncode = 0
        author_proc.stdout = ""
        mock_subproc_run.return_value = author_proc

        templates_dir = tmp_path / ".cadence" / "templates"
        templates_dir.mkdir(parents=True)
        (templates_dir / "feature.txt").write_text("hello world\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_task_init_mode("feat-x", template="feature")
        finally:
            os.chdir(original_cwd)

        init_file = tmp_path / "cdc-tasks" / "feat-x" / "init"
        assert init_file.read_text(encoding="utf-8") == "hello world\n"

    @patch("cadence.cli.subprocess.run")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_template_substitutes_variables(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_subproc_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks", templates_dir=".cadence/templates"
        )

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        author_proc = MagicMock()
        author_proc.returncode = 0
        author_proc.stdout = "Ada Lovelace\n"
        mock_subproc_run.return_value = author_proc

        class _FixedDate(datetime.date):
            @classmethod
            def today(cls) -> datetime.date:
                return datetime.date(2026, 5, 6)

        monkeypatch.setattr("cadence.cli.datetime.date", _FixedDate)

        templates_dir = tmp_path / ".cadence" / "templates"
        templates_dir.mkdir(parents=True)
        (templates_dir / "feature.txt").write_text(
            "task: {{task_name}} ({{task_name}})\n"
            "branch: {{branch}}\n"
            "date: {{date}}\n"
            "author: {{author}}\n",
            encoding="utf-8",
        )

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_task_init_mode("feat-x", template="feature")
        finally:
            os.chdir(original_cwd)

        rendered = (tmp_path / "cdc-tasks" / "feat-x" / "init").read_text(encoding="utf-8")
        assert rendered == (
            "task: feat-x (feat-x)\nbranch: feat-x\ndate: 2026-05-06\nauthor: Ada Lovelace\n"
        )
        mock_subproc_run.assert_called_once()
        called_args = mock_subproc_run.call_args.args[0]
        assert called_args == ["git", "config", "user.name"]

    @patch("cadence.cli.subprocess.run")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_unknown_variable_left_intact(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_subproc_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks", templates_dir=".cadence/templates"
        )

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        author_proc = MagicMock()
        author_proc.returncode = 0
        author_proc.stdout = ""
        mock_subproc_run.return_value = author_proc

        templates_dir = tmp_path / ".cadence" / "templates"
        templates_dir.mkdir(parents=True)
        (templates_dir / "feature.txt").write_text(
            "literal: {{foo}} {{task_name}}\n", encoding="utf-8"
        )

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_task_init_mode("feat-x", template="feature")
        finally:
            os.chdir(original_cwd)

        rendered = (tmp_path / "cdc-tasks" / "feat-x" / "init").read_text(encoding="utf-8")
        assert rendered == "literal: {{foo}} feat-x\n"

    @patch("cadence.cli.subprocess.run")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_template_missing_user_name_falls_back_to_empty(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_subproc_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks", templates_dir=".cadence/templates"
        )

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        author_proc = MagicMock()
        author_proc.returncode = 1
        author_proc.stdout = ""
        mock_subproc_run.return_value = author_proc

        templates_dir = tmp_path / ".cadence" / "templates"
        templates_dir.mkdir(parents=True)
        (templates_dir / "feature.txt").write_text("by [{{author}}]\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_task_init_mode("feat-x", template="feature")
        finally:
            os.chdir(original_cwd)

        rendered = (tmp_path / "cdc-tasks" / "feat-x" / "init").read_text(encoding="utf-8")
        assert rendered == "by []\n"

    @patch("cadence.cli.subprocess.run")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_template_subprocess_oserror_falls_back_to_empty_author(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_subproc_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks", templates_dir=".cadence/templates"
        )

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        mock_subproc_run.side_effect = FileNotFoundError("git not found")

        templates_dir = tmp_path / ".cadence" / "templates"
        templates_dir.mkdir(parents=True)
        (templates_dir / "feature.txt").write_text("by [{{author}}]\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_task_init_mode("feat-x", template="feature")
        finally:
            os.chdir(original_cwd)

        rendered = (tmp_path / "cdc-tasks" / "feat-x" / "init").read_text(encoding="utf-8")
        assert rendered == "by []\n"

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @pytest.mark.parametrize(
        "bad_name",
        ["", "../etc/passwd", "..", ".", "-flag", "foo/bar", "a\\b"],
    )
    def test_invalid_template_name_exits_2_no_side_effects(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        bad_name: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks", templates_dir=".cadence/templates"
        )

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_task_init_mode("feat-x", template=bad_name)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        assert "invalid template name" in capsys.readouterr().err
        mock_svc.create_branch.assert_not_called()
        assert not (tmp_path / "cdc-tasks" / "feat-x").exists()

    @patch("cadence.cli.subprocess.run")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_template_honors_custom_templates_dir(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_subproc_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", templates_dir="custom/templates")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        mock_service_cls.return_value = mock_svc

        author_proc = MagicMock()
        author_proc.returncode = 0
        author_proc.stdout = ""
        mock_subproc_run.return_value = author_proc

        templates_dir = tmp_path / "custom" / "templates"
        templates_dir.mkdir(parents=True)
        (templates_dir / "feature.txt").write_text("from custom dir\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_task_init_mode("feat-x", template="feature")
        finally:
            os.chdir(original_cwd)

        rendered = (tmp_path / "cdc-tasks" / "feat-x" / "init").read_text(encoding="utf-8")
        assert rendered == "from custom dir\n"


class TestRunAutoDetect:
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
            _auto_detect_and_run(config=None)
        assert excinfo.value.code == 1
        assert "not a repo" in capsys.readouterr().err

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_detached_head_exits_2(
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
            _auto_detect_and_run(config=None)
        assert excinfo.value.code == 2
        assert "detached HEAD" in capsys.readouterr().err
        mock_run_plan.assert_not_called()
        mock_run_task.assert_not_called()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_task_directory_missing_exits_2(
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
                _auto_detect_and_run(config=None)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        assert "task directory not found" in capsys.readouterr().err

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_default_branch_exits_2(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(default_branch="main")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_service_cls.return_value = mock_svc

        with pytest.raises(SystemExit) as excinfo:
            _auto_detect_and_run(config=None)
        assert excinfo.value.code == 2
        assert "cannot run on default branch main" in capsys.readouterr().err
        mock_run_plan.assert_not_called()
        mock_run_task.assert_not_called()

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_default_branch_origin_prefix_exits_2(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
        mock_run_task: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(default_branch="origin/develop")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "develop"
        mock_service_cls.return_value = mock_svc

        with pytest.raises(SystemExit) as excinfo:
            _auto_detect_and_run(config=None)
        assert excinfo.value.code == 2
        assert "cannot run on default branch develop" in capsys.readouterr().err

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_plan_completed_prints_message(
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
            _auto_detect_and_run(config=None)
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
    def test_plan_exists_calls_run_task_mode(
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
            _auto_detect_and_run(config=None)
        finally:
            os.chdir(original_cwd)

        mock_run_task.assert_called_once()
        args, kwargs = mock_run_task.call_args
        assert args[0] == Path("cdc-tasks/feat-x/plan")
        assert kwargs == {"config": None}
        mock_run_plan.assert_not_called()

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_init_missing_exits_2(
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
                _auto_detect_and_run(config=None)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        assert "init file not found" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_init_empty_exits_2(
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
                _auto_detect_and_run(config=None)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        assert "init file is empty" in capsys.readouterr().err

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_init_non_empty_calls_run_plan_mode(
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
            _auto_detect_and_run(config=None)
        finally:
            os.chdir(original_cwd)

        mock_run_plan.assert_called_once()
        args, kwargs = mock_run_plan.call_args
        assert args[0] == Path("cdc-tasks/feat-x/init")
        assert kwargs == {"config": None}
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
            _auto_detect_and_run(config=None)
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
            _auto_detect_and_run(config=None)
        finally:
            os.chdir(original_cwd)

        mock_run_task.assert_called_once()
        mock_run_plan.assert_not_called()

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
            _auto_detect_and_run(config=None)
        finally:
            os.chdir(original_cwd)

        assert "plan already completed" in capsys.readouterr().out
        mock_run_plan.assert_not_called()
        mock_run_task.assert_not_called()


class TestRunPlanSubcommand:
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_default_branch_exits_2(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(default_branch="main")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_service_cls.return_value = mock_svc

        with pytest.raises(SystemExit) as excinfo:
            _run_plan_on_current_branch(config=None)
        assert excinfo.value.code == 2
        assert "cannot run on default branch main" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_init_missing_exits_2(
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
                _run_plan_on_current_branch(config=None)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        assert "init file not found" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_init_empty_exits_2(
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
        (task_dir / "init").write_text("   \n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                _run_plan_on_current_branch(config=None)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        assert "init file is empty" in capsys.readouterr().err

    @patch("cadence.cli.run_plan_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_happy_path_calls_run_plan_mode(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_plan: MagicMock,
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
            _run_plan_on_current_branch(config=None)
        finally:
            os.chdir(original_cwd)

        mock_run_plan.assert_called_once()
        args, kwargs = mock_run_plan.call_args
        assert args[0] == Path("cdc-tasks/feat-x/init")
        assert kwargs == {
            "config": None,
            "repo_path": None,
            "input_collector": None,
            "chain_collector": None,
            "import_path": None,
        }


class TestRunTaskSubcommand:
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_default_branch_exits_2(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(default_branch="main")

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "main"
        mock_service_cls.return_value = mock_svc

        with pytest.raises(SystemExit) as excinfo:
            _run_task_on_current_branch(config=None)
        assert excinfo.value.code == 2
        assert "cannot run on default branch main" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_plan_missing_exits_2(
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
                _run_task_on_current_branch(config=None)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        assert "plan file not found" in capsys.readouterr().err

    @patch("cadence.cli.run_task_mode")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    def test_happy_path_calls_run_task_mode(
        self,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
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
        (task_dir / "plan").write_text("# plan", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            _run_task_on_current_branch(config=None)
        finally:
            os.chdir(original_cwd)

        mock_run_task.assert_called_once()
        args, kwargs = mock_run_task.call_args
        assert args[0] == Path("cdc-tasks/feat-x/plan")
        assert kwargs == {"config": None, "repo_path": None, "chain_collector": None}


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
        assert excinfo.value.code == 2
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
        assert excinfo.value.code == 2
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

        assert excinfo.value.code == 2
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

        assert excinfo.value.code == 2
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

        assert excinfo.value.code == 2
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

        assert excinfo.value.code == 2
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
    def test_one_commit_ahead_is_silent_in_repo_path_mode(
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
        worktree = tmp_path / "wt"
        worktree.mkdir()

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_squash_mode(repo_path=str(worktree))
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "single commit already" not in out
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
    def test_phase_summary_emitted_on_success(
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
        from cadence.executor.events import Usage

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
        mock_executor.run.return_value = Result(
            output=claude_output,
            usage=Usage(
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=200,
                cache_creation_tokens=10,
            ),
            model="claude-opus-4-7",
        )
        mock_executor_cls.return_value = mock_executor

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with patch("cadence.cli.display_stats"):
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        printed = "\n".join(
            " ".join(str(a) for a in call.args) for call in mock_log.print.call_args_list
        )
        assert "phase squash done in" in printed

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

    def test_repo_path_plumbs_to_setup_runtime(self, tmp_path: Path) -> None:
        worktree = str(tmp_path / "wt")

        with patch("cadence.cli._setup_runtime") as mock_setup:
            mock_setup.side_effect = SystemExit(99)
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode(repo_path=worktree)
            assert excinfo.value.code == 99

        kwargs = mock_setup.call_args.kwargs
        assert kwargs["repo_path"] == worktree
        assert kwargs["claude_cwd"] == worktree
        assert kwargs["anchor"] is None

    def test_no_repo_path_passes_dot_and_none(self) -> None:
        with patch("cadence.cli._setup_runtime") as mock_setup:
            mock_setup.side_effect = SystemExit(99)
            with pytest.raises(SystemExit):
                run_squash_mode()

        kwargs = mock_setup.call_args.kwargs
        assert kwargs["repo_path"] == "."
        assert kwargs["claude_cwd"] is None

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_executor_uses_squash_model_not_task_model(
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

        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            task_model="claude-opus-4-7",
            squash_model="claude-haiku-4-5",
        )
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
            with patch("cadence.cli.display_stats"):
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        mock_executor_cls.assert_called_once()
        assert mock_executor_cls.call_args.kwargs["model"] == "claude-haiku-4-5"

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_default_squash_model_flows_through(
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

        cfg = Config(tasks_root="cdc-tasks", default_branch="main")
        assert cfg.squash_model == "claude-sonnet-4-6"
        assert cfg.task_model == "claude-opus-4-7"
        mock_config.return_value = cfg
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
            with patch("cadence.cli.display_stats"):
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        mock_executor_cls.assert_called_once()
        assert mock_executor_cls.call_args.kwargs["model"] == "claude-sonnet-4-6"

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_phase_summary_uses_squash_model(
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

        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            task_model="claude-opus-4-7",
            squash_model="claude-haiku-4-5",
            print_usage=True,
        )
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
            with (
                patch("cadence.cli.display_stats"),
                patch("cadence.cli.format_phase_summary", return_value="summary") as mock_fmt,
            ):
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        mock_fmt.assert_called_once()
        args, kwargs = mock_fmt.call_args
        positional_models = [a for a in args if isinstance(a, str)]
        assert "claude-haiku-4-5" in positional_models or kwargs.get("model") == (
            "claude-haiku-4-5"
        )
        assert "claude-opus-4-7" not in positional_models
        assert kwargs.get("model") != "claude-opus-4-7"


class TestParseChainFile:
    def test_missing_file_exits(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        missing = tmp_path / "absent.txt"
        with pytest.raises(SystemExit) as excinfo:
            _parse_chain_file(missing)
        assert excinfo.value.code == 2
        assert f"file not found: {missing}" in capsys.readouterr().err

    def test_empty_file_exits(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        chain = tmp_path / "chain.txt"
        chain.write_text("")
        with pytest.raises(SystemExit) as excinfo:
            _parse_chain_file(chain)
        assert excinfo.value.code == 2
        assert "chain file is empty" in capsys.readouterr().err

    def test_only_blanks_and_comments_exits(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        chain = tmp_path / "chain.txt"
        chain.write_text("\n\n# comment\n   \n# another\n")
        with pytest.raises(SystemExit) as excinfo:
            _parse_chain_file(chain)
        assert excinfo.value.code == 2
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
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "invalid task name in chain file" in err
        assert name in err

    def test_duplicate_names_exit(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        chain = tmp_path / "chain.txt"
        chain.write_text("task-a\ntask-b\ntask-a\ntask-b\ntask-c\n")
        with pytest.raises(SystemExit) as excinfo:
            _parse_chain_file(chain)
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "duplicate task names in chain file" in err
        assert "task-a" in err
        assert "task-b" in err


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

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_chain_file_missing_exits(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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
        assert excinfo.value.code == 2
        assert f"file not found: {missing}" in capsys.readouterr().err
        mock_plan.assert_not_called()
        mock_task.assert_not_called()
        mock_squash.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_chain_file_empty_exits(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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
        assert excinfo.value.code == 2
        assert "chain file is empty" in capsys.readouterr().err
        mock_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_invalid_task_name_exits(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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
        assert excinfo.value.code == 2
        assert "invalid task name in chain file" in capsys.readouterr().err
        mock_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_missing_task_dir_emits_warning_and_exits(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "warn:" in err
        assert "task directory not found" in err
        assert "ghost-task" in err
        mock_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_missing_init_file_emits_warning_and_exits(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "init file not found" in err
        mock_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_dirty_working_tree_exits(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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
        assert excinfo.value.code == 2
        assert "uncommitted changes present" in capsys.readouterr().err
        mock_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_detached_head_exits(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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
        assert excinfo.value.code == 2
        assert "cannot chain from a detached HEAD" in capsys.readouterr().err
        mock_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_happy_path_three_tasks(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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

        assert mock_plan.call_count == 3
        assert mock_task.call_count == 3
        assert mock_squash.call_count == 3

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

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_existing_branch_uses_checkout(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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
        assert mock_plan.call_count == 2
        assert mock_squash.call_count == 2

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_fail_fast_stops_chain(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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

        def side_effect(*, config: Path | None, chain_collector: Any = None) -> None:
            if mock_plan.call_count == 2:
                raise SystemExit(1)

        mock_plan.side_effect = side_effect

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert mock_plan.call_count == 2
        err = capsys.readouterr().err
        assert "chain failed at task 2/3: b" in err

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_config_propagated_to_helpers(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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

        from cadence.usage import UsageStats

        plan_kwargs = mock_plan.call_args.kwargs
        assert plan_kwargs["config"] == cfg_path
        assert isinstance(plan_kwargs["chain_collector"], UsageStats)
        task_kwargs = mock_task.call_args.kwargs
        assert task_kwargs["config"] == cfg_path
        assert isinstance(task_kwargs["chain_collector"], UsageStats)
        squash_kwargs = mock_squash.call_args.kwargs
        assert squash_kwargs["config"] == cfg_path
        assert isinstance(squash_kwargs["chain_collector"], UsageStats)
        assert (
            plan_kwargs["chain_collector"]
            is task_kwargs["chain_collector"]
            is squash_kwargs["chain_collector"]
        )

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_git_op_runtime_error_reports_chain_position(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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
        mock_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_invalid_per_task_yaml_reports_chain_position(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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

        mock_plan.return_value = None
        mock_task.return_value = None
        mock_squash.return_value = None

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

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_keyboard_interrupt_swallowed_by_inner_mode_stops_chain(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
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

        def side_effect(*, config: Path | None, chain_collector: Any = None) -> None:
            if mock_plan.call_count == 2:
                _sigint.shutdown_event.set()

        mock_plan.side_effect = side_effect

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
        assert mock_plan.call_count == 2
        assert mock_task.call_count == 1
        assert mock_squash.call_count == 1
        captured = capsys.readouterr()
        err = captured.err
        out = captured.out
        assert "chain interrupted at task 2/3: b" in err
        assert "chain failed at task 2/3" not in err
        assert out.count("chain done in") == 1

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_chain_summary_printed_with_summed_counts(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.executor.events import Usage
        from cadence.usage import UsageStats

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        self._make_setup_patch(mock_setup, mock_svc)

        for name in ("alpha", "beta"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\nbeta\n")

        def plan_se(*, config: Any, chain_collector: Any, **kw: Any) -> None:
            assert isinstance(chain_collector, UsageStats)
            s = UsageStats()
            s.add(
                Usage(
                    input_tokens=100,
                    output_tokens=50,
                    cache_read_tokens=200,
                    cache_creation_tokens=10,
                ),
                duration_ms=1000,
            )
            chain_collector.merge(s)

        mock_plan.side_effect = plan_se

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "chain done in" in out
        assert "tasks 2" in out
        assert "iters 2" in out
        assert "in 200" in out
        assert "out 100" in out
        assert "cache_read 400" in out
        assert "cache_create 20" in out

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_chain_summary_suppressed_when_print_usage_false(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        cfg = Config(tasks_root="cdc-tasks", default_branch="main", print_usage=False)
        mock_setup.return_value = (
            cfg,
            MagicMock(),
            MagicMock(),
            mock_svc,
            MagicMock(),
            "main",
            None,
        )

        (tmp_path / "cdc-tasks" / "alpha").mkdir(parents=True)
        (tmp_path / "cdc-tasks" / "alpha" / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "chain done in" not in out

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_chain_summary_printed_when_task_fails(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.executor.events import Usage
        from cadence.usage import UsageStats

        mock_svc = MagicMock()
        mock_svc.is_dirty.return_value = False
        mock_svc.current_branch.return_value = "main"
        mock_svc.branch_exists.return_value = False
        self._make_setup_patch(mock_setup, mock_svc)

        for name in ("a", "b"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("a\nb\n")

        def plan_se(*, config: Any, chain_collector: Any, **kw: Any) -> None:
            s = UsageStats()
            s.add(
                Usage(
                    input_tokens=100,
                    output_tokens=50,
                    cache_read_tokens=200,
                    cache_creation_tokens=0,
                ),
                duration_ms=500,
            )
            chain_collector.merge(s)
            if mock_plan.call_count == 2:
                raise SystemExit(1)

        mock_plan.side_effect = plan_se

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_mode(chain)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "chain failed at task 2/2: b" in captured.err
        assert "chain done in" in captured.out
        assert "tasks 2" in captured.out
        assert "iters 2" in captured.out
        assert "in 200" in captured.out


class TestRunChainParallel:
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

    def _make_svc(self, tmp_path: Path) -> MagicMock:
        svc = MagicMock()
        svc.is_dirty.return_value = False
        svc.current_branch.return_value = "main"
        svc.branch_exists.return_value = False
        svc.worktree_exists.return_value = False
        svc.root.return_value = str(tmp_path)
        return svc

    @patch("cadence.cli.run_chain_parallel")
    @patch("cadence.cli.run_chain_mode")
    def test_parallel_one_routes_to_sequential(
        self,
        mock_seq: MagicMock,
        mock_par: MagicMock,
        tmp_path: Path,
    ) -> None:
        from typer.testing import CliRunner

        f = tmp_path / "chain.txt"
        f.write_text("a\n")
        result = CliRunner().invoke(app, ["chain", str(f), "--parallel", "1"])
        assert result.exit_code == 0
        mock_seq.assert_called_once_with(f, config=None)
        mock_par.assert_not_called()

    @patch("cadence.cli.run_chain_parallel")
    @patch("cadence.cli.run_chain_mode")
    def test_parallel_default_routes_to_sequential(
        self,
        mock_seq: MagicMock,
        mock_par: MagicMock,
        tmp_path: Path,
    ) -> None:
        from typer.testing import CliRunner

        f = tmp_path / "chain.txt"
        f.write_text("a\n")
        result = CliRunner().invoke(app, ["chain", str(f)])
        assert result.exit_code == 0
        mock_seq.assert_called_once_with(f, config=None)
        mock_par.assert_not_called()

    @patch("cadence.cli.run_chain_parallel")
    @patch("cadence.cli.run_chain_mode")
    def test_parallel_zero_exits_2(
        self,
        mock_seq: MagicMock,
        mock_par: MagicMock,
        tmp_path: Path,
    ) -> None:
        from typer.testing import CliRunner

        f = tmp_path / "chain.txt"
        f.write_text("a\n")
        result = CliRunner().invoke(app, ["chain", str(f), "--parallel", "0"])
        assert result.exit_code == 2
        assert "--parallel must be >= 1" in result.output
        mock_seq.assert_not_called()
        mock_par.assert_not_called()

    @patch("cadence.cli.run_chain_parallel")
    @patch("cadence.cli.run_chain_mode")
    def test_parallel_n_routes_to_parallel(
        self,
        mock_seq: MagicMock,
        mock_par: MagicMock,
        tmp_path: Path,
    ) -> None:
        from typer.testing import CliRunner

        f = tmp_path / "chain.txt"
        f.write_text("a\nb\n")
        result = CliRunner().invoke(app, ["chain", str(f), "--parallel", "3"])
        assert result.exit_code == 0
        mock_par.assert_called_once_with(f, parallel=3, config=None)
        mock_seq.assert_not_called()

    def test_chain_help_lists_parallel(self) -> None:
        from typer.testing import CliRunner

        result = CliRunner().invoke(app, ["chain", "--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--parallel" in plain

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_happy_path_three_workers(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        svc = self._make_svc(tmp_path)
        self._make_setup_patch(mock_setup, svc)

        for name in ("alpha", "beta", "gamma"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\nbeta\ngamma\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_parallel(chain, parallel=3)
        finally:
            os.chdir(original_cwd)

        assert mock_plan.call_count == 3
        assert mock_task.call_count == 3
        assert mock_squash.call_count == 3
        assert svc.worktree_add.call_count == 3
        assert svc.worktree_remove.call_count == 3

        added_paths = {c.args[0] for c in svc.worktree_add.call_args_list}
        for name in ("alpha", "beta", "gamma"):
            assert str(tmp_path / ".cadence" / "worktrees" / name) in added_paths

        out = capsys.readouterr().out
        assert "[chain] starting 3 parallel tasks" in out
        for name in ("alpha", "beta", "gamma"):
            assert f"[chain] {name}: started" in out
            assert f"[chain] {name}: completed" in out
        assert "[chain] complete: 3/3 succeeded" in out

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_fail_fast_cancels_pending(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        svc = self._make_svc(tmp_path)
        self._make_setup_patch(mock_setup, svc)

        for name in ("a", "b", "c"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("a\nb\nc\n")

        a_started = threading.Event()
        a_release = threading.Event()
        b_failed = threading.Event()

        def plan_side_effect(
            *,
            config: Path | None,
            repo_path: str | None = None,
            input_collector: Any = None,
            chain_collector: Any = None,
        ) -> None:
            assert repo_path is not None
            if repo_path.endswith("/a"):
                a_started.set()
                # wait until b has failed so c is queued and can be cancelled
                a_release.wait(timeout=5.0)
                return
            if repo_path.endswith("/b"):
                a_started.wait(timeout=5.0)
                b_failed.set()
                a_release.set()
                raise SystemExit(1)
            return

        mock_plan.side_effect = plan_side_effect

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_parallel(chain, parallel=2)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert b_failed.is_set()

        added_paths = [c.args[0] for c in svc.worktree_add.call_args_list]
        added_names = [Path(p).name for p in added_paths]
        assert "a" in added_names
        assert "b" in added_names
        assert "c" not in added_names

        removed_names = [Path(c.args[0]).name for c in svc.worktree_remove.call_args_list]
        assert removed_names == ["a"]

        out = capsys.readouterr().out
        assert "[chain] b: failed at plan" in out
        assert "[chain] complete: 1/3 succeeded" in out

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_preflight_worktree_path_collision(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        svc = self._make_svc(tmp_path)
        self._make_setup_patch(mock_setup, svc)

        for name in ("a", "b"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        # Pre-create the worktree directory for "b"
        (tmp_path / ".cadence" / "worktrees" / "b").mkdir(parents=True)

        chain = tmp_path / "chain.txt"
        chain.write_text("a\nb\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_parallel(chain, parallel=2)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "worktree path already exists" in err
        assert "/b" in err
        svc.worktree_add.assert_not_called()
        mock_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_preflight_branch_collision(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        svc = self._make_svc(tmp_path)
        svc.branch_exists.side_effect = lambda name: name == "b"
        self._make_setup_patch(mock_setup, svc)

        for name in ("a", "b"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("a\nb\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_parallel(chain, parallel=2)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "branch already exists: b" in err
        svc.worktree_add.assert_not_called()
        mock_plan.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_plan_question_aborts_worker(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.input import ParallelAbortCollector

        svc = self._make_svc(tmp_path)
        self._make_setup_patch(mock_setup, svc)

        (tmp_path / "cdc-tasks" / "alpha").mkdir(parents=True)
        (tmp_path / "cdc-tasks" / "alpha" / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\n")

        captured: dict[str, Any] = {}

        def plan_side_effect(
            *,
            config: Path | None,
            repo_path: str | None = None,
            input_collector: Any = None,
            chain_collector: Any = None,
        ) -> None:
            captured["input_collector"] = input_collector
            assert isinstance(input_collector, ParallelAbortCollector)
            input_collector.ask_question("pick one", ["x", "y"])

        mock_plan.side_effect = plan_side_effect

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_parallel(chain, parallel=2)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        # worktree was added but NOT removed (failed worktrees are kept)
        assert svc.worktree_add.call_count == 1
        svc.worktree_remove.assert_not_called()
        out = capsys.readouterr().out
        assert "[chain] alpha: failed at plan" in out
        assert "--parallel" in out

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_worker_failure_summary_uses_underlying_cause(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        svc = self._make_svc(tmp_path)
        self._make_setup_patch(mock_setup, svc)

        (tmp_path / "cdc-tasks" / "alpha").mkdir(parents=True)
        (tmp_path / "cdc-tasks" / "alpha" / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\n")

        def plan_side_effect(
            *,
            config: Path | None,
            repo_path: str | None = None,
            input_collector: Any = None,
            chain_collector: Any = None,
        ) -> None:
            try:
                raise RuntimeError("real underlying reason")
            except RuntimeError as exc:
                raise SystemExit(1) from exc

        mock_plan.side_effect = plan_side_effect

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit):
                run_chain_parallel(chain, parallel=1)
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "real underlying reason" in out
        assert not out.rstrip().endswith(": 1")

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_failure_progress_path_uses_sanitized_name_for_plan_and_task(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        raw_name = "Foo Bar"
        sanitized = "foo-bar"

        svc = self._make_svc(tmp_path)
        self._make_setup_patch(mock_setup, svc)

        (tmp_path / "cdc-tasks" / raw_name).mkdir(parents=True)
        (tmp_path / "cdc-tasks" / raw_name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text(f"{raw_name}\n")

        def plan_side_effect(
            *,
            config: Path | None,
            repo_path: str | None = None,
            input_collector: Any = None,
            chain_collector: Any = None,
        ) -> None:
            raise SystemExit(1)

        mock_plan.side_effect = plan_side_effect

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit):
                run_chain_parallel(chain, parallel=1)
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        expected = str(Path("cdc-tasks") / sanitized / "progress-plan.txt")
        assert expected in out
        assert f"cdc-tasks/{raw_name}/progress-plan.txt" not in out

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_summary_stdout_only(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        svc = self._make_svc(tmp_path)
        self._make_setup_patch(mock_setup, svc)

        for name in ("alpha", "beta"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\nbeta\n")

        # Write fake content to per-task progress files; ensure not surfaced to stdout.
        (tmp_path / "cdc-tasks" / "alpha" / "progress-task.txt").write_text(
            "PRIVATE_PROGRESS_LINE\n"
        )

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_parallel(chain, parallel=2)
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "[chain] starting 2 parallel tasks: alpha, beta" in out
        assert "[chain] alpha: started" in out
        assert "[chain] beta: started" in out
        assert "[chain] alpha: completed" in out
        assert "[chain] beta: completed" in out
        assert "[chain] complete: 2/2 succeeded" in out
        assert "PRIVATE_PROGRESS_LINE" not in out

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_workers_invoked_with_repo_path(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.input import ParallelAbortCollector

        svc = self._make_svc(tmp_path)
        self._make_setup_patch(mock_setup, svc)

        (tmp_path / "cdc-tasks" / "alpha").mkdir(parents=True)
        (tmp_path / "cdc-tasks" / "alpha" / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_parallel(chain, parallel=2)
        finally:
            os.chdir(original_cwd)

        expected_wt = str(tmp_path / ".cadence" / "worktrees" / "alpha")

        plan_kwargs = mock_plan.call_args.kwargs
        assert plan_kwargs["repo_path"] == expected_wt
        assert isinstance(plan_kwargs["input_collector"], ParallelAbortCollector)

        task_kwargs = mock_task.call_args.kwargs
        assert task_kwargs["repo_path"] == expected_wt

        squash_kwargs = mock_squash.call_args.kwargs
        assert squash_kwargs["repo_path"] == expected_wt

    def test_install_sigquit_silent_in_worker_thread(self) -> None:
        from cadence.cli import _install_sigquit

        errors: list[BaseException] = []

        def worker() -> None:
            try:
                _install_sigquit(threading.Event())
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert errors == []

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_worktree_add_failure_in_worker_reports_phase(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        svc = self._make_svc(tmp_path)
        svc.worktree_add.side_effect = RuntimeError("locked")
        self._make_setup_patch(mock_setup, svc)

        (tmp_path / "cdc-tasks" / "alpha").mkdir(parents=True)
        (tmp_path / "cdc-tasks" / "alpha" / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_parallel(chain, parallel=1)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "[chain] alpha: failed at worktree_add" in out
        assert "locked" in out
        # plan/task/squash should not have been invoked
        mock_plan.assert_not_called()
        mock_task.assert_not_called()
        mock_squash.assert_not_called()
        svc.worktree_remove.assert_not_called()

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_worktree_remove_failure_warns_but_succeeds(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        svc = self._make_svc(tmp_path)
        svc.worktree_remove.side_effect = RuntimeError("rm failed")
        self._make_setup_patch(mock_setup, svc)

        (tmp_path / "cdc-tasks" / "alpha").mkdir(parents=True)
        (tmp_path / "cdc-tasks" / "alpha" / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_parallel(chain, parallel=2)
        finally:
            os.chdir(original_cwd)

        captured = capsys.readouterr()
        assert "warn: worktree remove failed: rm failed" in captured.err
        assert "[chain] alpha: completed" in captured.out
        assert "[chain] complete: 1/1 succeeded" in captured.out

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_unexpected_worker_exception_does_not_skip_summary(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        svc = self._make_svc(tmp_path)
        svc.worktree_add.side_effect = OSError("disk full")
        self._make_setup_patch(mock_setup, svc)

        (tmp_path / "cdc-tasks" / "alpha").mkdir(parents=True)
        (tmp_path / "cdc-tasks" / "alpha" / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\n")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_chain_parallel(chain, parallel=1)
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "[chain] alpha: failed" in captured.err
        assert "disk full" in captured.err
        assert "[chain] complete: 0/1 succeeded" in captured.out

    @patch("cadence.cli.run_squash_mode")
    @patch("cadence.cli._run_task_on_current_branch")
    @patch("cadence.cli._run_plan_on_current_branch")
    @patch("cadence.cli._setup_runtime")
    def test_chain_summary_aggregates_workers(
        self,
        mock_setup: MagicMock,
        mock_plan: MagicMock,
        mock_task: MagicMock,
        mock_squash: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.executor.events import Usage
        from cadence.usage import UsageStats

        svc = self._make_svc(tmp_path)
        self._make_setup_patch(mock_setup, svc)

        for name in ("alpha", "beta"):
            (tmp_path / "cdc-tasks" / name).mkdir(parents=True)
            (tmp_path / "cdc-tasks" / name / "init").touch()

        chain = tmp_path / "chain.txt"
        chain.write_text("alpha\nbeta\n")

        def plan_se(
            *,
            config: Any,
            repo_path: str | None,
            input_collector: Any,
            chain_collector: Any,
        ) -> None:
            assert isinstance(chain_collector, UsageStats)
            s = UsageStats()
            s.add(
                Usage(
                    input_tokens=100,
                    output_tokens=50,
                    cache_read_tokens=200,
                    cache_creation_tokens=10,
                ),
                duration_ms=1000,
            )
            chain_collector.merge(s)

        mock_plan.side_effect = plan_se

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_chain_parallel(chain, parallel=2)
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "chain done in" in out
        assert "tasks 2" in out
        assert "iters 2" in out
        assert "in 200" in out
        assert "out 100" in out
        assert "cache_read 400" in out
        assert "cache_create 20" in out


class TestComputeReportPath:
    def test_basic(self) -> None:
        assert (
            compute_report_path("api-changes", branch="feat-x", tasks_root="cdc-tasks")
            == "cdc-tasks/feat-x/report-api-changes.md"
        )

    def test_sanitizes_branch(self) -> None:
        assert (
            compute_report_path("api-changes", branch="feat/foo", tasks_root="cdc-tasks")
            == "cdc-tasks/feat-foo/report-api-changes.md"
        )

    def test_empty_branch_raises(self) -> None:
        with pytest.raises(RuntimeError):
            compute_report_path("api-changes", branch="", tasks_root="cdc-tasks")

    def test_empty_report_type_raises(self) -> None:
        with pytest.raises(RuntimeError):
            compute_report_path("", branch="feat-x", tasks_root="cdc-tasks")


class TestComputeProgressPathReportMode:
    def test_returns_report_progress_path(self) -> None:
        path = compute_progress_path(
            Mode.REPORT,
            branch="feat-x",
            tasks_root="cdc-tasks",
            report_type="api-changes",
        )
        assert path == "cdc-tasks/feat-x/progress-report-api-changes.txt"

    def test_missing_report_type_raises(self) -> None:
        with pytest.raises(RuntimeError):
            compute_progress_path(Mode.REPORT, branch="feat-x", tasks_root="cdc-tasks")

    def test_missing_branch_raises(self) -> None:
        with pytest.raises(RuntimeError):
            compute_progress_path(Mode.REPORT, tasks_root="cdc-tasks", report_type="api-changes")


class TestReportApiChangesCli:
    @staticmethod
    def _runner() -> Any:
        from typer.testing import CliRunner

        return CliRunner()

    def test_help_lists_options(self) -> None:
        result = self._runner().invoke(app, ["report", "api-changes", "--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--base" in plain
        assert "--stdout-only" in plain

    def test_report_no_subcommand_shows_help(self) -> None:
        result = self._runner().invoke(app, ["report"])
        assert result.exit_code == 2

    def test_report_api_changes_shows_in_root_help(self) -> None:
        import re

        result = self._runner().invoke(app, ["--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "report" in plain

    @patch("cadence.cli.run_report_api_changes_mode")
    def test_default_call(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["report", "api-changes"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(base=None, stdout_only=False, config=None)

    @patch("cadence.cli.run_report_api_changes_mode")
    def test_with_base(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["report", "api-changes", "--base", "develop"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(base="develop", stdout_only=False, config=None)

    @patch("cadence.cli.run_report_api_changes_mode")
    def test_with_stdout_only(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["report", "api-changes", "--stdout-only"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(base=None, stdout_only=True, config=None)

    @patch("cadence.cli.run_report_api_changes_mode")
    def test_global_config_propagates(self, mock_run: MagicMock, tmp_path: Path) -> None:
        cfg = tmp_path / "override.yaml"
        cfg.write_text("default_branch: main\n")
        result = self._runner().invoke(app, ["--config", str(cfg), "report", "api-changes"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(base=None, stdout_only=False, config=cfg)


class TestRunReportApiChangesMode:
    @staticmethod
    def _setup_mock_service(
        *,
        branch: str = "feat-x",
        is_default: bool = False,
    ) -> MagicMock:
        svc = MagicMock()
        svc.current_branch.return_value = branch
        svc.is_default_branch.return_value = is_default
        return svc

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_detached_head_exits_2(
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
            run_report_api_changes_mode()
        assert excinfo.value.code == 2
        assert "detached HEAD" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_default_branch_exits_2(
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
            run_report_api_changes_mode()
        assert excinfo.value.code == 2
        assert "default branch main" in capsys.readouterr().err

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_happy_path_writes_report_path_message(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "wrote: cdc-tasks/feat-x/report-api-changes.md" in out

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["base"] == "main"
        assert kwargs["stdout_only"] is False
        assert kwargs["branch"] == "feat-x"
        assert kwargs["default_branch"] == "main"
        assert kwargs["report_path"] == os.path.join("cdc-tasks", "feat-x", "report-api-changes.md")
        assert mock_run_report.call_args.args == ("api-changes",)
        mock_log.close.assert_called_once_with(success=True)

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_stdout_only_skips_wrote_message(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode(stdout_only=True)
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "wrote:" not in out

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["stdout_only"] is True

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_base_overrides_default_branch(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode(base="develop")
        finally:
            os.chdir(original_cwd)

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["base"] == "develop"
        assert kwargs["default_branch"] == "develop"

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_runtime_error_exits_1_and_log_close_failure(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.side_effect = RuntimeError("report body not found between markers")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        mock_log.error.assert_called()
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.run_report")
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
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.side_effect = KeyboardInterrupt

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_per_task_yaml_overrides_default_branch(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "config.yaml").write_text("default_branch: parent-branch\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["base"] == "parent-branch"
        assert kwargs["default_branch"] == "parent-branch"

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_invalid_per_task_yaml_exits_1(
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
        mock_service_cls.return_value = self._setup_mock_service()

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "config.yaml").write_text("not-a-mapping\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "top-level must be a mapping" in err

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_explicit_base_overrides_per_task_yaml(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "config.yaml").write_text("default_branch: parent-branch\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode(base="staging")
        finally:
            os.chdir(original_cwd)

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["base"] == "staging"
        assert kwargs["default_branch"] == "staging"

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_uses_review_model_when_report_model_unset(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            review_model="claude-review-model",
            report_api_changes_model="",
        )
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        ctor_kwargs = mock_executor_cls.call_args.kwargs
        assert ctor_kwargs["model"] == "claude-review-model"

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_uses_report_model_when_set(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            review_model="claude-review-model",
            report_api_changes_model="claude-report-model",
        )
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        ctor_kwargs = mock_executor_cls.call_args.kwargs
        assert ctor_kwargs["model"] == "claude-report-model"

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_end_to_end_writes_file_via_real_run_report(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_detect.return_value = None
        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        body = "# API changes: feat-x vs main\n\n## Added\n- /users (abc1234)"
        executor_output = (
            f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>\n{SignalReportDone}\n"
        )
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=executor_output, signal=SignalReportDone)
        mock_executor_cls.return_value = mock_executor

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        report_file = tmp_path / "cdc-tasks" / "feat-x" / "report-api-changes.md"
        assert report_file.is_file()
        assert report_file.read_text(encoding="utf-8") == body

        out = capsys.readouterr().out
        assert body in out
        assert "wrote: cdc-tasks/feat-x/report-api-changes.md" in out
        mock_log.close.assert_called_once_with(success=True)

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_stdout_only_does_not_create_file(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_detect.return_value = None
        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        body = "# API changes: feat-x vs main\n\n## Added\n- /users (abc1234)"
        executor_output = (
            f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>\n{SignalReportDone}\n"
        )
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=executor_output, signal=SignalReportDone)
        mock_executor_cls.return_value = mock_executor

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode(stdout_only=True)
        finally:
            os.chdir(original_cwd)

        report_file = tmp_path / "cdc-tasks" / "feat-x" / "report-api-changes.md"
        assert not report_file.exists()

        out = capsys.readouterr().out
        assert body in out
        assert "wrote:" not in out

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_prompt_includes_project_context_files(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        cadence_dir = tmp_path / ".cadence"
        context_dir = cadence_dir / "context"
        context_dir.mkdir(parents=True)
        sentinel = "OPENAPI_SENTINEL_TOKEN"
        (context_dir / "openapi.yaml").write_text(f"openapi: 3.0\n# {sentinel}\n", encoding="utf-8")

        mock_detect.return_value = cadence_dir
        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        body = "# API changes: feat-x vs main"
        executor_output = (
            f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>\n{SignalReportDone}\n"
        )
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=executor_output, signal=SignalReportDone)
        mock_executor_cls.return_value = mock_executor

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        prompt = mock_executor.run.call_args.args[0]
        assert sentinel in prompt
        assert "## openapi.yaml" in prompt

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_prompt_includes_public_api_paths_when_set(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_detect.return_value = None
        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            public_api_paths=["src/api", "src/handlers"],
        )
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        body = "# API changes: feat-x vs main"
        executor_output = (
            f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>\n{SignalReportDone}\n"
        )
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=executor_output, signal=SignalReportDone)
        mock_executor_cls.return_value = mock_executor

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        prompt = mock_executor.run.call_args.args[0]
        assert "src/api" in prompt
        assert "src/handlers" in prompt

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_prompt_uses_inference_when_paths_empty(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_detect.return_value = None
        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            public_api_paths=[],
        )
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        body = "# API changes: feat-x vs main"
        executor_output = (
            f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>\n{SignalReportDone}\n"
        )
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=executor_output, signal=SignalReportDone)
        mock_executor_cls.return_value = mock_executor

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        prompt = mock_executor.run.call_args.args[0]
        assert "infer from project structure" in prompt

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_phase_summary_emitted_after_report(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config
        from cadence.executor.events import Usage

        mock_detect.return_value = None
        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            public_api_paths=["src/api"],
        )
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        body = "# API changes: feat-x vs main"
        executor_output = (
            f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>\n{SignalReportDone}\n"
        )
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output=executor_output,
            signal=SignalReportDone,
            usage=Usage(
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=200,
                cache_creation_tokens=10,
            ),
            model="claude-opus-4-7",
        )
        mock_executor_cls.return_value = mock_executor

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        printed = [
            (call.args[0] % call.args[1:]) if len(call.args) > 1 else call.args[0]
            for call in mock_log.print.call_args_list
        ]
        summary = next((p for p in printed if "phase report-api-changes done in" in p), None)
        assert summary is not None
        assert "iters 1" in summary
        assert "in 100" in summary
        assert "out 50" in summary
        assert "cost ≈ $" in summary

    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir")
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_phase_summary_suppressed_when_print_usage_false(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        mock_detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config
        from cadence.executor.events import Usage

        mock_detect.return_value = None
        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            public_api_paths=["src/api"],
            print_usage=False,
        )
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        body = "# API changes: feat-x vs main"
        executor_output = (
            f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>\n{SignalReportDone}\n"
        )
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            output=executor_output,
            signal=SignalReportDone,
            usage=Usage(
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=200,
                cache_creation_tokens=10,
            ),
            model="claude-opus-4-7",
        )
        mock_executor_cls.return_value = mock_executor

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        printed = [
            (call.args[0] % call.args[1:]) if len(call.args) > 1 else call.args[0]
            for call in mock_log.print.call_args_list
        ]
        assert not any("phase report-api-changes done in" in p for p in printed)


class TestComputeReportPathTestCases:
    def test_basic(self) -> None:
        assert (
            compute_report_path("test-cases", branch="feat-x", tasks_root="cdc-tasks")
            == "cdc-tasks/feat-x/report-test-cases.md"
        )


class TestComputeProgressPathReportTestCases:
    def test_returns_report_progress_path(self) -> None:
        path = compute_progress_path(
            Mode.REPORT,
            branch="feat-x",
            tasks_root="cdc-tasks",
            report_type="test-cases",
        )
        assert path == "cdc-tasks/feat-x/progress-report-test-cases.txt"


class TestReportTestCasesCli:
    @staticmethod
    def _runner() -> Any:
        from typer.testing import CliRunner

        return CliRunner()

    def test_help_lists_options(self) -> None:
        result = self._runner().invoke(app, ["report", "test-cases", "--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--base" in plain
        assert "--stdout-only" in plain

    @patch("cadence.cli.run_report_test_cases_mode")
    def test_default_call(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["report", "test-cases"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(base=None, stdout_only=False, config=None)

    @patch("cadence.cli.run_report_test_cases_mode")
    def test_with_base(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["report", "test-cases", "--base", "develop"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(base="develop", stdout_only=False, config=None)

    @patch("cadence.cli.run_report_test_cases_mode")
    def test_with_stdout_only(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["report", "test-cases", "--stdout-only"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(base=None, stdout_only=True, config=None)

    @patch("cadence.cli.run_report_test_cases_mode")
    def test_global_config_propagates(self, mock_run: MagicMock, tmp_path: Path) -> None:
        cfg = tmp_path / "override.yaml"
        cfg.write_text("default_branch: main\n")
        result = self._runner().invoke(app, ["--config", str(cfg), "report", "test-cases"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(base=None, stdout_only=False, config=cfg)


class TestRunReportTestCasesMode:
    @staticmethod
    def _setup_mock_service(
        *,
        branch: str = "feat-x",
        is_default: bool = False,
    ) -> MagicMock:
        svc = MagicMock()
        svc.current_branch.return_value = branch
        svc.is_default_branch.return_value = is_default
        return svc

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_detached_head_exits_2(
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
            run_report_test_cases_mode()
        assert excinfo.value.code == 2
        assert "detached HEAD" in capsys.readouterr().err

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_default_branch_exits_2(
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
            run_report_test_cases_mode()
        assert excinfo.value.code == 2
        assert "default branch main" in capsys.readouterr().err

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_happy_path_writes_report_path_message(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "wrote: cdc-tasks/feat-x/report-test-cases.md" in out

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["base"] == "main"
        assert kwargs["stdout_only"] is False
        assert kwargs["branch"] == "feat-x"
        assert kwargs["default_branch"] == "main"
        assert kwargs["report_path"] == os.path.join("cdc-tasks", "feat-x", "report-test-cases.md")
        assert mock_run_report.call_args.args == ("test-cases",)
        mock_log.close.assert_called_once_with(success=True)

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_stdout_only_skips_wrote_message(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_test_cases_mode(stdout_only=True)
        finally:
            os.chdir(original_cwd)

        out = capsys.readouterr().out
        assert "wrote:" not in out

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["stdout_only"] is True

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_base_overrides_default_branch(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_test_cases_mode(base="develop")
        finally:
            os.chdir(original_cwd)

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["base"] == "develop"
        assert kwargs["default_branch"] == "develop"

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_runtime_error_exits_1_and_log_close_failure(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.side_effect = RuntimeError("report body not found between markers")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        mock_log.error.assert_called()
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_uses_review_model_when_report_model_unset(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            review_model="claude-review-model",
            report_test_cases_model="",
        )
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        ctor_kwargs = mock_executor_cls.call_args.kwargs
        assert ctor_kwargs["model"] == "claude-review-model"

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_uses_report_model_when_set(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(
            tasks_root="cdc-tasks",
            default_branch="main",
            review_model="claude-review-model",
            report_test_cases_model="claude-tc-model",
        )
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        ctor_kwargs = mock_executor_cls.call_args.kwargs
        assert ctor_kwargs["model"] == "claude-tc-model"

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_per_task_yaml_overrides_default_branch(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "config.yaml").write_text("default_branch: parent-branch\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["base"] == "parent-branch"
        assert kwargs["default_branch"] == "parent-branch"

    @patch("cadence.cli.run_report")
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
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.side_effect = KeyboardInterrupt

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_invalid_per_task_yaml_exits_1(
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
        mock_service_cls.return_value = self._setup_mock_service()

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "config.yaml").write_text("not-a-mapping\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "top-level must be a mapping" in err

    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_explicit_base_overrides_per_task_yaml(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "config.yaml").write_text("default_branch: parent-branch\n", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_test_cases_mode(base="staging")
        finally:
            os.chdir(original_cwd)

        kwargs = mock_run_report.call_args.kwargs
        assert kwargs["base"] == "staging"
        assert kwargs["default_branch"] == "staging"


class HookRecorder:
    def __init__(self, outcomes: list[Any] | None = None) -> None:
        from cadence.hooks import HookOutcome

        self.calls: list[dict[str, Any]] = []
        self._outcomes = list(outcomes) if outcomes else []
        self._default = HookOutcome(ran=False, exit_code=0, timed_out=False)

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._outcomes:
            return self._outcomes.pop(0)
        return self._default


class TestRunPlanModeHooks:
    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_pre_and_post_called_in_order(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        task_dir = tmp_path / "cdc-tasks" / "0042-feature"
        task_dir.mkdir(parents=True)
        f = task_dir / "init"
        f.write_text("implement feature X")

        run_plan_mode(f)

        assert len(recorder.calls) == 2
        assert recorder.calls[0]["kind"] == "pre"
        assert recorder.calls[0]["phase"] == "plan"
        assert recorder.calls[1]["kind"] == "post"
        assert recorder.calls[1]["phase"] == "plan"
        mock_executor.run.assert_called_once()

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_pre_hook_failure_aborts_phase(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config
        from cadence.hooks import HookOutcome

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder(outcomes=[HookOutcome(ran=True, exit_code=7, timed_out=False)])
        mock_run_hook.side_effect = recorder

        task_dir = tmp_path / "cdc-tasks" / "0042-feature"
        task_dir.mkdir(parents=True)
        f = task_dir / "init"
        f.write_text("implement feature X")

        with pytest.raises(SystemExit) as excinfo:
            run_plan_mode(f)
        assert excinfo.value.code == 7

        mock_executor.run.assert_not_called()
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["kind"] == "pre"
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_post_hook_failure_warns_but_succeeds(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config
        from cadence.hooks import HookOutcome

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder(
            outcomes=[
                HookOutcome(ran=False, exit_code=0, timed_out=False),
                HookOutcome(ran=True, exit_code=3, timed_out=False),
            ]
        )
        mock_run_hook.side_effect = recorder

        task_dir = tmp_path / "cdc-tasks" / "0042-feature"
        task_dir.mkdir(parents=True)
        f = task_dir / "init"
        f.write_text("implement feature X")

        run_plan_mode(f)

        mock_log.warn.assert_any_call("post-%s hook exited %d", "plan", 3)
        mock_executor.run.assert_called_once()

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_env_contents(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        task_dir = tmp_path / "cdc-tasks" / "0042-feature"
        task_dir.mkdir(parents=True)
        f = task_dir / "init"
        f.write_text("implement feature X")

        run_plan_mode(f)

        pre_env = recorder.calls[0]["env"]
        assert pre_env["CADENCE_PHASE"] == "plan"
        assert pre_env["CADENCE_HOOK"] == "pre"
        assert pre_env["CADENCE_BRANCH"] == ""
        assert pre_env["CADENCE_TASK_NAME"] == "0042-feature"
        assert pre_env["CADENCE_TASKS_ROOT"] == os.path.abspath("cdc-tasks")

        post_env = recorder.calls[1]["env"]
        assert post_env["CADENCE_HOOK"] == "post"
        assert post_env["CADENCE_PHASE_RESULT"] == "success"
        assert post_env["CADENCE_PHASE_DURATION_MS"].isdigit()

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_hooks_disabled_does_not_warn_or_exit(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(
            iteration_delay_ms=0, tasks_root="cdc-tasks", hooks_enabled=False
        )

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        task_dir = tmp_path / "cdc-tasks" / "0042-feature"
        task_dir.mkdir(parents=True)
        f = task_dir / "init"
        f.write_text("implement feature X")

        run_plan_mode(f)

        for call in recorder.calls:
            assert call["enabled"] is False
        assert mock_log.warn.call_count == 0


class TestRunTaskModeHooks:
    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_pre_and_post_called(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m00s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "0042-feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalCompleted)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1: done\n\n- [x] done item\n")

        run_task_mode(f)

        assert len(recorder.calls) == 2
        assert recorder.calls[0]["kind"] == "pre"
        assert recorder.calls[0]["phase"] == "task"
        assert recorder.calls[1]["kind"] == "post"
        pre_env = recorder.calls[0]["env"]
        assert pre_env["CADENCE_BRANCH"] == "0042-feature"
        assert pre_env["CADENCE_TASK_NAME"] == "0042-feature"
        post_env = recorder.calls[1]["env"]
        assert post_env["CADENCE_PHASE_RESULT"] == "success"

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_pre_hook_failure_aborts(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config
        from cadence.hooks import HookOutcome

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "0042-feature"
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder(outcomes=[HookOutcome(ran=True, exit_code=9, timed_out=False)])
        mock_run_hook.side_effect = recorder

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1\n\n- [ ] do it\n")

        with pytest.raises(SystemExit) as excinfo:
            run_task_mode(f)
        assert excinfo.value.code == 9

        mock_executor.run.assert_not_called()
        assert len(recorder.calls) == 1
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_post_hook_failure_warns(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config
        from cadence.hooks import HookOutcome

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "1m00s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "0042-feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalCompleted)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder(
            outcomes=[
                HookOutcome(ran=False, exit_code=0, timed_out=False),
                HookOutcome(ran=True, exit_code=4, timed_out=False),
            ]
        )
        mock_run_hook.side_effect = recorder

        f = tmp_path / "plan.md"
        f.write_text("# plan\n\n### Task 1: done\n\n- [x] done item\n")

        run_task_mode(f)

        mock_log.warn.assert_any_call("post-%s hook exited %d", "task", 4)


class TestRunReviewModeHooks:
    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_pre_and_post_called(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "0042-feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalReviewDone)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner
            run_review_mode()

        assert len(recorder.calls) == 2
        assert recorder.calls[0]["kind"] == "pre"
        assert recorder.calls[0]["phase"] == "review"
        assert recorder.calls[1]["kind"] == "post"
        pre_env = recorder.calls[0]["env"]
        assert pre_env["CADENCE_BRANCH"] == "0042-feature"
        assert pre_env["CADENCE_TASK_NAME"] == "0042-feature"

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_pre_hook_failure_aborts(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config
        from cadence.hooks import HookOutcome

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "0042-feature"
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder(outcomes=[HookOutcome(ran=True, exit_code=11, timed_out=False)])
        mock_run_hook.side_effect = recorder

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner_cls.return_value = mock_runner

            with pytest.raises(SystemExit) as excinfo:
                run_review_mode()
            assert excinfo.value.code == 11

            mock_runner.run.assert_not_called()

        assert len(recorder.calls) == 1
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli._install_sigquit")
    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.TerminalCollector")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    @patch("cadence.cli.Service")
    def test_post_hook_failure_warns(
        self,
        mock_svc_cls: MagicMock,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_executor_cls: MagicMock,
        mock_terminal_cls: MagicMock,
        mock_run_hook: MagicMock,
        _sigquit: MagicMock,
        tmp_path: Path,
    ) -> None:
        from cadence.config import Config
        from cadence.hooks import HookOutcome

        mock_config.return_value = Config(iteration_delay_ms=0, tasks_root="cdc-tasks")

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress.txt")
        mock_log.elapsed.return_value = "0m30s"
        mock_logger_cls.return_value = mock_log

        mock_svc = MagicMock()
        mock_svc.current_branch.return_value = "0042-feature"
        mock_svc.diff_stats.return_value = DiffStats()
        mock_svc.root.return_value = "/repo"
        mock_svc_cls.return_value = mock_svc

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output="done", signal=SignalReviewDone)
        mock_executor_cls.return_value = mock_executor

        mock_terminal_cls.return_value = MagicMock()

        recorder = HookRecorder(
            outcomes=[
                HookOutcome(ran=False, exit_code=0, timed_out=False),
                HookOutcome(ran=True, exit_code=2, timed_out=False),
            ]
        )
        mock_run_hook.side_effect = recorder

        with patch("cadence.cli.Runner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run.return_value = True
            mock_runner_cls.return_value = mock_runner
            run_review_mode()

        mock_log.warn.assert_any_call("post-%s hook exited %d", "review", 2)


class TestRunSquashModeHooks:
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
        svc.root.return_value = "/repo"
        return svc

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_pre_and_post_called(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_hook: MagicMock,
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

        claude_output = "<<<CADENCE:COMMIT_MSG_BEGIN>>>\nmsg body\n<<<CADENCE:COMMIT_MSG_END>>>"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(output=claude_output)
        mock_executor_cls.return_value = mock_executor

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            expected_tasks_root = os.path.abspath("cdc-tasks")
            with patch("cadence.cli.display_stats"):
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        assert len(recorder.calls) == 2
        assert recorder.calls[0]["kind"] == "pre"
        assert recorder.calls[0]["phase"] == "squash"
        assert recorder.calls[1]["kind"] == "post"
        assert recorder.calls[1]["phase"] == "squash"

        pre_env = recorder.calls[0]["env"]
        assert pre_env["CADENCE_PHASE"] == "squash"
        assert pre_env["CADENCE_HOOK"] == "pre"
        assert pre_env["CADENCE_BRANCH"] == "feat-x"
        assert pre_env["CADENCE_TASK_NAME"] == "feat-x"
        assert pre_env["CADENCE_TASKS_ROOT"] == expected_tasks_root

        post_env = recorder.calls[1]["env"]
        assert post_env["CADENCE_HOOK"] == "post"
        assert post_env["CADENCE_PHASE_RESULT"] == "success"
        assert post_env["CADENCE_PHASE_DURATION_MS"].isdigit()

        mock_svc.squash_commits.assert_called_once()

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_pre_hook_failure_aborts(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config
        from cadence.hooks import HookOutcome

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_svc = self._setup_mock_service()
        mock_service_cls.return_value = mock_svc

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-squash.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor = MagicMock()
        mock_executor_cls.return_value = mock_executor

        recorder = HookRecorder(outcomes=[HookOutcome(ran=True, exit_code=13, timed_out=False)])
        mock_run_hook.side_effect = recorder

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

        assert excinfo.value.code == 13
        mock_executor.run.assert_not_called()
        mock_svc.squash_commits.assert_not_called()
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["kind"] == "pre"
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_post_hook_failure_warns_but_squash_preserved(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config
        from cadence.hooks import HookOutcome

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

        recorder = HookRecorder(
            outcomes=[
                HookOutcome(ran=False, exit_code=0, timed_out=False),
                HookOutcome(ran=True, exit_code=6, timed_out=False),
            ]
        )
        mock_run_hook.side_effect = recorder

        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with patch("cadence.cli.display_stats"):
                run_squash_mode()
        finally:
            os.chdir(original_cwd)

        mock_svc.squash_commits.assert_called_once()
        mock_log.warn.assert_any_call("post-%s hook exited %d", "squash", 6)
        mock_log.close.assert_called_once_with(success=True)

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    def test_validation_failures_skip_hook(
        self,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        # detached HEAD
        mock_service_cls.return_value = self._setup_mock_service(branch="")
        with pytest.raises(SystemExit) as excinfo:
            run_squash_mode()
        assert excinfo.value.code == 2

        # default branch
        mock_service_cls.return_value = self._setup_mock_service(branch="main", is_default=True)
        with pytest.raises(SystemExit) as excinfo:
            run_squash_mode()
        assert excinfo.value.code == 2

        # missing task dir
        mock_service_cls.return_value = self._setup_mock_service()
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)
        assert excinfo.value.code == 2

        # plan not completed
        task_dir = tmp_path / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)
        assert excinfo.value.code == 2

        # dirty tree
        (task_dir / "plan-completed").write_text("done", encoding="utf-8")
        mock_service_cls.return_value = self._setup_mock_service(is_dirty=True)
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)
        assert excinfo.value.code == 2

        # zero commits ahead
        mock_service_cls.return_value = self._setup_mock_service(commits_ahead=0)
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_squash_mode()
        finally:
            os.chdir(original_cwd)
        assert excinfo.value.code == 2

        # single commit ahead — early return, no SystemExit
        mock_service_cls.return_value = self._setup_mock_service(commits_ahead=1)
        os.chdir(tmp_path)
        try:
            run_squash_mode()
        finally:
            os.chdir(original_cwd)

        # No hook call recorded across any of these validation failures
        assert recorder.calls == []


class TestRunReportApiChangesModeHooks:
    @staticmethod
    def _setup_mock_service(
        *,
        branch: str = "feat-x",
        is_default: bool = False,
    ) -> MagicMock:
        svc = MagicMock()
        svc.current_branch.return_value = branch
        svc.is_default_branch.return_value = is_default
        svc.root.return_value = "/repo"
        return svc

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_pre_and_post_called(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            expected_tasks_root = os.path.abspath("cdc-tasks")
            run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        assert len(recorder.calls) == 2
        assert recorder.calls[0]["kind"] == "pre"
        assert recorder.calls[0]["phase"] == "report"
        assert recorder.calls[1]["kind"] == "post"
        assert recorder.calls[1]["phase"] == "report"

        pre_env = recorder.calls[0]["env"]
        assert pre_env["CADENCE_PHASE"] == "report"
        assert pre_env["CADENCE_REPORT_TYPE"] == "api-changes"
        assert pre_env["CADENCE_BRANCH"] == "feat-x"
        assert pre_env["CADENCE_TASK_NAME"] == "feat-x"
        assert pre_env["CADENCE_TASKS_ROOT"] == expected_tasks_root

        post_env = recorder.calls[1]["env"]
        assert post_env["CADENCE_REPORT_TYPE"] == "api-changes"
        assert post_env["CADENCE_PHASE_RESULT"] == "success"
        assert post_env["CADENCE_PHASE_DURATION_MS"].isdigit()

        mock_run_report.assert_called_once()

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_pre_hook_failure_aborts(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config
        from cadence.hooks import HookOutcome

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()

        recorder = HookRecorder(outcomes=[HookOutcome(ran=True, exit_code=15, timed_out=False)])
        mock_run_hook.side_effect = recorder

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 15
        mock_run_report.assert_not_called()
        assert len(recorder.calls) == 1
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_post_hook_records_failure_when_run_report_raises(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-api-changes.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.side_effect = RuntimeError("report failed")

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_report_api_changes_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert len(recorder.calls) == 2
        post_env = recorder.calls[1]["env"]
        assert post_env["CADENCE_PHASE_RESULT"] == "failure"


class TestRunReportTestCasesModeHooks:
    @staticmethod
    def _setup_mock_service(
        *,
        branch: str = "feat-x",
        is_default: bool = False,
    ) -> MagicMock:
        svc = MagicMock()
        svc.current_branch.return_value = branch
        svc.is_default_branch.return_value = is_default
        svc.root.return_value = "/repo"
        return svc

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_pre_and_post_called_with_test_cases_report_type(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.return_value = True

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        assert len(recorder.calls) == 2
        pre_env = recorder.calls[0]["env"]
        assert pre_env["CADENCE_PHASE"] == "report"
        assert pre_env["CADENCE_REPORT_TYPE"] == "test-cases"
        post_env = recorder.calls[1]["env"]
        assert post_env["CADENCE_REPORT_TYPE"] == "test-cases"
        assert post_env["CADENCE_PHASE_RESULT"] == "success"

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_pre_hook_failure_aborts(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config
        from cadence.hooks import HookOutcome

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()

        recorder = HookRecorder(outcomes=[HookOutcome(ran=True, exit_code=21, timed_out=False)])
        mock_run_hook.side_effect = recorder

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 21
        mock_run_report.assert_not_called()
        mock_log.close.assert_called_once_with(success=False)

    @patch("cadence.cli.run_hook")
    @patch("cadence.cli.run_report")
    @patch("cadence.cli.ClaudeExecutor")
    @patch("cadence.cli.Service")
    @patch("cadence.cli.load_config")
    @patch("cadence.cli.detect_local_dir", return_value=None)
    @patch("cadence.cli.check_claude_dep")
    @patch("cadence.cli.Logger")
    def test_post_hook_records_failure_when_run_report_raises(
        self,
        mock_logger_cls: MagicMock,
        _check: MagicMock,
        _detect: MagicMock,
        mock_config: MagicMock,
        mock_service_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_run_report: MagicMock,
        mock_run_hook: MagicMock,
        tmp_path: Path,
    ) -> None:
        import os

        from cadence.config import Config

        mock_config.return_value = Config(tasks_root="cdc-tasks", default_branch="main")
        mock_service_cls.return_value = self._setup_mock_service()

        mock_log = MagicMock()
        mock_log.path = str(tmp_path / "progress-report-test-cases.txt")
        mock_log.elapsed.return_value = "0m05s"
        mock_logger_cls.return_value = mock_log

        mock_executor_cls.return_value = MagicMock()
        mock_run_report.side_effect = RuntimeError("report failed")

        recorder = HookRecorder()
        mock_run_hook.side_effect = recorder

        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(SystemExit) as excinfo:
                run_report_test_cases_mode()
        finally:
            os.chdir(original_cwd)

        assert excinfo.value.code == 1
        assert len(recorder.calls) == 2
        post_env = recorder.calls[1]["env"]
        assert post_env["CADENCE_PHASE_RESULT"] == "failure"
        assert post_env["CADENCE_REPORT_TYPE"] == "test-cases"


class TestStatusCli:
    @staticmethod
    def _runner() -> Any:
        from typer.testing import CliRunner

        return CliRunner()

    def test_help_lists_status(self) -> None:
        result = self._runner().invoke(app, ["status", "--help"])
        assert result.exit_code == 0
        assert "Show the status of cadence tasks" in result.output

    @patch("cadence.cli.run_status_mode")
    def test_status_no_flags(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["status"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(current_only=False, json_output=False, config=None)

    @patch("cadence.cli.run_status_mode")
    def test_status_current_flag(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["status", "--current"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(current_only=True, json_output=False, config=None)

    @patch("cadence.cli.run_status_mode")
    def test_status_json_flag(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(current_only=False, json_output=True, config=None)

    @patch("cadence.cli.run_status_mode")
    def test_status_global_config_propagates(self, mock_run: MagicMock, tmp_path: Path) -> None:
        cfg = tmp_path / "override.yaml"
        cfg.write_text("default_branch: main\n")
        result = self._runner().invoke(app, ["--config", str(cfg), "status"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(current_only=False, json_output=False, config=cfg)


class TestRunStatusMode:
    @staticmethod
    def _init_repo(repo: Path, *, branch: str = "main") -> None:
        import subprocess

        subprocess.run(["git", "init", "-q", "-b", branch, str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            check=True,
        )
        readme = repo / "README.md"
        readme.write_text("hello\n")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)

    @staticmethod
    def _checkout_branch(repo: Path, branch: str) -> None:
        import subprocess

        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True)

    def test_not_a_git_repo(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            run_status_mode(current_only=False, json_output=False)
        assert excinfo.value.code == 2
        assert "error:" in capsys.readouterr().err

    def test_happy_path_init_only(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        self._checkout_branch(repo, "feat-x")
        task_dir = repo / "cdc-tasks" / "feat-x"
        task_dir.mkdir(parents=True)
        (task_dir / "init").write_text("scaffold")

        monkeypatch.chdir(repo)
        run_status_mode(current_only=False, json_output=False)

        out = capsys.readouterr().out
        assert "current branch: feat-x" in out
        assert re.search(r"^\s*state\s+init only", out, re.MULTILINE)
        assert re.search(r"^\s*last commit\s+\S+\s+\(.+\)\s+\"initial\"", out, re.MULTILINE)

    def test_json_output_parses(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import json as _json

        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        self._checkout_branch(repo, "feat-y")
        task_dir = repo / "cdc-tasks" / "feat-y"
        task_dir.mkdir(parents=True)
        (task_dir / "init").write_text("scaffold")

        monkeypatch.chdir(repo)
        run_status_mode(current_only=False, json_output=True)

        out = capsys.readouterr().out
        payload = _json.loads(out)
        assert "current" in payload
        assert "tasks" in payload
        assert payload["current"]["branch"] == "feat-y"
        assert payload["current"]["state"] == "init only"
        assert payload["tasks"] == []

    def test_current_only_skips_others(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        self._checkout_branch(repo, "feat-current")
        cur_dir = repo / "cdc-tasks" / "feat-current"
        cur_dir.mkdir(parents=True)
        (cur_dir / "init").write_text("x")
        other_dir = repo / "cdc-tasks" / "feat-other"
        other_dir.mkdir(parents=True)
        (other_dir / "init").write_text("y")

        monkeypatch.chdir(repo)
        run_status_mode(current_only=True, json_output=False)

        out = capsys.readouterr().out
        assert "feat-current" in out
        assert "feat-other" not in out
        assert "other tasks under" not in out

    def test_current_branch_excluded_from_others(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        self._checkout_branch(repo, "feat-current")
        cur_dir = repo / "cdc-tasks" / "feat-current"
        cur_dir.mkdir(parents=True)
        (cur_dir / "init").write_text("x")
        other_dir = repo / "cdc-tasks" / "feat-other"
        other_dir.mkdir(parents=True)
        (other_dir / "init").write_text("y")

        monkeypatch.chdir(repo)
        run_status_mode(current_only=False, json_output=True)

        import json as _json

        out = capsys.readouterr().out
        payload = _json.loads(out)
        names = [t["name"] for t in payload["tasks"]]
        assert names == ["feat-other"]
        assert payload["current"]["branch"] == "feat-current"

    def test_detached_head_no_current_section(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        # add a second commit so we have something to detach to
        (repo / "extra.txt").write_text("e\n")
        subprocess.run(["git", "-C", str(repo), "add", "extra.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "second"], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "HEAD~1"], check=True)

        task_dir = repo / "cdc-tasks" / "feat-z"
        task_dir.mkdir(parents=True)
        (task_dir / "init").write_text("z")

        monkeypatch.chdir(repo)
        run_status_mode(current_only=False, json_output=False)

        out = capsys.readouterr().out
        assert "current branch:" not in out
        assert "feat-z" in out
        assert "init only" in out

    def test_detached_head_no_tasks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "extra.txt").write_text("e\n")
        subprocess.run(["git", "-C", str(repo), "add", "extra.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "second"], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "HEAD~1"], check=True)

        monkeypatch.chdir(repo)
        run_status_mode(current_only=False, json_output=False)

        out = capsys.readouterr().out
        assert "no tasks under cdc-tasks/" in out

    def test_branch_with_no_task_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        self._checkout_branch(repo, "feat-no-dir")

        monkeypatch.chdir(repo)
        run_status_mode(current_only=False, json_output=False)

        out = capsys.readouterr().out
        assert "current branch: feat-no-dir" in out
        assert "no task dir under cdc-tasks/" in out

    def test_current_only_detached_head_emits_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "extra.txt").write_text("e\n")
        subprocess.run(["git", "-C", str(repo), "add", "extra.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "second"], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "HEAD~1"], check=True)

        monkeypatch.chdir(repo)
        run_status_mode(current_only=True, json_output=False)

        out = capsys.readouterr().out
        assert out.strip() != ""
        assert "no current cadence task" in out


class TestDoctorCli:
    @staticmethod
    def _runner() -> Any:
        from typer.testing import CliRunner

        return CliRunner()

    @staticmethod
    def _init_repo(repo: Path) -> None:
        import subprocess

        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            check=True,
        )
        readme = repo / "README.md"
        readme.write_text("hello\n")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)

    def test_help_lists_doctor(self) -> None:
        result = self._runner().invoke(app, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "pre-flight" in result.output

    def test_help_top_level_lists_doctor(self) -> None:
        result = self._runner().invoke(app, ["--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "doctor" in plain

    @patch("cadence.cli.run_doctor_mode")
    def test_doctor_calls_run_doctor_mode(self, mock_run: MagicMock) -> None:
        result = self._runner().invoke(app, ["doctor"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(config=None)

    @patch("cadence.cli.run_doctor_mode")
    def test_global_config_propagates_to_doctor(self, mock_run: MagicMock, tmp_path: Path) -> None:
        cfg = tmp_path / "override.yaml"
        cfg.write_text("default_branch: main\n")
        result = self._runner().invoke(app, ["--config", str(cfg), "doctor"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(config=cfg)

    @staticmethod
    def _stub_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess as _subprocess

        def fake_which(name: str, *args: Any, **kwargs: Any) -> str | None:
            if name == "claude":
                return "/fake/claude"
            if name == "git":
                return "/fake/git"
            return None

        def fake_run(*args: Any, **kwargs: Any) -> _subprocess.CompletedProcess[str]:
            argv = args[0]
            return _subprocess.CompletedProcess(argv, 0, stdout="x 1.0\n", stderr="")

        monkeypatch.setattr("cadence.diagnostics.doctor.shutil.which", fake_which)
        monkeypatch.setattr("cadence.diagnostics.doctor.subprocess.run", fake_run)

    def test_happy_path_exits_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        local = repo / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("default_branch: main\n")
        monkeypatch.chdir(repo)
        self._stub_binaries(monkeypatch)

        result = self._runner().invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "environment" in result.output
        assert "repository" in result.output
        assert "config" in result.output

    def test_failure_when_claude_missing_exits_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil as _shutil

        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        monkeypatch.chdir(repo)

        real_which = _shutil.which

        def fake_which(name: str, *args: Any, **kwargs: Any) -> str | None:
            if name == "claude":
                return None
            return real_which(name, *args, **kwargs)

        monkeypatch.setattr("cadence.diagnostics.doctor.shutil.which", fake_which)

        result = self._runner().invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "✗" in result.output

    def test_unknown_key_in_local_config_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        local = repo / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("not_a_real_field: 1\n")
        monkeypatch.chdir(repo)
        self._stub_binaries(monkeypatch)

        result = self._runner().invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "not_a_real_field" in result.output
        assert "unknown config key" in result.output

    def test_malformed_local_config_reports_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        local = repo / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("foo: [unclosed\n")
        monkeypatch.chdir(repo)
        self._stub_binaries(monkeypatch)

        result = self._runner().invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "Traceback" not in result.output
        assert "invalid YAML" in result.output


class TestBuildLoggerProgressJsonl:
    def _invoke(self, tmp_path: Path, *, progress_jsonl: bool) -> Any:
        from cadence.config import ColorConfig
        from cadence.progress.colors import Colors
        from cadence.status import Mode, PhaseHolder

        progress_path = str(tmp_path / "progress-plan.txt")
        with patch("cadence.cli.Logger") as mock_logger_cls:
            _build_logger(
                progress_path,
                "plan-file",
                "desc",
                Mode.PLAN,
                "branch-x",
                Colors(ColorConfig()),
                PhaseHolder(),
                progress_jsonl=progress_jsonl,
            )
        assert mock_logger_cls.call_count == 1
        return mock_logger_cls.call_args.args[0]

    def test_progress_jsonl_true_propagates(self, tmp_path: Path) -> None:
        cfg = self._invoke(tmp_path, progress_jsonl=True)
        assert cfg.progress_jsonl is True

    def test_progress_jsonl_false_propagates(self, tmp_path: Path) -> None:
        cfg = self._invoke(tmp_path, progress_jsonl=False)
        assert cfg.progress_jsonl is False

    def test_progress_jsonl_default_is_false(self, tmp_path: Path) -> None:
        from cadence.config import ColorConfig
        from cadence.progress.colors import Colors
        from cadence.status import Mode, PhaseHolder

        progress_path = str(tmp_path / "progress-plan.txt")
        with patch("cadence.cli.Logger") as mock_logger_cls:
            _build_logger(
                progress_path,
                "plan-file",
                "desc",
                Mode.PLAN,
                "branch-x",
                Colors(ColorConfig()),
                PhaseHolder(),
            )
        cfg = mock_logger_cls.call_args.args[0]
        assert cfg.progress_jsonl is False

    def test_yaml_flag_flows_through_run_plan_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cadence.config import Config

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("implement feature X")

        cfg = Config()
        cfg.progress_jsonl = True

        captured: dict[str, Any] = {}

        def fake_build_logger(*args: Any, **kwargs: Any) -> Any:
            captured["progress_jsonl"] = kwargs.get("progress_jsonl")
            raise SystemExit(0)

        with (
            patch("cadence.cli._setup_runtime") as setup,
            patch("cadence.cli.compute_progress_path", return_value=str(tmp_path / "progress.txt")),
            patch("cadence.cli._build_logger", side_effect=fake_build_logger),
        ):
            setup.return_value = (
                cfg,
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                "main",
                None,
            )
            with pytest.raises(SystemExit):
                run_plan_mode(plan_file)

        assert captured["progress_jsonl"] is True
