from __future__ import annotations

import io
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rlx.config import ColorConfig
from rlx.plan import (
    Checkbox,
    NoPlansFoundError,
    Selector,
    Task,
    TaskStatus,
    determine_task_status,
    extract_branch_name,
    file_has_uncompleted_checkbox,
    parse_plan,
    parse_plan_file,
)


class TestCheckboxActionability:
    def test_plain_text_is_actionable(self) -> None:
        cb = Checkbox(text="Implement foo", checked=False)
        assert cb.is_actionable() is True

    def test_text_with_empty_brackets_not_actionable(self) -> None:
        text = "Checkboxes (`- [ ]` / `- [x]`) belong only in Task sections"
        cb = Checkbox(text=text, checked=False)
        assert cb.is_actionable() is False

    def test_text_with_space_brackets_not_actionable(self) -> None:
        cb = Checkbox(text="use [ ] to mean unchecked", checked=False)
        assert cb.is_actionable() is False

    def test_text_with_x_brackets_not_actionable(self) -> None:
        cb = Checkbox(text="use [x] for done", checked=False)
        assert cb.is_actionable() is False


class TestDetermineTaskStatus:
    def test_empty_is_pending(self) -> None:
        assert determine_task_status([]) == TaskStatus.PENDING

    def test_none_checked_is_pending(self) -> None:
        cbs = [Checkbox("a", False), Checkbox("b", False)]
        assert determine_task_status(cbs) == TaskStatus.PENDING

    def test_all_checked_is_done(self) -> None:
        cbs = [Checkbox("a", True), Checkbox("b", True)]
        assert determine_task_status(cbs) == TaskStatus.DONE

    def test_partial_checked_is_active(self) -> None:
        cbs = [Checkbox("a", True), Checkbox("b", False)]
        assert determine_task_status(cbs) == TaskStatus.ACTIVE


class TestTaskHasUncompletedWork:
    def test_all_done(self) -> None:
        t = Task(1, "T", TaskStatus.DONE, [Checkbox("a", True)])
        assert t.has_uncompleted_actionable_work() is False

    def test_unchecked_actionable(self) -> None:
        t = Task(1, "T", TaskStatus.ACTIVE, [Checkbox("a", False)])
        assert t.has_uncompleted_actionable_work() is True

    def test_unchecked_not_actionable(self) -> None:
        t = Task(1, "T", TaskStatus.PENDING, [Checkbox("use [ ] for unchecked", False)])
        assert t.has_uncompleted_actionable_work() is False

    def test_mixed(self) -> None:
        t = Task(
            1,
            "T",
            TaskStatus.ACTIVE,
            [Checkbox("real work", False), Checkbox("use [x] for done", False)],
        )
        assert t.has_uncompleted_actionable_work() is True


