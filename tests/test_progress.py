from __future__ import annotations

import os

import pytest

from cadence.cli import compute_progress_path
from cadence.config import ColorConfig
from cadence.progress.colors import Colors
from cadence.progress.flock import lock_file, unlock_file
from cadence.progress.logger import (
    Logger,
    ProgressLoggerConfig,
    _is_progress_completed,
    sanitize_plan_name,
)
from cadence.status import (
    Mode,
    PhaseHolder,
    PhasePlan,
    PhaseReview,
    PhaseTask,
    Section,
)


class TestColors:
    def test_default_colors_create(self) -> None:
        colors = Colors(ColorConfig())
        assert colors.for_phase(PhaseTask) is not None
        assert colors.timestamp() is not None

    def test_for_phase_task(self) -> None:
        colors = Colors(ColorConfig())
        style = colors.for_phase(PhaseTask)
        assert style == colors.for_phase(PhasePlan)

    def test_for_phase_review(self) -> None:
        colors = Colors(ColorConfig())
        review_style = colors.for_phase(PhaseReview)
        task_style = colors.for_phase(PhaseTask)
        assert review_style != task_style

    def test_for_phase_unknown_falls_back(self) -> None:
        colors = Colors(ColorConfig())
        style = colors.for_phase("unknown_phase")
        assert style == colors.for_phase(PhaseTask)

    def test_accessors(self) -> None:
        colors = Colors(ColorConfig())
        assert colors.warn() is not None
        assert colors.error() is not None
        assert colors.signal() is not None
        assert colors.info() is not None

    def test_custom_hex(self) -> None:
        cfg = ColorConfig(task="#ff0000", review="#00ff00")
        colors = Colors(cfg)
        task_style = colors.for_phase(PhaseTask)
        review_style = colors.for_phase(PhaseReview)
        assert task_style != review_style


class TestFlock:
    def test_lock_unlock(self, tmp_path: object) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            path = f.name
        try:
            with open(path, "w") as f:
                lock_file(f)
                unlock_file(f)
        finally:
            os.unlink(path)


class TestComputeProgressPath:
    def test_plan_mode_with_plan_file(self) -> None:
        path = compute_progress_path(Mode.PLAN, plan_file="some/dir/plan.md")
        assert path == os.path.join("some/dir", "progress-plan.txt")

    def test_plan_mode_plan_file_no_dir(self) -> None:
        path = compute_progress_path(Mode.PLAN, plan_file="plan.md")
        assert path == os.path.join(".", "progress-plan.txt")

    def test_plan_mode_no_plan_file_raises(self) -> None:
        with pytest.raises(RuntimeError):
            compute_progress_path(Mode.PLAN)

    def test_full_mode_with_plan_file(self) -> None:
        path = compute_progress_path(Mode.FULL, plan_file="some/dir/plan.md")
        assert path == os.path.join("some/dir", "progress-task.txt")

    def test_full_mode_no_plan_file_raises(self) -> None:
        with pytest.raises(RuntimeError):
            compute_progress_path(Mode.FULL)

    def test_review_mode_feature_branch(self) -> None:
        path = compute_progress_path(
            Mode.REVIEW,
            branch="feat/foo",
            default_branch="main",
            tasks_root="cdc-tasks",
        )
        assert path == os.path.join("cdc-tasks", "feat-foo", "progress-review.txt")

    def test_review_mode_default_branch_uses_hash(self) -> None:
        path = compute_progress_path(
            Mode.REVIEW,
            branch="main",
            default_branch="main",
            head_hash="abc1234",
            tasks_root="cdc-tasks",
        )
        assert path == os.path.join("cdc-tasks", "abc1234", "progress-review.txt")

    def test_review_mode_default_branch_with_origin_prefix_uses_hash(self) -> None:
        path = compute_progress_path(
            Mode.REVIEW,
            branch="main",
            default_branch="origin/main",
            head_hash="abc1234def567",
            tasks_root="cdc-tasks",
        )
        assert path == os.path.join("cdc-tasks", "abc1234def56", "progress-review.txt")

    def test_review_mode_full_length_hash_truncated_to_12(self) -> None:
        path = compute_progress_path(
            Mode.REVIEW,
            branch="",
            default_branch="main",
            head_hash="0123456789abcdef0123456789abcdef01234567",
            tasks_root="cdc-tasks",
        )
        assert path == os.path.join("cdc-tasks", "0123456789ab", "progress-review.txt")

    def test_review_mode_detached_head_uses_hash(self) -> None:
        path = compute_progress_path(
            Mode.REVIEW,
            branch="",
            default_branch="main",
            head_hash="def5678",
            tasks_root="cdc-tasks",
        )
        assert path == os.path.join("cdc-tasks", "def5678", "progress-review.txt")

    def test_review_mode_custom_tasks_root(self) -> None:
        path = compute_progress_path(
            Mode.REVIEW,
            branch="feature-x",
            default_branch="main",
            tasks_root="my-tasks",
        )
        assert path == os.path.join("my-tasks", "feature-x", "progress-review.txt")

    def test_review_mode_no_branch_no_hash_raises(self) -> None:
        with pytest.raises(RuntimeError):
            compute_progress_path(Mode.REVIEW, branch="", default_branch="main", head_hash="")


