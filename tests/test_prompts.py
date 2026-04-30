from __future__ import annotations

from pathlib import Path

import pytest

from cadence.processor.prompts import (
    build_finalize_prompt,
    build_review_first_prompt,
    build_review_second_prompt,
    build_task_prompt,
    expand_agent_references,
    format_agent_expansion,
    load_prompt,
    replace_prompt_variables,
)


class TestLoadTaskPrompt:
    def test_loads_embedded_task(self) -> None:
        prompt = load_prompt("task")
        assert "{{PLAN_FILE}}" in prompt
        assert "{{PROGRESS_FILE}}" in prompt
        assert "<<<CADENCE:ALL_TASKS_DONE>>>" in prompt
        assert "<<<CADENCE:TASK_FAILED>>>" in prompt

    def test_missing_prompt_raises_runtime_error_with_diagnostic(
        self,
    ) -> None:
        with pytest.raises(RuntimeError) as excinfo:
            load_prompt("does_not_exist")
        message = str(excinfo.value)
        assert "does_not_exist" in message
        assert "prompt" in message
        assert "rlx" in message
        assert "reinstall" in message
        assert "pip install" in message
        assert isinstance(excinfo.value.__cause__, FileNotFoundError)

    @pytest.mark.parametrize(
        "name",
        [
            "make_plan",
            "task",
            "review_first",
            "review_second",
            "finalize",
        ],
    )
    def test_all_shipped_prompts_load(self, name: str) -> None:
        prompt = load_prompt(name)
        assert prompt.strip(), (
            f"shipped prompt {name!r} loaded but is empty"
        )


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
        assert "<<<CADENCE:ALL_TASKS_DONE>>>" in result
        assert "<<<CADENCE:TASK_FAILED>>>" in result

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


class TestFormatAgentExpansion:
    def test_with_model(self) -> None:
        out = format_agent_expansion(
            "hello body", model="sonnet", agent_type="general-purpose"
        )
        assert "Use the Task tool with model=sonnet" in out
        assert "launch a general-purpose agent" in out
        assert "<<<AGENT_PROMPT BEGIN>>>\nhello body\n<<<AGENT_PROMPT END>>>" in out
        assert "Report findings only - no positive observations." in out

    def test_body_with_quotes_preserved(self) -> None:
        body = 'has "double" and \'single\' quotes'
        out = format_agent_expansion(
            body, model="", agent_type="general-purpose"
        )
        assert body in out

    def test_without_model(self) -> None:
        out = format_agent_expansion(
            "body", model="", agent_type="code-reviewer"
        )
        assert "Use the Task tool to launch a code-reviewer agent" in out
        assert "with model=" not in out


class TestExpandAgentReferences:
    def test_expands_embedded_agent(self) -> None:
        result = expand_agent_references(
            "PRE {{agent:quality}} POST",
            local_dir=None,
            warn=None,
            base_vars={},
        )
        assert "{{agent:quality}}" not in result
        assert "Use the Task tool" in result
        assert "PRE" in result and "POST" in result

    def test_missing_agent_warns_and_keeps_marker(self) -> None:
        warnings: list[str] = []
        result = expand_agent_references(
            "before {{agent:nonexistent}} after",
            local_dir=None,
            warn=warnings.append,
            base_vars={},
        )
        assert "{{agent:nonexistent}}" in result
        assert warnings, "expected at least one warning"
        assert any("nonexistent" in w for w in warnings)

    def test_missing_embedded_agent_surfaces_diagnostic_via_warn(
        self,
    ) -> None:
        warnings: list[str] = []
        result = expand_agent_references(
            "x {{agent:not-a-real-agent}} y",
            local_dir=None,
            warn=warnings.append,
            base_vars={},
        )
        assert "{{agent:not-a-real-agent}}" in result
        assert any(
            "not-a-real-agent" in w
            and "reinstall" in w
            and "pip install" in w
            for w in warnings
        ), (
            "expected the load_agent diagnostic to be forwarded "
            "to the warn callback, not swallowed"
        )

    def test_recursion_guard(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "parent.txt").write_text(
            "outer body has {{agent:child}} inside"
        )
        (agents_dir / "child.txt").write_text("child body")
        result = expand_agent_references(
            "{{agent:parent}}",
            local_dir=tmp_path,
            warn=None,
            base_vars={},
        )
        assert "{{agent:child}}" in result
        assert "child body" not in result

    def test_base_vars_applied_to_agent_body(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "quality.txt").write_text(
            "review branch {{DEFAULT_BRANCH}} goal {{GOAL}}"
        )
        result = expand_agent_references(
            "{{agent:quality}}",
            local_dir=tmp_path,
            warn=None,
            base_vars={
                "plan_file": "",
                "progress_file": "",
                "goal": "custom goal",
                "default_branch": "develop",
                "plans_dir": "",
            },
        )
        assert "review branch develop goal custom goal" in result
        assert "{{DEFAULT_BRANCH}}" not in result
        assert "{{GOAL}}" not in result


