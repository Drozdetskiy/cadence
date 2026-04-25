from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rlx.config import Config as AppConfig
from rlx.executor.claude_executor import (
    LimitPatternError,
    PatternMatchError,
    Result,
)
from rlx.processor.prompts import (
    append_commit_trailer_instruction,
    build_plan_prompt,
    load_prompt,
    normalize_crlf,
    replace_base_variables,
)
from rlx.processor.runner import (
    Dependencies,
    RunContext,
    Runner,
    UserAbortedError,
)
from rlx.status import (
    Mode,
    PhaseHolder,
    SignalCompleted,
    SignalFailed,
    SignalPlanReady,
    SignalReviewDone,
)


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
        assert "<<<RLX:QUESTION>>>" in prompt

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
        prompt = (
            "{{PLAN_FILE}} {{PROGRESS_FILE}} "
            "{{GOAL}} {{DEFAULT_BRANCH}} {{PLANS_DIR}}"
        )
        result = replace_base_variables(
            prompt,
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            goal="implement feature X",
            default_branch="main",
            plans_dir="docs/plans",
        )
        assert "/tmp/plan.md" in result
        assert "/tmp/progress.txt" in result
        assert "implement feature X" in result
        assert "main" in result
        assert "docs/plans" in result

    def test_fallback_values(self) -> None:
        prompt = (
            "{{PLAN_FILE}} {{PROGRESS_FILE}} "
            "{{GOAL}} {{DEFAULT_BRANCH}} {{PLANS_DIR}}"
        )
        result = replace_base_variables(prompt)
        assert "(no plan file" in result
        assert "(no progress file" in result
        assert "current branch vs master" in result
        assert "master" in result
        assert "docs/plans" in result


