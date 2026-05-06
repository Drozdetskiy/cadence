from __future__ import annotations

from pathlib import Path

import pytest

from cadence.processor.prompts import (
    COMMIT_FORMAT_SENTINEL,
    append_commit_format_instruction,
    build_plan_prompt,
    build_report_api_changes_prompt,
    build_report_test_cases_prompt,
    build_review_first_prompt,
    build_review_second_prompt,
    build_squash_commit_prompt,
    build_task_prompt,
    expand_agent_references,
    format_agent_expansion,
    load_context_files,
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
        assert "cadence" in message
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
            "squash_commit",
            "report_api_changes",
        ],
    )
    def test_all_shipped_prompts_load(self, name: str) -> None:
        prompt = load_prompt(name)
        assert prompt.strip(), f"shipped prompt {name!r} loaded but is empty"


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
            "Custom task: {{PLAN_FILE}} goal={{GOAL}} branch={{DEFAULT_BRANCH}}"
        )
        result = build_task_prompt(
            plan_file="/tmp/p.md",
            progress_file="/tmp/pr.txt",
            default_branch="develop",
            local_dir=tmp_path,
        )
        assert result == (
            "Custom task: /tmp/p.md goal=implementation of plan at /tmp/p.md branch=develop"
        )


class TestBuildPlanPrompt:
    def test_does_not_include_documentation_update_task(self) -> None:
        result = build_plan_prompt(
            plan_description="add a feature",
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            derived_plan_path="/tmp/derived.md",
        )
        assert "Update documentation" not in result
        assert "update README.md" not in result
        assert "update CLAUDE.md" not in result

    def test_includes_accepted_tradeoffs_section(self) -> None:
        result = build_plan_prompt(
            plan_description="add a feature",
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            derived_plan_path="/tmp/derived.md",
        )
        assert "## Accepted Trade-offs" in result

    def test_includes_out_of_scope_section(self) -> None:
        result = build_plan_prompt(
            plan_description="add a feature",
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            derived_plan_path="/tmp/derived.md",
        )
        assert "## Out of Scope" in result

    def test_includes_none_guidance_bullet(self) -> None:
        result = build_plan_prompt(
            plan_description="add a feature",
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            derived_plan_path="/tmp/derived.md",
        )
        assert "- None — " in result

    def test_validation_block_lists_protected_categories(self) -> None:
        result = build_plan_prompt(
            plan_description="add a feature",
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            derived_plan_path="/tmp/derived.md",
        )
        assert "Decision Surface" in result
        for keyword in (
            "logic bugs",
            "security vulnerabilities",
            "data loss",
            "missing tests",
            "failing tests",
            "failing linter",
            "regressions",
        ):
            assert keyword in result, f"protected category keyword missing: {keyword!r}"

    def test_sections_appear_between_context_and_development_approach(self) -> None:
        result = build_plan_prompt(
            plan_description="add a feature",
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            derived_plan_path="/tmp/derived.md",
        )
        ctx = result.index("## Context")
        accepted = result.index("## Accepted Trade-offs")
        out_of_scope = result.index("## Out of Scope")
        approach = result.index("## Development Approach")
        assert ctx < accepted < out_of_scope < approach