class TestParsePlan:
    def test_title_parsed(self) -> None:
        content = "# My Plan\n\nSome content.\n"
        plan = parse_plan(content)
        assert plan.title == "My Plan"
        assert plan.tasks == []

    def test_single_task_with_checkboxes(self) -> None:
        content = (
            "# My Plan\n"
            "\n"
            "### Task 1: Setup\n"
            "- [x] Create dir\n"
            "- [ ] Add config\n"
        )
        plan = parse_plan(content)
        assert plan.title == "My Plan"
        assert len(plan.tasks) == 1
        t = plan.tasks[0]
        assert t.number == 1
        assert t.title == "Setup"
        assert t.status == TaskStatus.ACTIVE
        assert len(t.checkboxes) == 2
        assert t.checkboxes[0].checked is True
        assert t.checkboxes[1].checked is False

    def test_iteration_header(self) -> None:
        content = (
            "# Plan\n"
            "### Iteration 3: Do something\n"
            "- [ ] first\n"
        )
        plan = parse_plan(content)
        assert len(plan.tasks) == 1
        assert plan.tasks[0].number == 3
        assert plan.tasks[0].title == "Do something"

    def test_multiple_tasks(self) -> None:
        content = (
            "# Plan\n"
            "### Task 1: A\n"
            "- [x] one\n"
            "### Task 2: B\n"
            "- [ ] two\n"
        )
        plan = parse_plan(content)
        assert len(plan.tasks) == 2
        assert plan.tasks[0].status == TaskStatus.DONE
        assert plan.tasks[1].status == TaskStatus.PENDING

    def test_h2_closes_task(self) -> None:
        content = (
            "# Plan\n"
            "### Task 1: A\n"
            "- [ ] inside task\n"
            "## Success criteria\n"
            "- [ ] outside task\n"
        )
        plan = parse_plan(content)
        assert len(plan.tasks) == 1
        assert len(plan.tasks[0].checkboxes) == 1
        assert plan.tasks[0].checkboxes[0].text == "inside task"

    def test_h1_after_title_closes_task(self) -> None:
        content = (
            "# Plan\n"
            "### Task 1: A\n"
            "- [ ] inside\n"
            "# Another top\n"
            "- [ ] outside\n"
        )
        plan = parse_plan(content)
        assert len(plan.tasks) == 1
        assert len(plan.tasks[0].checkboxes) == 1

    def test_h3_subsection_does_not_close(self) -> None:
        content = (
            "# Plan\n"
            "### Task 1: A\n"
            "- [ ] a\n"
            "### Subsection not a task\n"
        )
        plan = parse_plan(content)
        assert len(plan.tasks) == 1

    def test_h4_does_not_close(self) -> None:
        content = (
            "# Plan\n"
            "### Task 1: A\n"
            "- [ ] a\n"
            "#### sub\n"
            "- [ ] b\n"
        )
        plan = parse_plan(content)
        assert len(plan.tasks) == 1
        assert len(plan.tasks[0].checkboxes) == 2

    def test_indented_checkbox(self) -> None:
        content = (
            "# Plan\n"
            "### Task 1: A\n"
            "  - [ ] indented\n"
        )
        plan = parse_plan(content)
        assert len(plan.tasks[0].checkboxes) == 1
        assert plan.tasks[0].checkboxes[0].text == "indented"

    def test_capital_x_checked(self) -> None:
        content = "# P\n### Task 1: A\n- [X] yes\n"
        plan = parse_plan(content)
        assert plan.tasks[0].checkboxes[0].checked is True

    def test_no_title(self) -> None:
        content = "### Task 1: A\n- [ ] a\n"
        plan = parse_plan(content)
        assert plan.title == ""
        assert len(plan.tasks) == 1

    def test_checkboxes_outside_task_ignored(self) -> None:
        content = "# Plan\n- [ ] before any task\n### Task 1: A\n- [ ] inside\n"
        plan = parse_plan(content)
        assert len(plan.tasks) == 1
        assert len(plan.tasks[0].checkboxes) == 1

    def test_non_numeric_task_num_becomes_zero(self) -> None:
        content = "# P\n### Task abc: title\n- [ ] x\n"
        plan = parse_plan(content)
        assert plan.tasks[0].number == 0


