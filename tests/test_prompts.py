from __future__ import annotations

from pathlib import Path

from rlx.processor.prompts import (
    build_task_prompt,
    load_prompt,
)


class TestLoadTaskPrompt:
    def test_loads_embedded_task(self) -> None:
        prompt = load_prompt("task")
        assert "{{PLAN_FILE}}" in prompt
        assert "{{PROGRESS_FILE}}" in prompt
        assert "<<<RLX:ALL_TASKS_DONE>>>" in prompt
        assert "<<<RLX:TASK_FAILED>>>" in prompt


class TestBuildTaskPrompt:
    def test_substitutes_plan_file(self) -> None:
        result = build_task_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "/tmp/plan.md" in result
        assert "{{PLAN_FILE}}" not in result
        assert "{{PROGRESS_FILE}}" not in result
        assert "{{DEFAULT_BRANCH}}" not in result

    def test_substitutes_progress_file(self) -> None:
        result = build_task_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
        )
        assert "/tmp/progress.txt" in result

    def test_appends_commit_trailer(self) -> None:
        result = build_task_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            commit_trailer="Co-authored-by: Bot",
        )
        assert "Co-authored-by: Bot" in result
        assert "trailer" in result.lower()

    def test_no_trailer_when_empty(self) -> None:
        result_no = build_task_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
        )
        assert "Co-authored-by" not in result_no

    def test_retains_signal_markers(self) -> None:
        result = build_task_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
        )
        assert "<<<RLX:ALL_TASKS_DONE>>>" in result
        assert "<<<RLX:TASK_FAILED>>>" in result

    def test_local_override(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "task.txt").write_text(
            "Custom task: {{PLAN_FILE}} "
            "goal={{GOAL}} branch={{DEFAULT_BRANCH}}"
        )
        result = build_task_prompt(
            plan_file="/tmp/p.md",
            progress_file="/tmp/pr.txt",
            default_branch="develop",
            local_dir=tmp_path,
        )
        assert result == (
            "Custom task: /tmp/p.md "
            "goal=implementation of plan at /tmp/p.md branch=develop"
        )