class TestFormatAgentExpansion:
    def test_with_model(self) -> None:
        out = format_agent_expansion("hello body", model="sonnet", agent_type="general-purpose")
        assert "Use the Task tool with model=sonnet" in out
        assert "launch a general-purpose agent" in out
        assert "<<<AGENT_PROMPT BEGIN>>>\nhello body\n<<<AGENT_PROMPT END>>>" in out
        assert "Report findings only - no positive observations." in out

    def test_body_with_quotes_preserved(self) -> None:
        body = "has \"double\" and 'single' quotes"
        out = format_agent_expansion(body, model="", agent_type="general-purpose")
        assert body in out

    def test_without_model(self) -> None:
        out = format_agent_expansion("body", model="", agent_type="code-reviewer")
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
            "not-a-real-agent" in w and "reinstall" in w and "pip install" in w for w in warnings
        ), "expected the load_agent diagnostic to be forwarded to the warn callback, not swallowed"

    def test_recursion_guard(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "parent.txt").write_text("outer body has {{agent:child}} inside")
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
        (agents_dir / "quality.txt").write_text("review branch {{DEFAULT_BRANCH}} goal {{GOAL}}")
        result = expand_agent_references(
            "{{agent:quality}}",
            local_dir=tmp_path,
            warn=None,
            base_vars={
                "plan_file": "",
                "progress_file": "",
                "goal": "custom goal",
                "default_branch": "develop",
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
            commit_trailer=trailer,
            local_dir=None,
        )
        assert result.count(trailer) == 1
        agent_start = result.index("Use the Task tool")
        agent_end = result.index("Report findings only - no positive observations.")
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
            commit_trailer="",
            local_dir=None,
        )
        assert "trailer" not in result.lower()

    def test_format_appended_after_trailer(self) -> None:
        trailer = "Co-Authored-By: Bot"
        prompt = "x"
        result = replace_prompt_variables(
            prompt,
            plan_file="",
            progress_file="",
            goal="",
            default_branch="main",
            commit_trailer=trailer,
            local_dir=None,
            commit_format="CUSTOM_FMT_BODY",
        )
        assert result.count(trailer) == 1
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.index(trailer) < result.index(COMMIT_FORMAT_SENTINEL)
        # Format block must remain at the very end of the prompt — Claude reads
        # it as the authoritative commit-message spec, so nothing should sit
        # between the sentinel/body and EOF.
        assert result.rstrip().endswith("CUSTOM_FMT_BODY")


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
        assert "Review code changes and identify missing documentation updates." not in result
        # Four agent expansions - count the fixed emitted sentence
        assert result.count("Report findings only - no positive observations.") == 4
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
        assert "When making git commits, add the following trailer" not in result

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
        (agents_dir / "quality.txt").write_text("OVERRIDE_QUALITY_BODY for {{DEFAULT_BRANCH}}")
        result = build_review_first_prompt(
            plan_file="",
            progress_file="",
            default_branch="feature-branch",
            local_dir=tmp_path,
        )
        assert "OVERRIDE_QUALITY_BODY for feature-branch" in result

    def test_does_not_list_docs_as_issue_category(self) -> None:
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert ", docs," not in result

    def test_includes_decision_surface_section_headings(self) -> None:
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "## Accepted Trade-offs" in result
        assert "## Out of Scope" in result

    def test_includes_accepted_in_plan_classification(self) -> None:
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "ACCEPTED-IN-PLAN" in result

    def test_includes_protected_category_keywords(self) -> None:
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        anchor = "Protected categories that can NEVER be silenced this way:"
        assert anchor in result
        paragraph = result[result.index(anchor) :].split('"', 1)[0]
        for keyword in (
            "security",
            "data loss",
            "regressions",
            "failing tests",
            "failing linter",
            "missing tests",
        ):
            assert keyword in paragraph, (
                f"protected category keyword missing from agent-injection paragraph: {keyword!r}"
            )

    def test_empty_plan_file_renders_no_plan_placeholder(self) -> None:
        with_plan = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        without_plan = build_review_first_prompt(
            plan_file="",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "Plan file: (no plan file - reviewing current branch)" in without_plan
        assert "Plan file: /tmp/plan.md" in with_plan
        assert "Plan file: /tmp/plan.md" not in without_plan

    def test_load_plan_decision_surface_step_present(self) -> None:
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "Load Plan Decision Surface" in result


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
        assert result.count("Report findings only - no positive observations.") == 2
        assert "<<<CADENCE:REVIEW_DONE>>>" in result

    def test_includes_decision_surface_section_headings(self) -> None:
        result = build_review_second_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "## Accepted Trade-offs" in result
        assert "## Out of Scope" in result

    def test_includes_accepted_in_plan_classification(self) -> None:
        result = build_review_second_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "ACCEPTED-IN-PLAN" in result

    def test_includes_protected_category_keywords(self) -> None:
        result = build_review_second_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        anchor = "Protected categories that can NEVER be silenced this way:"
        assert anchor in result
        paragraph = result[result.index(anchor) :].split('"', 1)[0]
        for keyword in (
            "security",
            "data loss",
            "regressions",
            "failing tests",
            "failing linter",
            "missing tests",
        ):
            assert keyword in paragraph, (
                f"protected category keyword missing from agent-injection paragraph: {keyword!r}"
            )

    def test_empty_plan_file_renders_no_plan_placeholder(self) -> None:
        with_plan = build_review_second_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        without_plan = build_review_second_prompt(
            plan_file="",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "Plan file: (no plan file - reviewing current branch)" in without_plan
        assert "Plan file: /tmp/plan.md" in with_plan
        assert "Plan file: /tmp/plan.md" not in without_plan

    def test_load_plan_decision_surface_step_present(self) -> None:
        result = build_review_second_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert "Load Plan Decision Surface" in result


class TestAppendCommitFormatInstruction:
    def test_empty_format_unchanged(self) -> None:
        assert append_commit_format_instruction("prompt", "") == "prompt"

    def test_appends_sentinel_and_body(self) -> None:
        result = append_commit_format_instruction("prompt", "BODY_X")
        assert COMMIT_FORMAT_SENTINEL in result
        assert "BODY_X" in result
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.count("BODY_X") == 1


class TestCommitFormatInBuilders:
    def test_task_prompt_no_format_when_empty(self) -> None:
        result = build_task_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
        )
        assert COMMIT_FORMAT_SENTINEL not in result

    def test_task_prompt_injects_custom_format(self) -> None:
        result = build_task_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            commit_format="CUSTOM_FMT_BODY",
        )
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.count("CUSTOM_FMT_BODY") == 1

    def test_review_first_prompt_no_format_when_empty(self) -> None:
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert COMMIT_FORMAT_SENTINEL not in result

    def test_review_first_prompt_injects_custom_format(self) -> None:
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            commit_format="CUSTOM_FMT_BODY",
        )
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.count("CUSTOM_FMT_BODY") == 1

    def test_review_second_prompt_no_format_when_empty(self) -> None:
        result = build_review_second_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
        )
        assert COMMIT_FORMAT_SENTINEL not in result

    def test_review_second_prompt_injects_custom_format(self) -> None:
        result = build_review_second_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            commit_format="CUSTOM_FMT_BODY",
        )
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.count("CUSTOM_FMT_BODY") == 1

    def test_trailer_and_format_both_appear_once(self) -> None:
        trailer = "Co-Authored-By: Bot"
        result = build_task_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            commit_trailer=trailer,
            commit_format="CUSTOM_FMT_BODY",
        )
        assert result.count(trailer) == 1
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.count("CUSTOM_FMT_BODY") == 1

    def test_review_first_trailer_and_format_both_appear_once(self) -> None:
        trailer = "Co-Authored-By: Bot"
        result = build_review_first_prompt(
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            commit_trailer=trailer,
            commit_format="CUSTOM_FMT_BODY",
        )
        assert result.count(trailer) == 1
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.count("CUSTOM_FMT_BODY") == 1


