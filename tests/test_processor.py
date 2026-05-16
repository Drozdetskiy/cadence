from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from cadence.config import Config as AppConfig
from cadence.executor.claude_executor import (
    LimitPatternError,
    PatternMatchError,
    Result,
)
from cadence.executor.events import Usage
from cadence.processor.prompts import (
    append_commit_trailer_instruction,
    build_plan_prompt,
    load_prompt,
    normalize_crlf,
    replace_base_variables,
)
from cadence.processor.runner import (
    Dependencies,
    RunContext,
    Runner,
    UserAbortedError,
)
from cadence.progress.events import (
    ErrorEvent,
    IterationEndEvent,
    IterationStartEvent,
    PhaseEndEvent,
    PhaseStartEvent,
    SignalEvent,
)
from cadence.status import (
    Mode,
    PhaseHolder,
    SignalCompleted,
    SignalFailed,
    SignalPlanReady,
    SignalReviewDone,
    SignalReviewSecondDone,
)
from cadence.usage import UsageStats


class TestNormalizeCrlf:
    def test_crlf(self) -> None:
        assert normalize_crlf("a\r\nb") == "a\nb"

    def test_cr(self) -> None:
        assert normalize_crlf("a\rb") == "a\nb"

    def test_lf_unchanged(self) -> None:
        assert normalize_crlf("a\nb") == "a\nb"

    def test_mixed(self) -> None:
        assert normalize_crlf("a\r\nb\rc\nd") == "a\nb\nc\nd"