class TestParsePlanFile:
    def test_reads_file(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("# Title\n### Task 1: A\n- [ ] x\n")
        plan = parse_plan_file(str(f))
        assert plan.title == "Title"
        assert len(plan.tasks) == 1


class TestFileHasUncompletedCheckbox:
    def test_finds_unchecked(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("# P\nrandom text\n- [ ] todo\n")
        assert file_has_uncompleted_checkbox(str(f)) is True

    def test_all_checked(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("- [x] done\n- [X] also done\n")
        assert file_has_uncompleted_checkbox(str(f)) is False

    def test_ignores_format_description(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("- [ ] use [ ] for empty\n")
        assert file_has_uncompleted_checkbox(str(f)) is False

    def test_no_checkboxes(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("# Plan\nSome text.\n")
        assert file_has_uncompleted_checkbox(str(f)) is False

    def test_missing_file(self, tmp_path: Path) -> None:
        assert file_has_uncompleted_checkbox(str(tmp_path / "nope.md")) is False


class TestExtractBranchName:
    def test_plain_name(self) -> None:
        assert extract_branch_name("feature-login.md") == "feature-login"

    def test_strips_date_prefix(self) -> None:
        assert extract_branch_name("2024-01-15-auth-refactor.md") == "auth-refactor"

    def test_only_date_falls_back(self) -> None:
        assert extract_branch_name("2024-01-15.md") == "2024-01-15"

    def test_handles_path(self) -> None:
        assert extract_branch_name("/tmp/plans/2026-04-25-my-feature.md") == "my-feature"

    def test_no_extension(self) -> None:
        assert extract_branch_name("2024-01-15-foo") == "foo"


class TestSelector:
    def _make(
        self, plans_dir: str, input_text: str = ""
    ) -> tuple[Selector, io.StringIO]:
        stdout = io.StringIO()
        stdin = io.StringIO(input_text)
        sel = Selector(plans_dir, ColorConfig(), stdin=stdin, stdout=stdout)
        return sel, stdout

    def test_select_with_explicit_file(self, tmp_path: Path) -> None:
        f = tmp_path / "plan.md"
        f.write_text("x")
        sel, _ = self._make(str(tmp_path))
        result = sel.select(str(f), optional=False)
        assert result == str(f.resolve())

    def test_select_missing_file_raises(self, tmp_path: Path) -> None:
        sel, _ = self._make(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            sel.select(str(tmp_path / "nope.md"), optional=False)

    def test_select_optional_empty_returns_empty(self, tmp_path: Path) -> None:
        sel, _ = self._make(str(tmp_path))
        assert sel.select("", optional=True) == ""

    def test_select_numbered_no_plans_raises(self, tmp_path: Path) -> None:
        sel, _ = self._make(str(tmp_path))
        with pytest.raises(NoPlansFoundError):
            sel.select("", optional=False)

    def test_select_missing_plans_dir_raises(self, tmp_path: Path) -> None:
        sel, _ = self._make(str(tmp_path / "nope"))
        with pytest.raises(NoPlansFoundError):
            sel.select("", optional=False)

    def test_select_single_file_auto(self, tmp_path: Path) -> None:
        f = tmp_path / "only.md"
        f.write_text("x")
        sel, _ = self._make(str(tmp_path))
        result = sel.select("", optional=False)
        assert result == str(f.resolve())

    def test_select_multiple_picks_by_number(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("x")
        (tmp_path / "b.md").write_text("x")
        sel, _ = self._make(str(tmp_path), input_text="2\n")
        result = sel.select("", optional=False)
        assert result == str((tmp_path / "b.md").resolve())

    def test_select_multiple_invalid_then_valid(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("x")
        (tmp_path / "b.md").write_text("x")
        sel, _ = self._make(str(tmp_path), input_text="abc\n99\n1\n")
        result = sel.select("", optional=False)
        assert result == str((tmp_path / "a.md").resolve())

    def test_select_multiple_eof_raises(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("x")
        (tmp_path / "b.md").write_text("x")
        sel, _ = self._make(str(tmp_path), input_text="")
        with pytest.raises(RuntimeError):
            sel.select("", optional=False)

    def test_find_recent_returns_newest_after(self, tmp_path: Path) -> None:
        old = tmp_path / "old.md"
        old.write_text("x")
        past = time.time() - 1000
        os.utime(old, (past, past))

        start = datetime.now() - timedelta(seconds=5)
        new = tmp_path / "new.md"
        new.write_text("x")
        future = time.time() + 2
        os.utime(new, (future, future))

        sel, _ = self._make(str(tmp_path))
        assert sel.find_recent(start) == str(new.resolve())

    def test_find_recent_missing_dir_empty(self, tmp_path: Path) -> None:
        sel, _ = self._make(str(tmp_path / "nope"))
        assert sel.find_recent(datetime.now()) == ""

    def test_find_recent_none_after_start(self, tmp_path: Path) -> None:
        f = tmp_path / "a.md"
        f.write_text("x")
        past = time.time() - 1000
        os.utime(f, (past, past))
        sel, _ = self._make(str(tmp_path))
        assert sel.find_recent(datetime.now()) == ""

    def test_select_numbered_excludes_completed(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("x")
        (tmp_path / "b-completed.md").write_text("x")
        sel, _ = self._make(str(tmp_path))
        result = sel.select("", optional=False)
        assert result == str((tmp_path / "a.md").resolve())

    def test_select_numbered_only_completed_raises(self, tmp_path: Path) -> None:
        (tmp_path / "a-completed.md").write_text("x")
        sel, _ = self._make(str(tmp_path))
        with pytest.raises(NoPlansFoundError):
            sel.select("", optional=False)

    def test_select_numbered_picker_hides_completed(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("x")
        (tmp_path / "b.md").write_text("x")
        (tmp_path / "c-completed.md").write_text("x")
        sel, stdout = self._make(str(tmp_path), input_text="2\n")
        result = sel.select("", optional=False)
        assert result == str((tmp_path / "b.md").resolve())
        assert "c-completed.md" not in stdout.getvalue()

    def test_find_recent_skips_completed(self, tmp_path: Path) -> None:
        start = datetime.now() - timedelta(seconds=5)
        plain = tmp_path / "plain.md"
        plain.write_text("x")
        completed = tmp_path / "plan-completed.md"
        completed.write_text("x")
        future = time.time() + 2
        os.utime(plain, (future - 1, future - 1))
        os.utime(completed, (future, future))
        sel, _ = self._make(str(tmp_path))
        assert sel.find_recent(start) == str(plain.resolve())