class TestNoHardcodedCommitExamplesInDefaults:
    def test_task_prompt_drops_feat_example(self) -> None:
        for commit_format in ("", "CUSTOM_FMT_BODY"):
            result = build_task_prompt(
                plan_file="/tmp/plan.md",
                progress_file="/tmp/progress.txt",
                commit_format=commit_format,
            )
            assert "feat: <brief task description>" not in result

    def test_review_first_prompt_drops_fix_example(self) -> None:
        for commit_format in ("", "CUSTOM_FMT_BODY"):
            result = build_review_first_prompt(
                plan_file="/tmp/plan.md",
                progress_file="/tmp/progress.txt",
                default_branch="main",
                commit_format=commit_format,
            )
            assert "fix: address code review findings" not in result

    def test_review_second_prompt_drops_fix_example(self) -> None:
        for commit_format in ("", "CUSTOM_FMT_BODY"):
            result = build_review_second_prompt(
                plan_file="/tmp/plan.md",
                progress_file="/tmp/progress.txt",
                default_branch="main",
                commit_format=commit_format,
            )
            assert "fix: address code review findings" not in result


class TestBuildSquashCommitPrompt:
    def test_substitutes_default_branch(self) -> None:
        result = build_squash_commit_prompt(default_branch="develop")
        assert "develop" in result
        assert "{{DEFAULT_BRANCH}}" not in result

    def test_default_branch_falls_back_to_main(self) -> None:
        result = build_squash_commit_prompt()
        assert "main" in result
        assert "{{DEFAULT_BRANCH}}" not in result

    def test_includes_begin_end_markers(self) -> None:
        result = build_squash_commit_prompt(default_branch="main")
        assert "<<<CADENCE:COMMIT_MSG_BEGIN>>>" in result
        assert "<<<CADENCE:COMMIT_MSG_END>>>" in result

    def test_does_not_inject_trailer_instruction(self) -> None:
        # The squash trailer is appended by Service.squash_commits, not the
        # prompt — including it here would yield a duplicated trailer.
        result = build_squash_commit_prompt(default_branch="main")
        assert "When making git commits, add the following trailer" not in result

    def test_appends_commit_format_block(self) -> None:
        result = build_squash_commit_prompt(
            default_branch="main",
            commit_format="CUSTOM_FMT_BODY",
        )
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.count("CUSTOM_FMT_BODY") == 1
        assert result.rstrip().endswith("CUSTOM_FMT_BODY")

    def test_no_format_when_empty(self) -> None:
        result = build_squash_commit_prompt(default_branch="main")
        assert COMMIT_FORMAT_SENTINEL not in result

    def test_local_override(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "squash_commit.txt").write_text("Custom squash for {{DEFAULT_BRANCH}}")
        result = build_squash_commit_prompt(
            default_branch="develop",
            local_dir=tmp_path,
        )
        assert result == "Custom squash for develop"


