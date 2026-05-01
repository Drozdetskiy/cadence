from __future__ import annotations

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
)
from rlx.status import (
    Mode,
    PhaseHolder,
    SignalFailed,
    SignalPlanReady,
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