class TestLoadPrompt:
    def test_loads_embedded_make_plan(self) -> None:
        prompt = load_prompt("make_plan")
        assert "{{PLAN_DESCRIPTION}}" in prompt
        assert "<<<CADENCE:QUESTION>>>" in prompt

    def test_local_override(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        local_file = prompts_dir / "make_plan.txt"
        local_file.write_text("local override content")
        result = load_prompt("make_plan", local_dir=tmp_path)
        assert result == "local override content"

    def test_fallback_to_embedded(self, tmp_path: Path) -> None:
        result = load_prompt("make_plan", local_dir=tmp_path)
        assert "{{PLAN_DESCRIPTION}}" in result

    def test_normalizes_crlf(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        local_file = prompts_dir / "make_plan.txt"
        local_file.write_bytes(b"line1\r\nline2\r\n")
        result = load_prompt("make_plan", local_dir=tmp_path)
        assert "\r" not in result
        assert result == "line1\nline2\n"


class TestReplaceBaseVariables:
    def test_replaces_all_variables(self) -> None:
        prompt = "{{PLAN_FILE}} {{PROGRESS_FILE}} {{GOAL}} {{DEFAULT_BRANCH}}"
        result = replace_base_variables(
            prompt,
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            goal="implement feature X",
            default_branch="main",
        )
        assert "/tmp/plan.md" in result
        assert "/tmp/progress.txt" in result
        assert "implement feature X" in result
        assert "main" in result

    def test_fallback_values(self) -> None:
        prompt = "{{PLAN_FILE}} {{PROGRESS_FILE}} {{GOAL}} {{DEFAULT_BRANCH}}"
        result = replace_base_variables(prompt)
        assert "(no plan file" in result
        assert "(no progress file" in result
        assert "current branch vs main" in result
        assert "main" in result


class TestAppendCommitTrailer:
    def test_appends_trailer(self) -> None:
        result = append_commit_trailer_instruction("prompt", "Signed-off-by: Bot")
        assert "Signed-off-by: Bot" in result
        assert "trailer" in result.lower()

    def test_empty_trailer_unchanged(self) -> None:
        assert append_commit_trailer_instruction("prompt", "") == "prompt"


class TestBuildPlanPrompt:
    def test_substitutes_plan_description(self) -> None:
        result = build_plan_prompt(
            "Add caching layer",
            progress_file="/tmp/prog.txt",
            default_branch="main",
        )
        assert "Add caching layer" in result
        assert "{{PLAN_DESCRIPTION}}" not in result

    def test_applies_base_variables(self) -> None:
        result = build_plan_prompt(
            "desc",
            progress_file="/tmp/prog.txt",
        )
        assert "/tmp/prog.txt" in result

    def test_appends_commit_trailer(self) -> None:
        result = build_plan_prompt(
            "desc",
            commit_trailer="Co-authored-by: Bot",
        )
        assert "Co-authored-by: Bot" in result

    def test_local_override(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "make_plan.txt").write_text("Custom: {{PLAN_DESCRIPTION}}")
        result = build_plan_prompt("my feature", local_dir=tmp_path)
        assert result == "Custom: my feature"

    def test_substitutes_derived_plan_path(self) -> None:
        result = build_plan_prompt(
            "desc",
            derived_plan_path="tasks/0008-settings/plan",
        )
        assert "tasks/0008-settings/plan" in result
        assert "{{DERIVED_PLAN_PATH}}" not in result

    def test_derived_plan_path_fallback_when_empty(self) -> None:
        result = build_plan_prompt("desc")
        assert "{{DERIVED_PLAN_PATH}}" not in result
        assert "(next to the prompt file)" in result


def _make_runner(
    executor: object,
    *,
    plan_description: str = "test desc",
    max_iterations: int = 50,
    app_cfg: AppConfig | None = None,
) -> tuple[Runner, MagicMock, MagicMock]:
    ctx = RunContext(
        mode=Mode.PLAN,
        plan_description=plan_description,
    )
    log = MagicMock()
    log.path = "/tmp/progress.txt"
    holder = PhaseHolder()
    input_mock = MagicMock()
    deps = Dependencies(
        executor=executor,  # type: ignore[arg-type]
        input_collector=input_mock,
        logger=log,
        holder=holder,
    )
    cfg = app_cfg or AppConfig(
        max_iterations=max_iterations,
        iteration_delay_ms=0,
    )
    runner = Runner(ctx, cfg, deps)
    return runner, log, input_mock


class TestRunnerPlanCreationQuestionFlow:
    def test_question_then_plan_ready(self) -> None:
        executor = MagicMock()
        q_output = (
            "<<<CADENCE:QUESTION>>>\n"
            '{"question": "Which DB?", '
            '"options": ["Postgres", "SQLite"]}\n'
            "<<<CADENCE:END>>>"
        )
        executor.run.side_effect = [
            Result(output=q_output, signal=""),
            Result(output="done", signal=SignalPlanReady),
        ]
        runner, log, input_mock = _make_runner(executor)
        input_mock.ask_question.return_value = "Postgres"

        runner.run_plan_creation()

        input_mock.ask_question.assert_called_once()
        log.log_question.assert_called_once()
        log.log_answer.assert_called_once_with("Postgres")
        assert executor.run.call_count == 2


class TestRunnerPlanCreationDraftFlow:
    def test_draft_auto_accepted_then_plan_ready(self) -> None:
        executor = MagicMock()
        draft_output = (
            "<<<CADENCE:PLAN_DRAFT>>>\n"
            "# My Plan\n"
            "## Overview\n"
            "Implementation details\n"
            "<<<CADENCE:END>>>"
        )
        executor.run.side_effect = [
            Result(output=draft_output, signal=""),
            Result(output="written", signal=SignalPlanReady),
        ]
        runner, log, _input_mock = _make_runner(executor)

        runner.run_plan_creation()

        log.print.assert_any_call("draft received, auto-accepting")
        assert executor.run.call_count == 2

    def test_draft_auto_accept_no_feedback_in_next_prompt(self) -> None:
        executor = MagicMock()
        draft = "<<<CADENCE:PLAN_DRAFT>>>\nDraft 1\n<<<CADENCE:END>>>"
        executor.run.side_effect = [
            Result(output=draft, signal=""),
            Result(output="done", signal=SignalPlanReady),
        ]
        runner, _log, _input_mock = _make_runner(executor)

        runner.run_plan_creation()

        calls = executor.run.call_args_list
        assert "PREVIOUS DRAFT FEEDBACK" not in calls[1][0][0]


class TestRunnerPlanCreationErrors:
    def test_failed_signal_raises(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalFailed)
        runner, _log, _input_mock = _make_runner(executor)

        with pytest.raises(RuntimeError, match="plan creation failed"):
            runner.run_plan_creation()

    def test_pattern_match_error_handled(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal="",
            error=PatternMatchError("API Error:", "claude /usage"),
        )
        runner, log, _input_mock = _make_runner(executor)

        runner.run_plan_creation()

        log.error.assert_called()

    def test_limit_pattern_with_wait_retries(self) -> None:
        executor = MagicMock()
        limit_err = LimitPatternError("You've hit your limit", "claude /usage")
        executor.run.side_effect = [
            Result(output="", signal="", error=limit_err),
            Result(output="ok", signal=SignalPlanReady),
        ]
        app = AppConfig(
            wait_on_limit="1s",
            max_iterations=50,
            iteration_delay_ms=0,
        )
        runner, _log, _input_mock = _make_runner(executor, plan_description="test", app_cfg=app)

        start = time.monotonic()
        runner.run_plan_creation()
        elapsed = time.monotonic() - start

        assert executor.run.call_count == 2
        assert elapsed >= 0.5

    def test_max_iterations_warns(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="no signals", signal="")
        runner, log, _input_mock = _make_runner(executor, max_iterations=5)

        runner.run_plan_creation()

        log.warn.assert_called()
        assert executor.run.call_count == 5


class TestRunnerPlanCreationIdleTimeout:
    def test_idle_timeout_continues(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal="", idle_timed_out=True),
            Result(output="done", signal=SignalPlanReady),
        ]
        runner, _log, _input_mock = _make_runner(executor)

        runner.run_plan_creation()

        assert executor.run.call_count == 2


class TestRunnerPlanCreationImportedBrief:
    def _make_runner_with_ctx(
        self,
        executor: object,
        ctx: RunContext,
    ) -> Runner:
        log = MagicMock()
        log.path = "/tmp/progress.txt"
        holder = PhaseHolder()
        deps = Dependencies(
            executor=executor,  # type: ignore[arg-type]
            input_collector=MagicMock(),
            logger=log,
            holder=holder,
        )
        cfg = AppConfig(max_iterations=50, iteration_delay_ms=0)
        return Runner(ctx, cfg, deps)

    def test_no_imported_brief_omits_external_section(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        ctx = RunContext(mode=Mode.PLAN, plan_description="init body")
        runner = self._make_runner_with_ctx(executor, ctx)

        runner.run_plan_creation()

        prompt = executor.run.call_args_list[0][0][0]
        assert "# External brief" not in prompt
        assert "init body" in prompt

    def test_imported_brief_included_with_source_path(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="done", signal=SignalPlanReady)
        ctx = RunContext(
            mode=Mode.PLAN,
            plan_description="",
            imported_brief="external content here",
            imported_brief_source="/abs/path/to/brief.md",
        )
        runner = self._make_runner_with_ctx(executor, ctx)

        runner.run_plan_creation()

        prompt = executor.run.call_args_list[0][0][0]
        assert "# External brief (imported from /abs/path/to/brief.md)" in prompt
        assert "external content here" in prompt


def _make_resolver_runner(plan_file: str) -> Runner:
    ctx = RunContext(mode=Mode.REVIEW, plan_file=plan_file)
    log = MagicMock()
    log.path = "/tmp/progress.txt"
    holder = PhaseHolder()
    deps = Dependencies(
        executor=MagicMock(),
        input_collector=MagicMock(),
        logger=log,
        holder=holder,
    )
    cfg = AppConfig(max_iterations=1, iteration_delay_ms=0)
    return Runner(ctx, cfg, deps)


class TestResolvePlanFilePath:
    def test_returns_empty_when_no_plan_file(self) -> None:
        runner = _make_resolver_runner("")
        assert runner.resolve_plan_file_path() == ""

    def test_returns_original_when_exists(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("# plan\n")
        runner = _make_resolver_runner(str(plan))
        assert runner.resolve_plan_file_path() == str(plan)

    def test_falls_back_to_completed_sibling_with_extension(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        completed = tmp_path / "plan-completed.md"
        completed.write_text("# done\n")
        runner = _make_resolver_runner(str(plan))
        assert runner.resolve_plan_file_path() == str(completed)

    def test_falls_back_to_completed_sibling_no_extension(self, tmp_path: Path) -> None:
        plan = tmp_path / "preprompt"
        completed = tmp_path / "preprompt-completed"
        completed.write_text("# done\n")
        runner = _make_resolver_runner(str(plan))
        assert runner.resolve_plan_file_path() == str(completed)

    def test_returns_original_when_neither_exists(self, tmp_path: Path) -> None:
        plan = tmp_path / "missing.md"
        runner = _make_resolver_runner(str(plan))
        assert runner.resolve_plan_file_path() == str(plan)


def _make_task_runner(
    executor: object,
    *,
    plan_file: str = "",
    max_iterations: int = 10,
    task_retry_count: int = 1,
    app_cfg: AppConfig | None = None,
    local_dir: Path | None = None,
) -> tuple[Runner, MagicMock, MagicMock]:
    ctx = RunContext(
        mode=Mode.FULL,
        plan_file=plan_file,
        local_dir=local_dir,
    )
    log = MagicMock()
    log.path = "/tmp/progress.txt"
    holder = PhaseHolder()
    input_mock = MagicMock()
    deps = Dependencies(
        executor=executor,  # type: ignore[arg-type]
        input_collector=input_mock,
        logger=log,
        holder=holder,
    )
    cfg = app_cfg or AppConfig(
        max_iterations=max_iterations,
        iteration_delay_ms=0,
        task_retry_count=task_retry_count,
    )
    runner = Runner(ctx, cfg, deps)
    return runner, log, input_mock


def _write_plan(tmp_path: Path, content: str, name: str = "plan.md") -> Path:
    f = tmp_path / name
    f.write_text(content)
    return f


_PLAN_DONE = "# Plan\n### Task 1: A\n- [x] one\n"
_PLAN_PENDING = "# Plan\n### Task 1: A\n- [ ] one\n"
_PLAN_PENDING_TASK_2 = "# Plan\n### Task 1: A\n- [x] one\n### Task 2: B\n- [ ] two\n"


class TestRunTaskPhaseCompletion:
    def test_completed_signal_returns_true(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalCompleted)
        runner, log, _ = _make_task_runner(executor, plan_file=str(plan))

        result = runner.run_task_phase()

        assert result is True
        assert executor.run.call_count == 1
        log.print.assert_any_call("all tasks done")

    def test_completed_with_uncompleted_warns_then_returns_true(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()

        # First COMPLETED signal but plan still has [ ] -> warn and continue.
        # Second iteration: the plan still has [ ] so we'd loop again. To keep
        # the test focused we make the second call return COMPLETED with the
        # plan having been replaced with an all-done plan.
        call_count = {"n": 0}

        def fake_run(prompt: str) -> Result:
            call_count["n"] += 1
            if call_count["n"] == 2:
                plan.write_text(_PLAN_DONE)
            return Result(output="", signal=SignalCompleted)

        executor.run.side_effect = fake_run
        runner, log, _ = _make_task_runner(executor, plan_file=str(plan), max_iterations=5)

        result = runner.run_task_phase()

        assert result is True
        log.warn.assert_any_call("COMPLETED signal received but uncompleted tasks remain")

    def test_failed_signal_retries_then_raises(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalFailed)
        runner, log, _ = _make_task_runner(
            executor,
            plan_file=str(plan),
            max_iterations=10,
            task_retry_count=2,
        )

        with pytest.raises(RuntimeError, match="task execution failed"):
            runner.run_task_phase()

        # initial + 2 retries = 3 calls
        assert executor.run.call_count == 3
        log.error.assert_called()

    def test_max_iterations_returns_false(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        executor.run.return_value = Result(output="no signal", signal="")
        runner, log, _ = _make_task_runner(executor, plan_file=str(plan), max_iterations=3)

        result = runner.run_task_phase()

        assert result is False
        assert executor.run.call_count == 3
        log.warn.assert_any_call("max iterations reached")

    def test_pattern_match_error_returns_false(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal="",
            error=PatternMatchError("API Error:", "claude /usage"),
        )
        runner, log, _ = _make_task_runner(executor, plan_file=str(plan))

        result = runner.run_task_phase()

        assert result is False
        log.error.assert_called()

    def test_unexpected_error_raised(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        boom = RuntimeError("boom")
        executor.run.return_value = Result(output="", signal="", error=boom)
        runner, _log, _ = _make_task_runner(executor, plan_file=str(plan))

        with pytest.raises(RuntimeError, match="boom"):
            runner.run_task_phase()


class TestHasUncompletedTasks:
    def test_pending_returns_true(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        runner, _log, _ = _make_task_runner(MagicMock(), plan_file=str(plan))
        assert runner.has_uncompleted_tasks() is True

    def test_all_done_returns_false(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        runner, _log, _ = _make_task_runner(MagicMock(), plan_file=str(plan))
        assert runner.has_uncompleted_tasks() is False

    def test_empty_plan_file_returns_false(self) -> None:
        runner, _log, _ = _make_task_runner(MagicMock(), plan_file="")
        assert runner.has_uncompleted_tasks() is False

    def test_malformed_falls_back_to_file_scan(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, "no tasks here\n- [ ] orphan\n")
        runner, _log, _ = _make_task_runner(MagicMock(), plan_file=str(plan))
        assert runner.has_uncompleted_tasks() is True

    def test_resolves_completed_sibling(self, tmp_path: Path) -> None:
        completed = tmp_path / "plan-completed.md"
        completed.write_text(_PLAN_PENDING)

        original = str(tmp_path / "plan.md")
        runner, _log, _ = _make_task_runner(MagicMock(), plan_file=original)
        assert runner.has_uncompleted_tasks() is True


class TestNextPlanTaskPosition:
    def test_first_pending_task(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING_TASK_2)
        runner, _log, _ = _make_task_runner(MagicMock(), plan_file=str(plan))
        assert runner.next_plan_task_position() == 2

    def test_no_pending_returns_zero(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        runner, _log, _ = _make_task_runner(MagicMock(), plan_file=str(plan))
        assert runner.next_plan_task_position() == 0

    def test_empty_plan_returns_zero(self) -> None:
        runner, _log, _ = _make_task_runner(MagicMock(), plan_file="")
        assert runner.next_plan_task_position() == 0


class TestBreakPause:
    def test_break_with_pause_handler_resumes(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        # First call sets break event during run; we'll trigger break before result.
        # Subsequent call returns COMPLETED on the all-done plan.
        break_event = threading.Event()
        call_count = {"n": 0}

        def fake_run(prompt: str) -> Result:
            call_count["n"] += 1
            if call_count["n"] == 1:
                break_event.set()
                return Result(output="", signal="")
            plan.write_text(_PLAN_DONE)
            return Result(output="", signal=SignalCompleted)

        executor.run.side_effect = fake_run
        runner, _log, _ = _make_task_runner(executor, plan_file=str(plan), max_iterations=5)
        runner.set_break_event(break_event)
        runner.set_pause_handler(lambda: True)

        result = runner.run_task_phase()
        assert result is True
        assert call_count["n"] == 2
        assert not break_event.is_set()

    def test_break_without_pause_handler_aborts(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        break_event = threading.Event()

        def fake_run(prompt: str) -> Result:
            break_event.set()
            return Result(output="", signal="")

        executor.run.side_effect = fake_run
        runner, _log, _ = _make_task_runner(executor, plan_file=str(plan))
        runner.set_break_event(break_event)

        with pytest.raises(UserAbortedError):
            runner.run_task_phase()

    def test_break_with_pause_decline_aborts(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        break_event = threading.Event()

        def fake_run(prompt: str) -> Result:
            break_event.set()
            return Result(output="", signal="")

        executor.run.side_effect = fake_run
        runner, _log, _ = _make_task_runner(executor, plan_file=str(plan))
        runner.set_break_event(break_event)
        runner.set_pause_handler(lambda: False)

        with pytest.raises(UserAbortedError):
            runner.run_task_phase()


class TestSessionTimeout:
    def test_idle_no_signal_marks_session_timed_out(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal="", idle_timed_out=True)
        runner, _log, _ = _make_task_runner(executor, plan_file=str(plan), max_iterations=1)
        runner.run_task_phase()
        assert runner.last_session_timed_out is True

    def test_session_timer_fires_clears_signal_and_error(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        cfg = AppConfig(
            max_iterations=1,
            iteration_delay_ms=0,
            session_timeout="1s",
        )
        executor = MagicMock()

        def slow_run(prompt: str) -> Result:
            time.sleep(1.5)
            return Result(output="x", signal=SignalCompleted)

        executor.run.side_effect = slow_run
        runner, _log, _ = _make_task_runner(executor, plan_file=str(plan), app_cfg=cfg)
        runner.run_task_phase()
        assert runner.last_session_timed_out is True


class TestSleepWithCancel:
    def test_sleep_short_returns(self) -> None:
        executor = MagicMock()
        runner, _log, _ = _make_task_runner(executor, plan_file="")
        start = time.monotonic()
        runner._sleep_with_cancel(0.05)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.04

    def test_break_event_cancels_sleep(self) -> None:
        executor = MagicMock()
        runner, _log, _ = _make_task_runner(executor, plan_file="")
        event = threading.Event()
        runner.set_break_event(event)
        event.set()
        start = time.monotonic()
        runner._sleep_with_cancel(2.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.5

    def test_zero_duration_no_op(self) -> None:
        executor = MagicMock()
        runner, _log, _ = _make_task_runner(executor, plan_file="")
        runner._sleep_with_cancel(0.0)


class TestRunDispatch:
    def test_run_full_requires_plan_file(self) -> None:
        executor = MagicMock()
        runner, _log, _ = _make_task_runner(executor, plan_file="")
        with pytest.raises(ValueError, match="plan_file"):
            runner.run_full()

    def test_run_tasks_only_requires_plan_file(self) -> None:
        executor = MagicMock()
        runner, _log, _ = _make_task_runner(executor, plan_file="")
        with pytest.raises(ValueError, match="plan_file"):
            runner.run_tasks_only()

    def test_run_dispatches_full_mode(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalCompleted)
        runner, _log, _ = _make_task_runner(executor, plan_file=str(plan))
        assert runner.run() is True

    def test_run_unsupported_mode_raises(self) -> None:
        executor = MagicMock()
        runner, _log, _ = _make_task_runner(executor, plan_file="")
        runner._ctx.mode = "bogus"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="unsupported mode"):
            runner.run()

    def test_run_dispatches_review_mode(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, _log, _ = _make_task_runner(executor, plan_file="")
        runner._ctx.mode = Mode.REVIEW
        assert runner.run() is True

    def test_run_dispatches_plan_mode(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalPlanReady)
        runner, _log, _ = _make_runner(executor)
        runner._ctx.mode = Mode.PLAN
        assert runner.run() is True
        assert executor.run.call_count == 1


class TestCheckResultError:
    def test_no_error_returns_true(self) -> None:
        runner, _log, _ = _make_task_runner(MagicMock(), plan_file="")
        assert runner._check_result_error(Result(output="", signal="")) is True

    def test_pattern_match_returns_false_and_logs(self) -> None:
        runner, log, _ = _make_task_runner(MagicMock(), plan_file="")
        result = Result(
            output="",
            signal="",
            error=PatternMatchError("API Error:", "claude /usage"),
        )
        assert runner._check_result_error(result) is False
        log.error.assert_called()

    def test_limit_pattern_returns_false_and_logs(self) -> None:
        runner, log, _ = _make_task_runner(MagicMock(), plan_file="")
        result = Result(
            output="",
            signal="",
            error=LimitPatternError("limit", "claude /usage"),
        )
        assert runner._check_result_error(result) is False
        log.error.assert_called()

    def test_unknown_error_logs_and_raises(self) -> None:
        runner, log, _ = _make_task_runner(MagicMock(), plan_file="")
        boom = RuntimeError("boom")
        result = Result(output="", signal="", error=boom)
        with pytest.raises(RuntimeError, match="boom"):
            runner._check_result_error(result)
        log.error.assert_called()


def _make_review_runner(
    executor: object,
    *,
    plan_file: str = "",
    review_executor: object | None = None,
    review_second_executor: object | None = None,
    max_iterations: int = 10,
    mode: Mode = Mode.REVIEW,
) -> tuple[Runner, MagicMock, MagicMock]:
    ctx = RunContext(
        mode=mode,
        plan_file=plan_file,
    )
    log = MagicMock()
    log.path = "/tmp/progress.txt"
    holder = PhaseHolder()
    input_mock = MagicMock()
    deps = Dependencies(
        executor=executor,  # type: ignore[arg-type]
        input_collector=input_mock,
        logger=log,
        holder=holder,
        review_executor=review_executor,  # type: ignore[arg-type]
        review_second_executor=review_second_executor,  # type: ignore[arg-type]
    )
    cfg = AppConfig(
        max_iterations=max_iterations,
        iteration_delay_ms=0,
    )
    runner = Runner(ctx, cfg, deps)
    return runner, log, input_mock


class TestRunClaudeReview:
    def test_review_done_returns_true(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, log, _ = _make_review_runner(executor)
        assert runner.run_claude_review("prompt") is True
        log.print.assert_any_call("review completed, no issues found")

    def test_failed_signal_raises(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalFailed)
        runner, _log, _ = _make_review_runner(executor)
        with pytest.raises(RuntimeError, match="review failed"):
            runner.run_claude_review("prompt")

    def test_no_signal_returns_true_with_warning(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal="")
        runner, log, _ = _make_review_runner(executor)
        assert runner.run_claude_review("prompt") is True
        log.warn.assert_any_call("review did not complete cleanly")

    def test_pattern_match_error_returns_false(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal="",
            error=PatternMatchError("API Error:", "claude /usage"),
        )
        runner, log, _ = _make_review_runner(executor)
        assert runner.run_claude_review("prompt") is False
        log.error.assert_called()

    def test_unknown_error_raises(self) -> None:
        executor = MagicMock()
        boom = RuntimeError("boom")
        executor.run.return_value = Result(output="", signal="", error=boom)
        runner, _log, _ = _make_review_runner(executor)
        with pytest.raises(RuntimeError, match="boom"):
            runner.run_claude_review("prompt")

    def test_review_done_sets_last_review_done(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, _log, _ = _make_review_runner(executor)
        assert runner.run_claude_review("prompt") is True
        assert runner.last_review_done is True

    def test_no_signal_clears_last_review_done(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal="")
        runner, _log, _ = _make_review_runner(executor)
        runner.last_review_done = True
        assert runner.run_claude_review("prompt") is True
        assert runner.last_review_done is False

    def test_pattern_error_does_not_set_last_review_done(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal="",
            error=PatternMatchError("API Error:", "claude /usage"),
        )
        runner, _log, _ = _make_review_runner(executor)
        runner.last_review_done = True
        assert runner.run_claude_review("prompt") is False
        assert runner.last_review_done is False

    def test_failed_signal_clears_last_review_done(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalFailed)
        runner, _log, _ = _make_review_runner(executor)
        runner.last_review_done = True
        with pytest.raises(RuntimeError, match="review failed"):
            runner.run_claude_review("prompt")
        assert runner.last_review_done is False


class TestRunClaudeReviewLoop:
    def test_review_done_first_iteration(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, _log, _ = _make_review_runner(executor)
        assert runner.run_claude_review_loop() is True
        assert executor.run.call_count == 1

    def test_review_second_signal_terminates_loop(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewSecondDone)
        runner, log, _ = _make_review_runner(executor)
        assert runner.run_claude_review_loop() is True
        assert executor.run.call_count == 1
        log.print.assert_any_call("review loop complete, no more findings")

    def test_legacy_review_done_signal_still_terminates_loop(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, log, _ = _make_review_runner(executor)
        assert runner.run_claude_review_loop() is True
        assert executor.run.call_count == 1
        log.print.assert_any_call("review loop complete, no more findings")

    def test_no_commit_detection_stops_loop(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal="")
        runner, log, _ = _make_review_runner(executor)
        git = MagicMock()
        git.head_hash.return_value = "abc123"
        runner.set_git_checker(git)

        result = runner.run_claude_review_loop()
        assert result is True
        assert executor.run.call_count == 1
        log.print.assert_any_call("no changes detected, stopping review loop")

    def test_timed_out_skips_head_check(self) -> None:
        executor = MagicMock()
        call_count = {"n": 0}

        def fake_run(prompt: str) -> Result:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return Result(output="", signal="", idle_timed_out=True)
            return Result(output="", signal=SignalReviewDone)

        executor.run.side_effect = fake_run
        runner, _log, _ = _make_review_runner(executor)
        git = MagicMock()
        git.head_hash.return_value = "samehash"
        runner.set_git_checker(git)

        result = runner.run_claude_review_loop()
        assert result is True
        # Should NOT stop after first iteration even though head is the same
        assert call_count["n"] == 2

    def test_head_changed_continues_loop(self) -> None:
        executor = MagicMock()
        call_count = {"n": 0}

        def fake_run(prompt: str) -> Result:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return Result(output="", signal="")
            return Result(output="", signal=SignalReviewDone)

        executor.run.side_effect = fake_run
        runner, _log, _ = _make_review_runner(executor)
        git = MagicMock()
        git.head_hash.side_effect = ["before", "after", "after2"]
        runner.set_git_checker(git)

        result = runner.run_claude_review_loop()
        assert result is True
        assert call_count["n"] == 2

    def test_iteration_cap_respected(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal="")
        # max_iterations=50 -> review max = max(3, 50//10) = 5
        runner, log, _ = _make_review_runner(executor, max_iterations=50)
        # No git checker so no-commit detection is disabled.
        assert runner.run_claude_review_loop() is True
        assert executor.run.call_count == 5
        log.warn.assert_any_call("max review iterations reached")

    def test_failed_signal_raises(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalFailed)
        runner, _log, _ = _make_review_runner(executor)
        with pytest.raises(RuntimeError, match="review failed"):
            runner.run_claude_review_loop()

    def test_pattern_match_error_returns_false(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal="",
            error=PatternMatchError("API Error:", "claude /usage"),
        )
        runner, _log, _ = _make_review_runner(executor)
        assert runner.run_claude_review_loop() is False


class TestRunnerForwardsCommitFormat:
    def test_task_phase_forwards_commit_format(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalCompleted)
        cfg = AppConfig(
            max_iterations=10,
            iteration_delay_ms=0,
            commit_format="MARKER_TASK_FORMAT",
        )
        runner, _log, _ = _make_task_runner(executor, plan_file=str(plan), app_cfg=cfg)
        runner.run_task_phase()
        prompt = executor.run.call_args_list[0][0][0]
        assert "MARKER_TASK_FORMAT" in prompt
        assert "Format every git commit message using these rules:" in prompt

    def test_review_first_forwards_commit_format(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal=""),  # round 1 — empty signal forces loop
            Result(output="", signal=SignalReviewDone),  # loop iteration
        ]
        ctx = RunContext(mode=Mode.REVIEW, plan_file="")
        log = MagicMock()
        log.path = "/tmp/progress.txt"
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,
            holder=PhaseHolder(),
        )
        cfg = AppConfig(
            max_iterations=10,
            iteration_delay_ms=0,
            commit_format="MARKER_REVIEW_FORMAT",
        )
        runner = Runner(ctx, cfg, deps)
        runner.run_review_only()
        # run_review_only issues both the review_first prompt and the loop's
        # review_second prompt. Both must carry commit_format — assert across
        # every executor call so a regression in either build site is caught.
        assert executor.run.call_count >= 2
        for run_call in executor.run.call_args_list:
            prompt = run_call[0][0]
            assert "MARKER_REVIEW_FORMAT" in prompt
            assert "Format every git commit message using these rules:" in prompt


class TestRunFullPipeline:
    def test_task_then_review(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal=SignalCompleted),  # task phase
            Result(output="", signal=SignalReviewDone),  # review_first
        ]
        runner, log, _ = _make_review_runner(
            executor,
            plan_file=str(plan),
            mode=Mode.FULL,
        )
        assert runner.run_full() is True
        assert executor.run.call_count == 2
        log.print.assert_any_call("nothing to verify, skipping review loop")

    def test_full_pipeline_runs_loop_when_round_one_not_clean(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal=SignalCompleted),  # task phase
            Result(output="", signal=""),  # review_first — no signal, forces loop
            Result(output="", signal=SignalReviewDone),  # review loop iteration
        ]
        runner, log, _ = _make_review_runner(
            executor,
            plan_file=str(plan),
            mode=Mode.FULL,
        )
        assert runner.run_full() is True
        assert executor.run.call_count == 3
        assert call("nothing to verify, skipping review loop") not in log.print.call_args_list

    def test_task_phase_failure_short_circuits(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal="",
            error=PatternMatchError("API Error:", "claude /usage"),
        )
        runner, _log, _ = _make_review_runner(
            executor,
            plan_file=str(plan),
            mode=Mode.FULL,
        )
        assert runner.run_full() is False
        # Only the task phase invocation; no review calls.
        assert executor.run.call_count == 1


class TestRunReviewOnly:
    def test_does_not_invoke_task_phase(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal=SignalReviewDone),  # review_first
        ]
        runner, log, _ = _make_review_runner(
            executor,
            plan_file="",
            mode=Mode.REVIEW,
        )
        assert runner.run_review_only() is True
        assert executor.run.call_count == 1
        log.print.assert_any_call("nothing to verify, skipping review loop")

    def test_skips_loop_when_round_one_returns_review_done(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, log, _ = _make_review_runner(
            executor,
            plan_file="",
            mode=Mode.REVIEW,
        )
        assert runner.run_review_only() is True
        assert executor.run.call_count == 1
        log.print.assert_any_call("nothing to verify, skipping review loop")

    def test_runs_loop_when_round_one_did_not_complete_cleanly(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal=""),  # round 1 — no signal
            Result(output="", signal=SignalReviewDone),  # loop iteration
        ]
        runner, log, _ = _make_review_runner(
            executor,
            plan_file="",
            mode=Mode.REVIEW,
        )
        assert runner.run_review_only() is True
        assert executor.run.call_count == 2
        assert call("nothing to verify, skipping review loop") not in log.print.call_args_list

    def test_review_second_signal_does_not_skip_loop_after_first_pass(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal=SignalReviewSecondDone),  # round 1 — wrong signal
            Result(output="", signal=SignalReviewDone),  # loop iteration terminates
        ]
        runner, log, _ = _make_review_runner(
            executor,
            plan_file="",
            mode=Mode.REVIEW,
        )
        assert runner.run_review_only() is True
        assert executor.run.call_count == 2
        assert runner.last_review_done is False
        assert call("nothing to verify, skipping review loop") not in log.print.call_args_list


class TestReviewAgentModelsWiring:
    def test_review_first_threads_agent_models_override(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        ctx = RunContext(mode=Mode.REVIEW, plan_file="")
        log = MagicMock()
        log.path = "/tmp/progress.txt"
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,
            holder=PhaseHolder(),
        )
        cfg = AppConfig(
            max_iterations=10,
            iteration_delay_ms=0,
            agent_models={"quality": "haiku"},
        )
        runner = Runner(ctx, cfg, deps)
        assert runner.run_review_only() is True
        prompt = executor.run.call_args_list[0][0][0]
        # quality (overridden to haiku) + implementation (sonnet) +
        # testing (opus) + simplification (opus).
        assert prompt.count("with model=haiku") == 1
        assert prompt.count("with model=sonnet") == 1
        assert prompt.count("with model=opus") == 2

    def test_review_first_defaults_use_frontmatter_models(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        ctx = RunContext(mode=Mode.REVIEW, plan_file="")
        log = MagicMock()
        log.path = "/tmp/progress.txt"
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,
            holder=PhaseHolder(),
        )
        cfg = AppConfig(max_iterations=10, iteration_delay_ms=0)
        runner = Runner(ctx, cfg, deps)
        assert runner.run_review_only() is True
        prompt = executor.run.call_args_list[0][0][0]
        # No overrides: quality/implementation default to sonnet,
        # testing/simplification default to opus.
        assert prompt.count("with model=sonnet") == 2
        assert prompt.count("with model=opus") == 2
        assert "with model=haiku" not in prompt

    def test_review_second_threads_agent_models_override(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal=""),  # forces loop
            Result(output="", signal=SignalReviewDone),  # loop iteration
        ]
        ctx = RunContext(mode=Mode.REVIEW, plan_file="")
        log = MagicMock()
        log.path = "/tmp/progress.txt"
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,
            holder=PhaseHolder(),
        )
        cfg = AppConfig(
            max_iterations=10,
            iteration_delay_ms=0,
            agent_models={"implementation": "haiku"},
        )
        runner = Runner(ctx, cfg, deps)
        assert runner.run_review_only() is True
        # The review-loop prompt is the second executor call.
        loop_prompt = executor.run.call_args_list[1][0][0]
        # review_second expands only quality + implementation.
        assert loop_prompt.count("with model=haiku") == 1
        assert loop_prompt.count("with model=sonnet") == 1


class TestReviewExecutorRouting:
    def test_review_uses_review_executor_when_set(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        primary = MagicMock()
        review = MagicMock()
        primary.run.return_value = Result(output="", signal=SignalCompleted)
        review.run.side_effect = [
            Result(output="", signal=SignalReviewDone),  # review_first
        ]
        runner, _log, _ = _make_review_runner(
            primary,
            plan_file=str(plan),
            review_executor=review,
            mode=Mode.FULL,
        )
        assert runner.run_full() is True
        assert primary.run.call_count == 1
        assert review.run.call_count == 1

    def test_review_executor_used_for_loop(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        primary = MagicMock()
        review = MagicMock()
        primary.run.return_value = Result(output="", signal=SignalCompleted)
        review.run.side_effect = [
            Result(output="", signal=""),  # review_first — forces loop
            Result(output="", signal=SignalReviewDone),  # loop iteration
        ]
        runner, _log, _ = _make_review_runner(
            primary,
            plan_file=str(plan),
            review_executor=review,
            mode=Mode.FULL,
        )
        assert runner.run_full() is True
        assert primary.run.call_count == 1
        assert review.run.call_count == 2

    def test_review_falls_back_to_primary_executor(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, _log, _ = _make_review_runner(executor)
        # No review_executor; property should return the primary one.
        assert runner._review_executor is executor


class TestReviewSecondExecutorRouting:
    def test_review_second_executor_used_for_loop(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        primary = MagicMock()
        review = MagicMock()
        review_second = MagicMock()
        primary.run.return_value = Result(output="", signal=SignalCompleted)
        review.run.return_value = Result(output="", signal="")  # forces loop
        review_second.run.return_value = Result(output="", signal=SignalReviewSecondDone)
        runner, _log, _ = _make_review_runner(
            primary,
            plan_file=str(plan),
            review_executor=review,
            review_second_executor=review_second,
            mode=Mode.FULL,
        )
        assert runner.run_full() is True
        assert primary.run.call_count == 1
        assert review.run.call_count == 1
        assert review_second.run.call_count == 1

    def test_review_second_executor_falls_back_to_review_executor(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        primary = MagicMock()
        review = MagicMock()
        primary.run.return_value = Result(output="", signal=SignalCompleted)
        review.run.side_effect = [
            Result(output="", signal=""),  # review_first — forces loop
            Result(output="", signal=SignalReviewDone),  # loop iteration
        ]
        runner, _log, _ = _make_review_runner(
            primary,
            plan_file=str(plan),
            review_executor=review,
            review_second_executor=None,
            mode=Mode.FULL,
        )
        assert runner.run_full() is True
        assert primary.run.call_count == 1
        assert review.run.call_count == 2

    def test_review_second_executor_falls_back_to_primary_when_both_none(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, _log, _ = _make_review_runner(executor)
        assert runner._review_loop_executor is executor


def _flatten_print_calls(log: MagicMock) -> str:
    return "\n".join(" ".join(str(a) for a in call.args) for call in log.print.call_args_list)


_FAKE_USAGE = Usage(
    input_tokens=100,
    output_tokens=50,
    cache_read_tokens=200,
    cache_creation_tokens=10,
)


class TestRunnerUsageSummaries:
    def _make_task_runner_with_models(
        self,
        executor: object,
        plan: Path,
        *,
        app_cfg: AppConfig | None = None,
        task_model: str = "claude-opus-4-7",
    ) -> tuple[Runner, MagicMock]:
        ctx = RunContext(mode=Mode.FULL, plan_file=str(plan))
        log = MagicMock()
        log.path = "/tmp/progress.txt"
        holder = PhaseHolder()
        deps = Dependencies(
            executor=executor,  # type: ignore[arg-type]
            input_collector=MagicMock(),
            logger=log,
            holder=holder,
            task_model=task_model,
        )
        cfg = app_cfg or AppConfig(max_iterations=10, iteration_delay_ms=0)
        return Runner(ctx, cfg, deps), log

    def test_iteration_and_phase_summary_emitted(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalCompleted,
            usage=_FAKE_USAGE,
            session_id="abc123",
            model="claude-opus-4-7",
        )
        runner, log = self._make_task_runner_with_models(executor, plan)

        runner.run_task_phase()

        printed = _flatten_print_calls(log)
        assert "iter 1 done in" in printed
        assert "phase task done in" in printed
        assert "session abc123" in printed

    def test_print_usage_false_suppresses_summary(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalCompleted,
            usage=_FAKE_USAGE,
            model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=10, iteration_delay_ms=0, print_usage=False)
        runner, log = self._make_task_runner_with_models(executor, plan, app_cfg=cfg)

        runner.run_task_phase()

        printed = _flatten_print_calls(log)
        assert "iter " not in printed
        assert "phase " not in printed

    def test_missing_usage_renders_unavailable(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalCompleted,
            usage=None,
            model="claude-opus-4-7",
        )
        runner, log = self._make_task_runner_with_models(executor, plan)

        runner.run_task_phase()

        printed = _flatten_print_calls(log)
        assert "usage unavailable" in printed

    def test_unknown_model_renders_question_mark_cost(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalCompleted,
            usage=_FAKE_USAGE,
            model="claude-mystery-9-9",
        )
        runner, log = self._make_task_runner_with_models(
            executor, plan, task_model="claude-mystery-9-9"
        )

        runner.run_task_phase()

        printed = _flatten_print_calls(log)
        assert "cost ≈ ?" in printed
        # Token counts are still present on the iteration line.
        assert "in 100" in printed
        assert "out 50" in printed

    def test_cost_estimates_false_drops_cost_keeps_tokens(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalCompleted,
            usage=_FAKE_USAGE,
            model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=10, iteration_delay_ms=0, cost_estimates=False)
        runner, log = self._make_task_runner_with_models(executor, plan, app_cfg=cfg)

        runner.run_task_phase()

        printed = _flatten_print_calls(log)
        assert "cost" not in printed
        assert "in 100" in printed
        assert "out 50" in printed

    def test_chain_collector_receives_phase_stats(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalCompleted,
            usage=_FAKE_USAGE,
            model="claude-opus-4-7",
        )
        runner, _ = self._make_task_runner_with_models(executor, plan)
        chain = UsageStats()
        runner.set_chain_collector(chain)

        runner.run_task_phase()

        assert chain.iterations == 1
        assert chain.input == 100
        assert chain.output == 50
        assert chain.had_usage is True
        assert chain.cost_known is True

    def test_phase_summary_emitted_on_executor_exception(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        boom = RuntimeError("kaboom")
        executor.run.return_value = Result(
            output="",
            signal="",
            error=boom,
            usage=_FAKE_USAGE,
            model="claude-opus-4-7",
        )
        runner, log = self._make_task_runner_with_models(executor, plan)

        with pytest.raises(RuntimeError, match="kaboom"):
            runner.run_task_phase()

        printed = _flatten_print_calls(log)
        assert "phase task done in" in printed

    def test_iteration_uses_configured_model_when_result_model_empty(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalCompleted,
            usage=_FAKE_USAGE,
            model="",
        )
        runner, log = self._make_task_runner_with_models(
            executor, plan, task_model="claude-opus-4-7"
        )

        runner.run_task_phase()

        printed = _flatten_print_calls(log)
        assert "cost ≈ $" in printed
        assert "cost ≈ ?" not in printed

    def test_phase_summary_emitted_on_plan_exception(self, tmp_path: Path) -> None:
        executor = MagicMock()
        boom = RuntimeError("plan-boom")
        executor.run.return_value = Result(
            output="",
            signal="",
            error=boom,
            usage=_FAKE_USAGE,
            model="claude-opus-4-7",
        )
        ctx = RunContext(mode=Mode.PLAN, plan_description="desc")
        log = MagicMock()
        log.path = "/tmp/progress.txt"
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,
            holder=PhaseHolder(),
            plan_model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=10, iteration_delay_ms=0)
        runner = Runner(ctx, cfg, deps)

        executor.run.return_value = Result(
            output="",
            signal="",
            error=boom,
            usage=_FAKE_USAGE,
            model="claude-opus-4-7",
        )
        with pytest.raises(RuntimeError, match="plan-boom"):
            runner.run_plan_creation()

        printed = _flatten_print_calls(log)
        assert "phase plan done in" in printed

    def test_phase_summary_emitted_on_review_exception(self) -> None:
        executor = MagicMock()
        boom = RuntimeError("review-boom")
        executor.run.return_value = Result(
            output="",
            signal="",
            error=boom,
            usage=_FAKE_USAGE,
            model="claude-opus-4-7",
        )
        ctx = RunContext(mode=Mode.REVIEW)
        log = MagicMock()
        log.path = "/tmp/progress.txt"
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,
            holder=PhaseHolder(),
            review_model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=10, iteration_delay_ms=0)
        runner = Runner(ctx, cfg, deps)

        with pytest.raises(RuntimeError, match="review-boom"):
            runner.run_claude_review("prompt")

        printed = _flatten_print_calls(log)
        assert "phase review done in" in printed

    def test_phase_summary_emitted_on_review_loop_exception(self) -> None:
        executor = MagicMock()
        boom = RuntimeError("review-loop-boom")
        executor.run.return_value = Result(
            output="",
            signal="",
            error=boom,
            usage=_FAKE_USAGE,
            model="claude-opus-4-7",
        )
        ctx = RunContext(mode=Mode.REVIEW)
        log = MagicMock()
        log.path = "/tmp/progress.txt"
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,
            holder=PhaseHolder(),
            review_model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=10, iteration_delay_ms=0)
        runner = Runner(ctx, cfg, deps)

        with pytest.raises(RuntimeError, match="review-loop-boom"):
            runner.run_claude_review_loop()

        printed = _flatten_print_calls(log)
        assert "phase review-loop done in" in printed


class _FakeLogger:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.path = "/tmp/progress.txt"

    def print(self, fmt: str, *args: object) -> None:
        pass

    def print_section(self, section: object) -> None:
        pass

    def print_aligned(self, text: str) -> None:
        pass

    def log_question(self, question: str, options: list[str]) -> None:
        pass

    def log_answer(self, answer: str) -> None:
        pass

    def error(self, fmt: str, *args: object) -> None:
        pass

    def warn(self, fmt: str, *args: object) -> None:
        pass

    def log_event(self, event: object) -> None:
        self.events.append(event)


def _event_kinds(events: list[object]) -> list[str]:
    return [type(e).__name__ for e in events]


class TestRunnerEmitsEvents:
    def test_task_phase_event_sequence(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalCompleted,
            session_id="sess-1",
        )

        ctx = RunContext(
            mode=Mode.FULL,
            plan_file=str(plan),
            default_branch="main",
        )
        log = _FakeLogger()
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,  # type: ignore[arg-type]
            holder=PhaseHolder(),
            task_model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=10, iteration_delay_ms=0)
        runner = Runner(ctx, cfg, deps)

        assert runner.run_task_phase() is True

        assert _event_kinds(log.events) == [
            "PhaseStartEvent",
            "IterationStartEvent",
            "IterationEndEvent",
            "SignalEvent",
            "PhaseEndEvent",
        ]
        ps = log.events[0]
        assert isinstance(ps, PhaseStartEvent)
        assert ps.phase == "task"
        assert ps.branch == "main"
        assert ps.model == "claude-opus-4-7"

        it_start = log.events[1]
        assert isinstance(it_start, IterationStartEvent)
        assert it_start.phase == "task"
        assert it_start.iteration == 1
        assert it_start.task_index == 1

        it_end = log.events[2]
        assert isinstance(it_end, IterationEndEvent)
        assert it_end.iteration == 1
        assert it_end.session_id == "sess-1"

        sig = log.events[3]
        assert isinstance(sig, SignalEvent)
        assert sig.signal == SignalCompleted
        assert sig.iteration == 1

        pe = log.events[4]
        assert isinstance(pe, PhaseEndEvent)
        assert pe.phase == "task"
        assert pe.result == "success"
        assert pe.iterations == 1

    def test_plan_creation_event_sequence(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalPlanReady,
            session_id="sess-plan",
        )
        ctx = RunContext(
            mode=Mode.PLAN,
            plan_description="desc",
            default_branch="main",
        )
        log = _FakeLogger()
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,  # type: ignore[arg-type]
            holder=PhaseHolder(),
            plan_model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=50, iteration_delay_ms=0)
        runner = Runner(ctx, cfg, deps)

        assert runner.run_plan_creation() is True

        assert _event_kinds(log.events) == [
            "PhaseStartEvent",
            "IterationStartEvent",
            "IterationEndEvent",
            "SignalEvent",
            "PhaseEndEvent",
        ]
        it_start = log.events[1]
        assert isinstance(it_start, IterationStartEvent)
        assert it_start.task_index is None
        pe = log.events[4]
        assert isinstance(pe, PhaseEndEvent)
        assert pe.phase == "plan"
        assert pe.result == "success"

    def test_task_phase_failure_marks_phase_end_failure(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal="",
            error=PatternMatchError("API Error:", "claude /usage"),
        )
        ctx = RunContext(mode=Mode.FULL, plan_file=str(plan), default_branch="main")
        log = _FakeLogger()
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,  # type: ignore[arg-type]
            holder=PhaseHolder(),
            task_model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=10, iteration_delay_ms=0)
        runner = Runner(ctx, cfg, deps)

        assert runner.run_task_phase() is False

        kinds = _event_kinds(log.events)
        assert "ErrorEvent" in kinds
        assert kinds[-1] == "PhaseEndEvent"
        err = next(e for e in log.events if isinstance(e, ErrorEvent))
        assert err.phase == "task"
        assert err.iteration == 1
        assert "API Error" in err.message
        pe = log.events[-1]
        assert isinstance(pe, PhaseEndEvent)
        assert pe.result == "failure"

    def test_limit_pattern_emits_error_event(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal="",
            error=LimitPatternError("limit", "claude /usage"),
        )
        ctx = RunContext(mode=Mode.PLAN, plan_description="desc", default_branch="main")
        log = _FakeLogger()
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,  # type: ignore[arg-type]
            holder=PhaseHolder(),
            plan_model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=50, iteration_delay_ms=0)
        runner = Runner(ctx, cfg, deps)

        assert runner.run_plan_creation() is False

        errs = [e for e in log.events if isinstance(e, ErrorEvent)]
        assert len(errs) == 1
        assert errs[0].phase == "plan"
        assert errs[0].iteration == 1

    def test_task_phase_runtime_error_still_emits_phase_end_failure(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_PENDING)
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal=SignalFailed,
            session_id="sess-1",
        )
        ctx = RunContext(mode=Mode.FULL, plan_file=str(plan), default_branch="main")
        log = _FakeLogger()
        deps = Dependencies(
            executor=executor,
            input_collector=MagicMock(),
            logger=log,  # type: ignore[arg-type]
            holder=PhaseHolder(),
            task_model="claude-opus-4-7",
        )
        cfg = AppConfig(max_iterations=10, iteration_delay_ms=0, task_retry_count=0)
        runner = Runner(ctx, cfg, deps)

        with pytest.raises(RuntimeError, match="task execution failed"):
            runner.run_task_phase()

        pe = log.events[-1]
        assert isinstance(pe, PhaseEndEvent)
        assert pe.phase == "task"
        assert pe.result == "failure"