class TestLoadContextFiles:
    def test_returns_empty_when_local_dir_is_none(self) -> None:
        assert load_context_files(None) == ""

    def test_returns_empty_when_context_dir_missing(self, tmp_path: Path) -> None:
        assert load_context_files(tmp_path) == ""

    def test_returns_empty_when_context_dir_is_a_file(self, tmp_path: Path) -> None:
        (tmp_path / "context").write_text("oops")
        assert load_context_files(tmp_path) == ""

    def test_returns_empty_when_context_dir_empty(self, tmp_path: Path) -> None:
        (tmp_path / "context").mkdir()
        assert load_context_files(tmp_path) == ""

    def test_includes_two_files_in_sorted_order(self, tmp_path: Path) -> None:
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "b.txt").write_text("BBBB")
        (ctx / "a.md").write_text("AAAA")
        result = load_context_files(tmp_path)
        assert result == "# Project context\n\n## a.md\nAAAA\n\n## b.txt\nBBBB\n\n"

    def test_skips_disallowed_extensions(self, tmp_path: Path) -> None:
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "ok.md").write_text("ok")
        (ctx / "skipme.png").write_bytes(b"\x89PNG")
        (ctx / "no_ext").write_text("nope")
        result = load_context_files(tmp_path)
        assert "ok.md" in result
        assert "skipme.png" not in result
        assert "no_ext" not in result

    def test_ignores_subdirectories(self, tmp_path: Path) -> None:
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "ok.md").write_text("ok")
        sub = ctx / "nested"
        sub.mkdir()
        (sub / "deep.md").write_text("deep")
        result = load_context_files(tmp_path)
        assert "ok.md" in result
        assert "deep.md" not in result
        assert "nested" not in result

    def test_includes_all_allowed_extensions(self, tmp_path: Path) -> None:
        ctx = tmp_path / "context"
        ctx.mkdir()
        for name in (
            "x.md",
            "x.txt",
            "x.sql",
            "x.yaml",
            "x.yml",
            "x.json",
            "x.proto",
        ):
            (ctx / name).write_text(f"body-{name}")
        result = load_context_files(tmp_path)
        for name in (
            "x.md",
            "x.txt",
            "x.sql",
            "x.yaml",
            "x.yml",
            "x.json",
            "x.proto",
        ):
            assert f"## {name}\nbody-{name}" in result

    def test_drops_files_over_byte_cap_and_warns_once(self, tmp_path: Path) -> None:
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "a.txt").write_text("A" * 100)
        (ctx / "b.txt").write_text("B" * 100)
        (ctx / "c.txt").write_text("C" * 100)
        warnings: list[str] = []
        result = load_context_files(tmp_path, max_bytes=150, warn=warnings.append)
        assert "## a.txt\n" + ("A" * 100) in result
        assert "b.txt" not in result
        assert "c.txt" not in result
        assert len(warnings) == 1
        assert "2" in warnings[0]
        assert "150" in warnings[0]

    def test_no_warning_when_nothing_skipped(self, tmp_path: Path) -> None:
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "a.md").write_text("hi")
        warnings: list[str] = []
        load_context_files(tmp_path, warn=warnings.append)
        assert warnings == []

    def test_warn_callable_optional_when_skipping(self, tmp_path: Path) -> None:
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "a.txt").write_text("X" * 1000)
        # No warn callable; must not raise even though we skip a file.
        result = load_context_files(tmp_path, max_bytes=10)
        assert result == ""

    def test_handles_invalid_utf8_via_replace(self, tmp_path: Path) -> None:
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "bad.txt").write_bytes(b"hello \xff world")
        result = load_context_files(tmp_path)
        assert "## bad.txt" in result
        assert "hello" in result and "world" in result