class TestAppendCommitTrailer:
    def test_appends_trailer(self) -> None:
        result = append_commit_trailer_instruction(
            "prompt", "Signed-off-by: Bot"
        )
        assert "Signed-off-by: Bot" in result
        assert "trailer" in result.lower()

    def test_empty_trailer_unchanged(self) -> None:
        assert (
            append_commit_trailer_instruction("prompt", "")
            == "prompt"
        )


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
            plans_dir="my-plans",
        )
        assert "/tmp/prog.txt" in result
        assert "my-plans" in result

    def test_appends_commit_trailer(self) -> None:
        result = build_plan_prompt(
            "desc",
            commit_trailer="Co-authored-by: Bot",
        )
        assert "Co-authored-by: Bot" in result

    def test_local_override(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "make_plan.txt").write_text(
            "Custom: {{PLAN_DESCRIPTION}}"
        )
        result = build_plan_prompt(
            "my feature", local_dir=tmp_path
        )
        assert result == "Custom: my feature"


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
            '<<<RLX:QUESTION>>>\n'
            '{"question": "Which DB?", '
            '"options": ["Postgres", "SQLite"]}\n'
            '<<<RLX:END>>>'
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
            '<<<RLX:PLAN_DRAFT>>>\n'
            '# My Plan\n'
            '## Overview\n'
            'Implementation details\n'
            '<<<RLX:END>>>'
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
        draft = (
            '<<<RLX:PLAN_DRAFT>>>\nDraft 1\n<<<RLX:END>>>'
        )
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
        executor.run.return_value = Result(
            output="", signal=SignalFailed
        )
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
        limit_err = LimitPatternError(
            "You've hit your limit", "claude /usage"
        )
        executor.run.side_effect = [
            Result(output="", signal="", error=limit_err),
            Result(output="ok", signal=SignalPlanReady),
        ]
        app = AppConfig(
            wait_on_limit="1s",
            max_iterations=50,
            iteration_delay_ms=0,
        )
        runner, _log, _input_mock = _make_runner(
            executor, plan_description="test", app_cfg=app
        )

        start = time.monotonic()
        runner.run_plan_creation()
        elapsed = time.monotonic() - start

        assert executor.run.call_count == 2
        assert elapsed >= 0.5

    def test_max_iterations_warns(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(
            output="no signals", signal=""
        )
        runner, log, _input_mock = _make_runner(
            executor, max_iterations=5
        )

        runner.run_plan_creation()

        log.warn.assert_called()
        assert executor.run.call_count == 5


class TestRunnerPlanCreationIdleTimeout:
    def test_idle_timeout_continues(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = [
            Result(
                output="", signal="", idle_timed_out=True
            ),
            Result(output="done", signal=SignalPlanReady),
        ]
        runner, _log, _input_mock = _make_runner(executor)

        runner.run_plan_creation()

        assert executor.run.call_count == 2


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


_PLAN_DONE = (
    "# Plan\n"
    "### Task 1: A\n"
    "- [x] one\n"
)
_PLAN_PENDING = (
    "# Plan\n"
    "### Task 1: A\n"
    "- [ ] one\n"
)
_PLAN_PENDING_TASK_2 = (
    "# Plan\n"
    "### Task 1: A\n"
    "- [x] one\n"
    "### Task 2: B\n"
    "- [ ] two\n"
)


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

    def test_completed_with_uncompleted_warns_then_returns_true(
        self, tmp_path: Path
    ) -> None:
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
        runner, log, _ = _make_task_runner(
            executor, plan_file=str(plan), max_iterations=5
        )

        result = runner.run_task_phase()

        assert result is True
        log.warn.assert_any_call(
            "COMPLETED signal received but uncompleted tasks remain"
        )

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
        runner, log, _ = _make_task_runner(
            executor, plan_file=str(plan), max_iterations=3
        )

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

    def test_resolves_completed_subdir(self, tmp_path: Path) -> None:
        completed_dir = tmp_path / "completed"
        completed_dir.mkdir()
        moved = completed_dir / "plan.md"
        moved.write_text(_PLAN_PENDING)

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
        runner, _log, _ = _make_task_runner(
            executor, plan_file=str(plan), max_iterations=5
        )
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
        executor.run.return_value = Result(
            output="", signal="", idle_timed_out=True
        )
        runner, _log, _ = _make_task_runner(
            executor, plan_file=str(plan), max_iterations=1
        )
        runner.run_task_phase()
        assert runner.last_session_timed_out is True

    def test_session_timer_fires_clears_signal_and_error(
        self, tmp_path: Path
    ) -> None:
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
        runner, _log, _ = _make_task_runner(
            executor, plan_file=str(plan), app_cfg=cfg
        )
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


def _make_review_runner(
    executor: object,
    *,
    plan_file: str = "",
    finalize_enabled: bool = False,
    review_executor: object | None = None,
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
    )
    cfg = AppConfig(
        max_iterations=max_iterations,
        iteration_delay_ms=0,
        finalize_enabled=finalize_enabled,
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


class TestRunClaudeReviewLoop:
    def test_review_done_first_iteration(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, _log, _ = _make_review_runner(executor)
        assert runner.run_claude_review_loop() is True
        assert executor.run.call_count == 1

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


class TestRunFinalize:
    def test_disabled_no_executor_call(self) -> None:
        executor = MagicMock()
        runner, _log, _ = _make_review_runner(executor, finalize_enabled=False)
        runner.run_finalize()
        executor.run.assert_not_called()

    def test_enabled_success(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, log, _ = _make_review_runner(executor, finalize_enabled=True)
        runner.run_finalize()
        executor.run.assert_called_once()
        log.print.assert_any_call("finalize step completed")

    def test_enabled_generic_error_swallowed(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = RuntimeError("boom")
        runner, log, _ = _make_review_runner(executor, finalize_enabled=True)
        runner.run_finalize()
        log.warn.assert_called()

    def test_enabled_keyboard_interrupt_propagates(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = KeyboardInterrupt()
        runner, _log, _ = _make_review_runner(executor, finalize_enabled=True)
        with pytest.raises(KeyboardInterrupt):
            runner.run_finalize()

    def test_enabled_pattern_match_error_swallowed(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(
            output="",
            signal="",
            error=PatternMatchError("API Error:", "claude /usage"),
        )
        runner, log, _ = _make_review_runner(executor, finalize_enabled=True)
        runner.run_finalize()
        log.error.assert_called()

    def test_enabled_failed_signal_swallowed(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalFailed)
        runner, log, _ = _make_review_runner(executor, finalize_enabled=True)
        runner.run_finalize()
        log.warn.assert_any_call("finalize reported failure")


class TestRunFullPipeline:
    def test_task_then_review_then_finalize(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal=SignalCompleted),  # task phase
            Result(output="", signal=SignalReviewDone),  # review_first
            Result(output="", signal=SignalReviewDone),  # review_loop
            Result(output="", signal=SignalReviewDone),  # finalize
        ]
        runner, _log, _ = _make_review_runner(
            executor,
            plan_file=str(plan),
            finalize_enabled=True,
            mode=Mode.FULL,
        )
        assert runner.run_full() is True
        assert executor.run.call_count == 4

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
            finalize_enabled=True,
            mode=Mode.FULL,
        )
        assert runner.run_full() is False
        # Only the task phase invocation; no review/finalize calls.
        assert executor.run.call_count == 1


class TestRunReviewOnly:
    def test_does_not_invoke_task_phase(self) -> None:
        executor = MagicMock()
        executor.run.side_effect = [
            Result(output="", signal=SignalReviewDone),  # review_first
            Result(output="", signal=SignalReviewDone),  # review_loop
            Result(output="", signal=SignalReviewDone),  # finalize
        ]
        runner, _log, _ = _make_review_runner(
            executor,
            plan_file="",
            finalize_enabled=True,
            mode=Mode.REVIEW,
        )
        assert runner.run_review_only() is True
        assert executor.run.call_count == 3


class TestReviewExecutorRouting:
    def test_review_uses_review_executor_when_set(self, tmp_path: Path) -> None:
        plan = _write_plan(tmp_path, _PLAN_DONE)
        primary = MagicMock()
        review = MagicMock()
        primary.run.return_value = Result(output="", signal=SignalCompleted)
        review.run.side_effect = [
            Result(output="", signal=SignalReviewDone),  # review_first
            Result(output="", signal=SignalReviewDone),  # review_loop
            Result(output="", signal=SignalReviewDone),  # finalize
        ]
        runner, _log, _ = _make_review_runner(
            primary,
            plan_file=str(plan),
            finalize_enabled=True,
            review_executor=review,
            mode=Mode.FULL,
        )
        assert runner.run_full() is True
        assert primary.run.call_count == 1
        assert review.run.call_count == 3

    def test_review_falls_back_to_primary_executor(self) -> None:
        executor = MagicMock()
        executor.run.return_value = Result(output="", signal=SignalReviewDone)
        runner, _log, _ = _make_review_runner(executor)
        # No review_executor; property should return the primary one.
        assert runner._review_executor is executor