class TestSanitizePlanName:
    def test_basic(self) -> None:
        assert sanitize_plan_name("Hello World") == "hello-world"

    def test_special_chars(self) -> None:
        assert sanitize_plan_name("plan@v2!") == "planv2"

    def test_collapse_dashes(self) -> None:
        assert sanitize_plan_name("a---b") == "a-b"

    def test_empty_fallback(self) -> None:
        assert sanitize_plan_name("!!!") == "unnamed"

    def test_length_limit(self) -> None:
        result = sanitize_plan_name("a" * 100)
        assert len(result) <= 50

    def test_slash_becomes_dash(self) -> None:
        assert sanitize_plan_name("feat/foo") == "feat-foo"

    def test_multiple_slashes(self) -> None:
        assert sanitize_plan_name("a/b/c") == "a-b-c"

    def test_backslash_becomes_dash(self) -> None:
        assert sanitize_plan_name("a\\b") == "a-b"


class TestLogger:
    def _make_logger(self, tmp_path: object, **kwargs: object) -> Logger:
        import pathlib

        p = pathlib.Path(str(tmp_path))
        original_cwd = os.getcwd()
        os.chdir(p)
        try:
            mode = Mode(str(kwargs.get("mode", "full")))
            branch = str(kwargs.get("branch", "feat"))
            default_branch = str(kwargs.get("default_branch", "main"))
            head_hash = str(kwargs.get("head_hash", "deadbeef"))
            tasks_root = str(kwargs.get("tasks_root", "cdc-tasks"))
            plan_file_default = "" if mode == Mode.REVIEW else "test.md"
            plan_file = str(kwargs.get("plan_file", plan_file_default))
            progress_path = compute_progress_path(
                mode,
                plan_file=plan_file,
                branch=branch,
                default_branch=default_branch,
                head_hash=head_hash,
                tasks_root=tasks_root,
            )
            cfg = ProgressLoggerConfig(
                progress_path=progress_path,
                plan_file=plan_file,
                mode=mode,
                branch=branch,
            )
            colors = Colors(ColorConfig())
            holder = PhaseHolder()
            return Logger(cfg, colors, holder)
        except Exception:
            os.chdir(original_cwd)
            raise

    def test_create_and_path(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            assert os.path.isfile(logger.path)
            logger.close()
        finally:
            os.chdir(original)

    def test_header_written(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path, plan_file="plan.md", mode="full", branch="feat")
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "# CADENCE Progress Log" in content
            assert "Plan: plan.md" in content
            assert "Branch: feat" in content
            assert "Mode: full" in content
            assert "Started:" in content
        finally:
            os.chdir(original)

    def test_header_review_mode_omits_empty_plan_line(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(
                tmp_path, mode="review", branch="feat", default_branch="main"
            )
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "Plan:" not in content
            assert "Branch: feat" in content
            assert "Mode: review" in content
        finally:
            os.chdir(original)

    def test_print_writes(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.print("hello %s", "world")
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "hello world" in content
        finally:
            os.chdir(original)

    def test_error_writes(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.error("bad thing")
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "ERROR: bad thing" in content
        finally:
            os.chdir(original)

    def test_warn_writes(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.warn("careful")
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "WARN: careful" in content
        finally:
            os.chdir(original)

    def test_section_writes(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.print_section(Section(label="test section"))
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "--- test section ---" in content
            import re as _re

            ts_pat = r"\[\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]"
            assert _re.search(ts_pat + r" --- test section ---", content)
        finally:
            os.chdir(original)

    def test_log_question(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.log_question("what?", ["a", "b"])
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "QUESTION: what?" in content
            assert "OPTIONS: a, b" in content
        finally:
            os.chdir(original)

    def test_log_answer(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.log_answer("42")
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "ANSWER: 42" in content
        finally:
            os.chdir(original)

    def test_close_writes_footer(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "Completed:" in content
            assert "-" * 60 in content
        finally:
            os.chdir(original)

    def test_elapsed_format(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            elapsed = logger.elapsed()
            assert "m" in elapsed
            logger.close()
        finally:
            os.chdir(original)

    def test_log_claude_output_writes_to_file_and_stdout(
        self, tmp_path: object, capsys: object
    ) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.log_claude_output("raw claude text here")
            logger.close()
            with open(logger.path) as f:
                content = f.read()
            assert "raw claude text here" in content
            found = False
            for line in content.splitlines():
                if "raw claude text here" in line:
                    assert line.startswith("[")
                    found = True
                    break
            assert found, "expected 'raw claude text here' on its own line"
            captured = capsys.readouterr()  # type: ignore[union-attr]
            assert "raw claude text here" in captured.out
        finally:
            os.chdir(original)

    def test_log_claude_output_consecutive_no_newline_single_timestamp(
        self, tmp_path: object, capsys: object
    ) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.log_claude_output("hello ")
            logger.log_claude_output("world")
            logger.close()
            captured = capsys.readouterr()  # type: ignore[union-attr]
            out = captured.out
            assert out.count("[") == 1
            assert "hello world" in out
        finally:
            os.chdir(original)

    def test_log_claude_output_multiline_chunk_timestamps_each_line(
        self, tmp_path: object, capsys: object
    ) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.log_claude_output("line1\nline2\n")
            logger.close()
            captured = capsys.readouterr()  # type: ignore[union-attr]
            out = captured.out
            lines = [line for line in out.strip().split("\n") if line]
            assert len(lines) == 2
            assert lines[0].startswith("[")
            assert "line1" in lines[0]
            assert lines[1].startswith("[")
            assert "line2" in lines[1]
        finally:
            os.chdir(original)

    def test_log_claude_output_newline_resets_line_state(
        self, tmp_path: object, capsys: object
    ) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path)
            logger.log_claude_output("first line\n")
            logger.log_claude_output("second line")
            logger.close()
            captured = capsys.readouterr()  # type: ignore[union-attr]
            out = captured.out
            assert out.count("[") == 2
            lines = out.strip().split("\n")
            assert lines[0].startswith("[")
            assert "first line" in lines[0]
            assert lines[1].startswith("[")
            assert "second line" in lines[1]
        finally:
            os.chdir(original)

    def test_reopen_completed_file_truncates(self, tmp_path: object) -> None:
        original = os.getcwd()
        try:
            logger = self._make_logger(tmp_path, plan_file="reopen.md")
            logger.print("first run")
            logger.close()

            logger2 = self._make_logger(tmp_path, plan_file="reopen.md")
            logger2.print("second run")
            logger2.close()

            with open(logger2.path) as f:
                content = f.read()
            assert "first run" not in content
            assert "second run" in content
        finally:
            os.chdir(original)

    def test_reopen_incomplete_file_appends_restart(self, tmp_path: object) -> None:
        original = os.getcwd()
        import pathlib

        p = pathlib.Path(str(tmp_path))
        os.chdir(p)
        try:
            plan_dir = p / "plans"
            plan_dir.mkdir(parents=True, exist_ok=True)
            progress_file = plan_dir / "progress-task.txt"
            progress_file.write_text("some incomplete data\n")

            cfg = ProgressLoggerConfig(
                progress_path=str(progress_file),
                plan_file="plans/incomplete.md",
                mode=Mode.FULL,
                branch="feat",
            )
            colors = Colors(ColorConfig())
            holder = PhaseHolder()
            logger = Logger(cfg, colors, holder)
            logger.close()

            content = progress_file.read_text()
            assert "some incomplete data" in content
            assert "--- restarted at" in content
        finally:
            os.chdir(original)


class TestIsProgressCompleted:
    def test_empty_file(self, tmp_path: object) -> None:
        import pathlib

        p = pathlib.Path(str(tmp_path)) / "test.txt"
        p.write_text("")
        with open(p, "r+") as f:
            assert _is_progress_completed(f) is False

    def test_completed_file(self, tmp_path: object) -> None:
        import pathlib

        p = pathlib.Path(str(tmp_path)) / "test.txt"
        p.write_text("stuff\n" + "-" * 60 + "\nCompleted: 2025-01-01 00:00:00 (0m00s)\n")
        with open(p, "r+") as f:
            assert _is_progress_completed(f) is True

    def test_incomplete_file(self, tmp_path: object) -> None:
        import pathlib

        p = pathlib.Path(str(tmp_path)) / "test.txt"
        p.write_text("some log output\n")
        with open(p, "r+") as f:
            assert _is_progress_completed(f) is False