class TestBuildReportApiChangesPrompt:
    def _write_template(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "report_api_changes.txt").write_text(
            "branch={{BRANCH}}\n"
            "base={{DEFAULT_BRANCH}}\n"
            "progress={{PROGRESS_FILE}}\n"
            "paths:\n{{PUBLIC_API_PATHS}}\n"
            "context:\n{{PROJECT_CONTEXT}}\n"
        )

    def test_substitutes_all_variables(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_api_changes_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
            public_api_paths=["src/api", "src/rpc"],
            progress_file="/tmp/progress.txt",
        )
        assert "branch=feature-x" in result
        assert "base=main" in result
        assert "progress=/tmp/progress.txt" in result
        assert "paths:\n- src/api\n- src/rpc" in result
        # No leftover placeholders.
        for token in (
            "{{BRANCH}}",
            "{{DEFAULT_BRANCH}}",
            "{{PROGRESS_FILE}}",
            "{{PUBLIC_API_PATHS}}",
            "{{PROJECT_CONTEXT}}",
        ):
            assert token not in result

    def test_empty_public_api_paths_renders_inference_fallback(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_api_changes_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
            public_api_paths=[],
        )
        assert "paths:\n(infer from project structure)" in result

    def test_default_branch_falls_back_to_main(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_api_changes_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="",
            public_api_paths=[],
        )
        assert "base=main" in result

    def test_injects_project_context_when_present(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "openapi.yaml").write_text("openapi: 3.0.0")
        result = build_report_api_changes_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
            public_api_paths=[],
        )
        assert "# Project context" in result
        assert "## openapi.yaml" in result
        assert "openapi: 3.0.0" in result

    def test_no_project_context_block_when_dir_absent(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_api_changes_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
            public_api_paths=[],
        )
        assert "# Project context" not in result
        # Trailing block becomes empty.
        assert result.endswith("context:\n\n")

    def test_no_agent_expansion(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "report_api_changes.txt").write_text("marker {{agent:quality}} end")
        result = build_report_api_changes_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
            public_api_paths=[],
        )
        assert "{{agent:quality}}" in result
        assert "Use the Task tool" not in result

    def test_no_commit_format_block_by_default(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_api_changes_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
            public_api_paths=[],
        )
        assert COMMIT_FORMAT_SENTINEL not in result

    def test_appends_commit_format_when_provided(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_api_changes_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
            public_api_paths=[],
            commit_format="CUSTOM_FMT_BODY",
        )
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.rstrip().endswith("CUSTOM_FMT_BODY")


class TestDefaultReportApiChangesPrompt:
    def test_loads_and_has_required_placeholders(self) -> None:
        prompt = load_prompt("report_api_changes")
        for token in (
            "{{BRANCH}}",
            "{{DEFAULT_BRANCH}}",
            "{{PUBLIC_API_PATHS}}",
            "{{PROJECT_CONTEXT}}",
        ):
            assert token in prompt, f"missing placeholder {token!r} in default prompt"

    def test_loads_and_has_required_signal_names(self) -> None:
        prompt = load_prompt("report_api_changes")
        assert "<<<CADENCE:REPORT_BEGIN>>>" in prompt
        assert "<<<CADENCE:REPORT_END>>>" in prompt
        assert "<<<CADENCE:REPORT_DONE>>>" in prompt
        assert "<<<CADENCE:REPORT_FAILED>>>" in prompt