class TestReplacePromptVariables:
    def test_trailer_appended_once(self) -> None:
        trailer = "Co-Authored-By: Bot <noreply@example.com>"
        prompt = "branch={{DEFAULT_BRANCH}} agents:\n{{agent:quality}}"
        result = replace_prompt_variables(
            prompt,
            plan_file="",
            progress_file="",
            goal="some goal",
            default_branch="main",
            plans_dir="",
            commit_trailer=trailer,
            local_dir=None,
        )
        assert result.count(trailer) == 1
        agent_start = result.index("Use the Task tool")
        agent_end = result.index(
            "Report findings only - no positive observations."
        )
        assert trailer not in result[agent_start:agent_end]
        assert result.rstrip().endswith(trailer)

    def test_no_trailer_when_empty(self) -> None:
        prompt = "x"
        result = replace_prompt_variables(
            prompt,
            plan_file="",
            progress_file="",
            goal="",
            default_branch="main",
            plans_dir="",
            commit_trailer="",
            local_dir=None,
        )
        assert "trailer" not in result.lower()


class TestBuildReviewFirstPrompt:
    def test_expands_all_four_agents_and_substitutes(self) -> None:
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        for name in (
            "quality",
            "implementation",
            "testing",
            "simplification",
        ):
            assert f"{{{{agent:{name}}}}}" not in result
        # Documentation agent must be fully removed
        assert "{{agent:documentation}}" not in result
        assert (
            "Review code changes and identify missing documentation updates."
            not in result
        )
        # Four agent expansions - count the fixed emitted sentence
        assert (
            result.count(
                "Report findings only - no positive observations."
            )
            == 4
        )
        # Variable substitution in outer prompt
        assert "{{DEFAULT_BRANCH}}" not in result
        assert "{{GOAL}}" not in result
        assert "{{PROGRESS_FILE}}" not in result
        assert "main" in result
        assert "/tmp/progress.txt" in result
        # Expected signals present
        assert "<<<CADENCE:REVIEW_DONE>>>" in result
        assert "<<<CADENCE:TASK_FAILED>>>" in result

    def test_trailer_once_when_configured(self) -> None:
        trailer = "Co-Authored-By: Bot"
        result = build_review_first_prompt(
            plan_file="",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            commit_trailer=trailer,
        )
        assert result.count(trailer) == 1

    def test_no_trailer_when_empty(self) -> None:
        result = build_review_first_prompt(
            plan_file="",
            progress_file="",
            default_branch="main",
        )
        assert "Co-Authored-By" not in result
        assert (
            "When making git commits, add the following trailer"
            not in result
        )

    def test_goal_when_no_plan_file(self) -> None:
        result = build_review_first_prompt(
            plan_file="",
            progress_file="",
            default_branch="develop",
        )
        assert "review of branch vs develop" in result

    def test_goal_when_plan_file_given(self) -> None:
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="",
            default_branch="main",
        )
        assert "implementation of plan at /tmp/plan.md" in result

    def test_local_agent_override(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "quality.txt").write_text(
            "OVERRIDE_QUALITY_BODY for {{DEFAULT_BRANCH}}"
        )
        result = build_review_first_prompt(
            plan_file="",
            progress_file="",
            default_branch="feature-branch",
            local_dir=tmp_path,
        )
        assert "OVERRIDE_QUALITY_BODY for feature-branch" in result


class TestBuildReviewSecondPrompt:
    def test_expands_two_agents(self) -> None:
        result = build_review_second_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "{{agent:quality}}" not in result
        assert "{{agent:implementation}}" not in result
        # Should NOT contain testing/simplification/documentation markers
        for name in ("testing", "simplification", "documentation"):
            assert f"{{{{agent:{name}}}}}" not in result
        assert (
            result.count(
                "Report findings only - no positive observations."
            )
            == 2
        )
        assert "<<<CADENCE:REVIEW_DONE>>>" in result


class TestBuildFinalizePrompt:
    def test_loads_and_substitutes(self) -> None:
        result = build_finalize_prompt(
            plan_file="",
            progress_file="",
            default_branch="main",
        )
        assert "{{DEFAULT_BRANCH}}" not in result
        assert "main" in result
        # No CADENCE signals expected in finalize
        assert "<<<CADENCE:" not in result

    def test_no_agent_refs(self) -> None:
        result = build_finalize_prompt(
            plan_file="",
            progress_file="",
            default_branch="main",
        )
        # Finalize has no agent markers so regex no-ops
        assert "{{agent:" not in result
        assert "Use the Task tool" not in result