class TestBuildReportTestCasesPrompt:
    def _write_template(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "report_test_cases.txt").write_text(
            "branch={{BRANCH}}\n"
            "base={{DEFAULT_BRANCH}}\n"
            "progress={{PROGRESS_FILE}}\n"
            "context:\n{{PROJECT_CONTEXT}}\n"
        )

    def test_substitutes_all_variables(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_test_cases_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
            progress_file="/tmp/progress.txt",
        )
        assert "branch=feature-x" in result
        assert "base=main" in result
        assert "progress=/tmp/progress.txt" in result
        for token in (
            "{{BRANCH}}",
            "{{DEFAULT_BRANCH}}",
            "{{PROGRESS_FILE}}",
            "{{PROJECT_CONTEXT}}",
        ):
            assert token not in result

    def test_default_branch_falls_back_to_main(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_test_cases_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="",
        )
        assert "base=main" in result

    def test_injects_project_context_when_present(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "schema.sql").write_text("CREATE TABLE users (id INT);")
        result = build_report_test_cases_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
        )
        assert "# Project context" in result
        assert "## schema.sql" in result
        assert "CREATE TABLE users (id INT);" in result

    def test_no_project_context_block_when_dir_absent(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_test_cases_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
        )
        assert "# Project context" not in result
        assert result.endswith("context:\n\n")

    def test_no_agent_expansion(self, tmp_path: Path) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "report_test_cases.txt").write_text("marker {{agent:quality}} end")
        result = build_report_test_cases_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
        )
        assert "{{agent:quality}}" in result
        assert "Use the Task tool" not in result

    def test_no_commit_format_block_by_default(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_test_cases_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
        )
        assert COMMIT_FORMAT_SENTINEL not in result

    def test_appends_commit_format_when_provided(self, tmp_path: Path) -> None:
        self._write_template(tmp_path)
        result = build_report_test_cases_prompt(
            local_dir=tmp_path,
            branch="feature-x",
            default_branch="main",
            commit_format="CUSTOM_FMT_BODY",
        )
        assert result.count(COMMIT_FORMAT_SENTINEL) == 1
        assert result.rstrip().endswith("CUSTOM_FMT_BODY")


class TestDefaultReportTestCasesPrompt:
    def test_loads_and_has_required_placeholders(self) -> None:
        prompt = load_prompt("report_test_cases")
        for token in (
            "{{BRANCH}}",
            "{{DEFAULT_BRANCH}}",
            "{{PROGRESS_FILE}}",
            "{{PROJECT_CONTEXT}}",
        ):
            assert token in prompt, f"missing placeholder {token!r} in default prompt"

    def test_loads_and_has_required_signal_names(self) -> None:
        prompt = load_prompt("report_test_cases")
        assert "<<<CADENCE:REPORT_BEGIN>>>" in prompt
        assert "<<<CADENCE:REPORT_END>>>" in prompt
        assert "<<<CADENCE:REPORT_DONE>>>" in prompt
        assert "<<<CADENCE:REPORT_FAILED>>>" in prompt

    def test_loads_and_has_required_tc_field_labels(self) -> None:
        prompt = load_prompt("report_test_cases")
        for label in (
            "**Type:**",
            "**Priority:**",
            "**Preconditions:**",
            "**Steps:**",
            "**Expected:**",
            "**DB verification:**",
            "**DB setup hint:**",
        ):
            assert label in prompt, f"missing TC field label {label!r} in default prompt"

    def test_loads_and_has_required_db_context_warning(self) -> None:
        prompt = load_prompt("report_test_cases")
        assert (
            "Note: no DB schema context found in .cadence/context/. "
            "DB verification and setup sections were omitted. "
            "To enable them, add a schema dump or entity description to .cadence/context/."
        ) in prompt


class TestPlanPromptHasNoCommitFormat:
    def test_plan_prompt_does_not_inject_commit_format_block(self) -> None:
        # build_plan_prompt does not accept commit_format; the sentinel must
        # never leak into the plan prompt.
        result = build_plan_prompt(
            plan_description="add a feature",
            plan_file="/tmp/plan.md",
            progress_file="/tmp/progress.txt",
            default_branch="main",
            derived_plan_path="/tmp/derived.md",
        )
        assert COMMIT_FORMAT_SENTINEL not in result
