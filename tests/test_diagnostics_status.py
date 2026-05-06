from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cadence.diagnostics.status import (
    PHASE_NONE,
    PHASE_PLAN,
    PHASE_TASK,
    STATE_COMPLETED,
    STATE_EMPTY,
    STATE_IN_FLIGHT,
    STATE_INIT_ONLY,
    STATE_PLAN_READY,
    STATE_UNKNOWN,
    CommitInfo,
    TaskState,
    _format_age,
    collect_task_states,
    format_status_json,
    format_status_text,
    get_task_state,
    query_last_external_commit,
    sort_other_tasks,
)


def _make_task_dir(root: Path, name: str, files: dict[str, str]) -> Path:
    d = root / name
    d.mkdir(parents=True)
    for fname, content in files.items():
        (d / fname).write_text(content)
    return d


def _set_mtime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


class TestCollectTaskStates:
    def test_missing_tasks_root(self, tmp_path: Path) -> None:
        result = collect_task_states(tmp_path / "missing", running_threshold_seconds=600)
        assert result == []

    def test_empty_tasks_root(self, tmp_path: Path) -> None:
        result = collect_task_states(tmp_path, running_threshold_seconds=600)
        assert result == []

    def test_init_only(self, tmp_path: Path) -> None:
        _make_task_dir(tmp_path, "0001-foo", {"init": "do things"})
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=10000.0)
        assert len(states) == 1
        s = states[0]
        assert s.name == "0001-foo"
        assert s.state == STATE_INIT_ONLY
        assert s.phase == PHASE_PLAN
        assert s.has_init is True
        assert s.has_plan is False
        assert s.has_plan_completed is False

    def test_plan_ready_no_progress(self, tmp_path: Path) -> None:
        _make_task_dir(tmp_path, "0001-foo", {"init": "x", "plan": "y"})
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=10000.0)
        s = states[0]
        assert s.state == STATE_PLAN_READY
        assert s.phase == PHASE_TASK

    def test_in_flight_fresh_progress_task(self, tmp_path: Path) -> None:
        d = _make_task_dir(
            tmp_path,
            "0001-foo",
            {"init": "x", "plan": "y", "progress-task.txt": "running\n"},
        )
        now = 10000.0
        _set_mtime(d / "progress-task.txt", now - 30)
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=now)
        s = states[0]
        assert s.state == STATE_IN_FLIGHT
        assert s.phase == PHASE_TASK

    def test_plan_ready_when_progress_stale(self, tmp_path: Path) -> None:
        d = _make_task_dir(
            tmp_path,
            "0001-foo",
            {"init": "x", "plan": "y", "progress-task.txt": "running\n"},
        )
        now = 10000.0
        _set_mtime(d / "progress-task.txt", now - 1200)
        # Also push the dir mtime to a fixed older value so detection rests on the file's mtime.
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=now)
        s = states[0]
        assert s.state == STATE_PLAN_READY

    def test_plan_ready_when_progress_terminated(self, tmp_path: Path) -> None:
        terminator = "-" * 60 + "\nCompleted: 2026-01-01 (5m)\n"
        d = _make_task_dir(
            tmp_path,
            "0001-foo",
            {"init": "x", "plan": "y", "progress-task.txt": "log\n" + terminator},
        )
        now = 10000.0
        _set_mtime(d / "progress-task.txt", now - 30)
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=now)
        s = states[0]
        assert s.state == STATE_PLAN_READY

    def test_completed_via_plan_completed(self, tmp_path: Path) -> None:
        _make_task_dir(tmp_path, "0001-foo", {"init": "x", "plan-completed": "z"})
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=10000.0)
        s = states[0]
        assert s.state == STATE_COMPLETED
        assert s.phase == PHASE_NONE
        assert s.has_plan_completed is True

    def test_plan_completed_takes_precedence_over_plan(self, tmp_path: Path) -> None:
        _make_task_dir(
            tmp_path,
            "0001-foo",
            {"init": "x", "plan": "y", "plan-completed": "z"},
        )
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=10000.0)
        assert states[0].state == STATE_COMPLETED

    def test_init_only_in_flight_via_progress_plan(self, tmp_path: Path) -> None:
        d = _make_task_dir(tmp_path, "0001-foo", {"init": "x", "progress-plan.txt": "log\n"})
        now = 10000.0
        _set_mtime(d / "progress-plan.txt", now - 30)
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=now)
        s = states[0]
        assert s.state == STATE_IN_FLIGHT
        assert s.phase == PHASE_PLAN

    def test_unknown_when_directory_has_only_unknown_file(self, tmp_path: Path) -> None:
        _make_task_dir(tmp_path, "0001-foo", {"random-file": "x"})
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=10000.0)
        s = states[0]
        assert s.state == STATE_UNKNOWN
        assert s.phase == PHASE_NONE

    def test_non_directory_entries_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "stray-file").write_text("x")
        _make_task_dir(tmp_path, "0001-foo", {"init": "y"})
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=10000.0)
        assert len(states) == 1
        assert states[0].name == "0001-foo"

    def test_last_activity_seconds_against_passed_now(self, tmp_path: Path) -> None:
        d = _make_task_dir(tmp_path, "0001-foo", {"init": "x"})
        now = 100000.0
        _set_mtime(d / "init", now - 500)
        _set_mtime(d, now - 500)
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=now)
        s = states[0]
        assert s.last_activity_seconds is not None
        assert 490 <= s.last_activity_seconds <= 510

    def test_task_dir_field_relative(self, tmp_path: Path) -> None:
        _make_task_dir(tmp_path, "0001-foo", {"init": "y"})
        states = collect_task_states(tmp_path, running_threshold_seconds=600, now=10000.0)
        assert states[0].task_dir.endswith("0001-foo")


class TestGetTaskState:
    def test_missing_task_returns_empty(self, tmp_path: Path) -> None:
        state = get_task_state(
            tmp_path,
            "no-such-task",
            running_threshold_seconds=600,
            now=10000.0,
        )
        assert isinstance(state, TaskState)
        assert state.state == STATE_EMPTY
        assert state.phase == PHASE_NONE
        assert state.has_init is False
        assert state.last_activity_seconds is None

    def test_existing_task(self, tmp_path: Path) -> None:
        _make_task_dir(tmp_path, "0001-foo", {"init": "x", "plan": "y"})
        state = get_task_state(tmp_path, "0001-foo", running_threshold_seconds=600, now=10000.0)
        assert state.state == STATE_PLAN_READY
        assert state.phase == PHASE_TASK


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _state(
    name: str,
    *,
    state: str,
    phase: str = PHASE_NONE,
    has_init: bool = True,
    has_plan: bool = False,
    has_plan_completed: bool = False,
    last_activity_seconds: int | None = None,
    task_dir: str | None = None,
) -> TaskState:
    return TaskState(
        name=name,
        task_dir=task_dir or f"cdc-tasks/{name}",
        state=state,
        phase=phase,
        has_init=has_init,
        has_plan=has_plan,
        has_plan_completed=has_plan_completed,
        last_activity_seconds=last_activity_seconds,
    )


class TestFormatAge:
    def test_none_returns_never(self) -> None:
        assert _format_age(None) == "never"

    def test_under_60_seconds(self) -> None:
        assert _format_age(0) == "<1 minute ago"
        assert _format_age(30) == "<1 minute ago"
        assert _format_age(59) == "<1 minute ago"

    def test_minutes(self) -> None:
        assert _format_age(60) == "1 minute ago"
        assert _format_age(119) == "1 minute ago"
        assert _format_age(120) == "2 minutes ago"
        assert _format_age(3599) == "59 minutes ago"

    def test_hours(self) -> None:
        assert _format_age(3600) == "1 hour ago"
        assert _format_age(7200) == "2 hours ago"
        assert _format_age(86399) == "23 hours ago"

    def test_days(self) -> None:
        assert _format_age(86400) == "1 day ago"
        assert _format_age(86400 * 2) == "2 days ago"

    def test_months(self) -> None:
        assert _format_age(86400 * 30) == "1 month ago"
        assert _format_age(86400 * 60) == "2 months ago"

    def test_years(self) -> None:
        assert _format_age(86400 * 365) == "1 year ago"
        assert _format_age(86400 * 365 * 3) == "3 years ago"


class TestSortOtherTasks:
    def test_groups_by_state_then_activity(self) -> None:
        a = _state("a", state=STATE_COMPLETED, last_activity_seconds=10)
        b = _state("b", state=STATE_IN_FLIGHT, last_activity_seconds=200)
        c = _state("c", state=STATE_IN_FLIGHT, last_activity_seconds=100)
        d = _state("d", state=STATE_PLAN_READY, last_activity_seconds=50)
        e = _state("e", state=STATE_INIT_ONLY, last_activity_seconds=20)
        f = _state("f", state=STATE_UNKNOWN, last_activity_seconds=5)
        g = _state("g", state=STATE_EMPTY, last_activity_seconds=None)
        result = sort_other_tasks([a, b, c, d, e, f, g])
        names = [t.name for t in result]
        # in-flight first (by activity asc), then plan ready, init only, unknown, completed, empty
        assert names == ["c", "b", "d", "e", "f", "a", "g"]

    def test_none_activity_sorts_last_within_group(self) -> None:
        a = _state("a", state=STATE_IN_FLIGHT, last_activity_seconds=None)
        b = _state("b", state=STATE_IN_FLIGHT, last_activity_seconds=10)
        c = _state("c", state=STATE_IN_FLIGHT, last_activity_seconds=5)
        result = sort_other_tasks([a, b, c])
        assert [t.name for t in result] == ["c", "b", "a"]


class TestFormatStatusText:
    def test_only_current_skips_others_section(self) -> None:
        current = _state(
            "0001-foo",
            state=STATE_PLAN_READY,
            phase=PHASE_TASK,
            has_init=True,
            has_plan=True,
            last_activity_seconds=120,
        )
        out = format_status_text(
            current=current,
            current_branch="0001-foo",
            tasks_root="cdc-tasks",
            last_commit=None,
            others=[_state("0002-bar", state=STATE_INIT_ONLY)],
            no_color=True,
            only_current=True,
        )
        assert "current branch: 0001-foo" in out
        assert "task: cdc-tasks/0001-foo/" in out
        assert "init" in out
        assert "plan" in out
        assert "state" in out
        assert "plan ready" in out
        assert "other tasks under" not in out
        assert "0002-bar" not in out

    def test_empty_state_renders_no_task_dir_line(self) -> None:
        current = _state("0001-foo", state=STATE_EMPTY, has_init=False)
        out = format_status_text(
            current=current,
            current_branch="0001-foo",
            tasks_root="cdc-tasks",
            last_commit=None,
            others=[],
            no_color=True,
            only_current=False,
        )
        assert "current branch: 0001-foo — no task dir under cdc-tasks/" in out

    def test_no_tasks_at_all(self) -> None:
        out = format_status_text(
            current=None,
            current_branch="",
            tasks_root="cdc-tasks",
            last_commit=None,
            others=[],
            no_color=True,
            only_current=False,
        )
        assert out.strip() == "no tasks under cdc-tasks/"

    def test_both_sections(self) -> None:
        current = _state(
            "0001-foo",
            state=STATE_IN_FLIGHT,
            phase=PHASE_TASK,
            has_init=True,
            has_plan=True,
            last_activity_seconds=30,
        )
        commit = CommitInfo(hash="abc1234", age="2 hours ago", subject="hello world")
        others = [
            _state(
                "0002-bar",
                state=STATE_PLAN_READY,
                phase=PHASE_TASK,
                has_plan=True,
                last_activity_seconds=600,
            ),
            _state(
                "0003-baz",
                state=STATE_COMPLETED,
                phase=PHASE_NONE,
                has_plan_completed=True,
                last_activity_seconds=86400,
            ),
        ]
        out = format_status_text(
            current=current,
            current_branch="0001-foo",
            tasks_root="cdc-tasks",
            last_commit=commit,
            others=others,
            no_color=True,
            only_current=False,
        )
        assert "current branch: 0001-foo" in out
        assert "last commit" in out
        assert "abc1234" in out
        assert "(2 hours ago)" in out
        assert '"hello world"' in out
        assert "in flight" in out
        assert "other tasks under cdc-tasks/:" in out
        assert "0002-bar" in out
        assert "0003-baz" in out
        assert "branch" in out and "phase" in out and "last activity" in out

    def test_missing_last_commit_omits_row(self) -> None:
        current = _state(
            "0001-foo",
            state=STATE_PLAN_READY,
            phase=PHASE_TASK,
            has_init=True,
            has_plan=True,
        )
        out = format_status_text(
            current=current,
            current_branch="0001-foo",
            tasks_root="cdc-tasks",
            last_commit=None,
            others=[],
            no_color=True,
            only_current=False,
        )
        assert "last commit" not in out

    def test_no_color_strips_ansi(self) -> None:
        current = _state(
            "0001-foo",
            state=STATE_IN_FLIGHT,
            phase=PHASE_TASK,
            has_init=True,
            has_plan=True,
        )
        out = format_status_text(
            current=current,
            current_branch="0001-foo",
            tasks_root="cdc-tasks",
            last_commit=None,
            others=[],
            no_color=True,
            only_current=False,
        )
        assert _strip_ansi(out) == out

    def test_unknown_state_row_aligns_with_other_rows(self) -> None:
        others = [
            _state("0001-foo", state=STATE_INIT_ONLY, phase=PHASE_PLAN),
            _state("0002-bar", state=STATE_UNKNOWN, phase=PHASE_NONE),
        ]
        out = format_status_text(
            current=None,
            current_branch="",
            tasks_root="cdc-tasks",
            last_commit=None,
            others=others,
            no_color=True,
            only_current=False,
        )
        rows = [line for line in out.splitlines() if "0001-foo" in line or "0002-bar" in line]
        assert len(rows) == 2
        # Phase column position must match across rows regardless of state length.
        assert rows[0].index(PHASE_PLAN) == rows[1].index(PHASE_NONE)

    def test_only_current_no_current_emits_message(self) -> None:
        out = format_status_text(
            current=None,
            current_branch="",
            tasks_root="cdc-tasks",
            last_commit=None,
            others=[],
            no_color=True,
            only_current=True,
        )
        assert "no current cadence task" in out


class TestFormatStatusJson:
    def test_full_payload_round_trip(self) -> None:
        current = _state(
            "0001-foo",
            state=STATE_IN_FLIGHT,
            phase=PHASE_TASK,
            has_init=True,
            has_plan=True,
            last_activity_seconds=30,
        )
        commit = CommitInfo(hash="abc1234", age="2 hours ago", subject="hello")
        others = [
            _state(
                "0002-bar",
                state=STATE_COMPLETED,
                phase=PHASE_NONE,
                has_init=True,
                has_plan_completed=True,
                last_activity_seconds=86400,
            ),
        ]
        out = format_status_json(
            current=current,
            current_branch="0001-foo",
            last_commit=commit,
            tasks=others,
            tasks_root="cdc-tasks",
        )
        data = json.loads(out)
        assert data["tasks_root"] == "cdc-tasks"
        assert data["current"]["branch"] == "0001-foo"
        assert data["current"]["state"] == STATE_IN_FLIGHT
        assert data["current"]["phase"] == PHASE_TASK
        assert data["current"]["files"] == {
            "init": True,
            "plan": True,
            "plan-completed": False,
        }
        assert data["current"]["last_activity_seconds"] == 30
        assert data["current"]["last_commit"] == {
            "hash": "abc1234",
            "age": "2 hours ago",
            "subject": "hello",
        }
        assert isinstance(data["tasks"], list)
        assert data["tasks"][0]["name"] == "0002-bar"
        # Completed tasks: phase serialized as null
        assert data["tasks"][0]["phase"] is None
        assert data["tasks"][0]["files"]["plan-completed"] is True

    def test_no_current(self) -> None:
        out = format_status_json(
            current=None,
            current_branch="",
            last_commit=None,
            tasks=[],
            tasks_root="cdc-tasks",
        )
        data = json.loads(out)
        assert data["current"] is None
        assert data["tasks"] == []

    def test_last_commit_none_serialized(self) -> None:
        current = _state(
            "0001-foo", state=STATE_PLAN_READY, phase=PHASE_TASK, has_init=True, has_plan=True
        )
        out = format_status_json(
            current=current,
            current_branch="0001-foo",
            last_commit=None,
            tasks=[],
            tasks_root="cdc-tasks",
        )
        data = json.loads(out)
        assert data["current"]["last_commit"] is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
class TestQueryLastExternalCommit:
    def _git(self, repo: Path, *args: str) -> None:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            env=env,
        )

    def test_returns_outside_commit_excluding_tasks_root(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-b", "main")
        self._git(repo, "config", "user.email", "test@example.com")
        self._git(repo, "config", "user.name", "Test")
        # First commit: outside tasks_root
        (repo / "src.py").write_text("print('hi')\n")
        self._git(repo, "add", "src.py")
        self._git(repo, "commit", "-m", "outside change")
        # Second commit: inside tasks_root
        (repo / "cdc-tasks").mkdir()
        (repo / "cdc-tasks" / "0001-foo").mkdir()
        (repo / "cdc-tasks" / "0001-foo" / "init").write_text("do things\n")
        self._git(repo, "add", "cdc-tasks/0001-foo/init")
        self._git(repo, "commit", "-m", "tasks-only change")
        info = query_last_external_commit(str(repo), tasks_root="cdc-tasks")
        assert info is not None
        assert info.subject == "outside change"
        assert info.hash
        assert "ago" in info.age

    def test_returns_none_when_no_external_commits(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-b", "main")
        self._git(repo, "config", "user.email", "test@example.com")
        self._git(repo, "config", "user.name", "Test")
        (repo / "cdc-tasks").mkdir()
        (repo / "cdc-tasks" / "0001-foo").mkdir()
        (repo / "cdc-tasks" / "0001-foo" / "init").write_text("x\n")
        self._git(repo, "add", "cdc-tasks/0001-foo/init")
        self._git(repo, "commit", "-m", "only inside")
        info = query_last_external_commit(str(repo), tasks_root="cdc-tasks")
        assert info is None

    def test_returns_none_when_not_a_repo(self, tmp_path: Path) -> None:
        info = query_last_external_commit(str(tmp_path), tasks_root="cdc-tasks")
        assert info is None

    def test_returns_none_when_git_executable_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("git not on PATH")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert query_last_external_commit(str(tmp_path), tasks_root="cdc-tasks") is None

    def test_returns_none_when_git_times_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="git", timeout=5)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert query_last_external_commit(str(tmp_path), tasks_root="cdc-tasks") is None

    def test_decodes_with_utf8_replace_to_avoid_locale_crash(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Under a non-UTF-8 locale, subprocess.run(text=True) without explicit
        # encoding can raise UnicodeDecodeError on unusual commit subjects.
        # query_last_external_commit must pin encoding="utf-8", errors="replace"
        # so it can never crash status output.
        captured: dict[str, object] = {}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(
                args=list(args[0]) if args else [],
                returncode=0,
                stdout="abc1234\x1f3 minutes ago\xff\x1fbroken\xff subject\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        info = query_last_external_commit(str(tmp_path), tasks_root="cdc-tasks")
        assert info is not None
        assert info.hash == "abc1234"
        kwargs = captured["kwargs"]
        assert isinstance(kwargs, dict)
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "replace"


class TestFormatStatusTextMarkupSafe:
    def test_tasks_root_with_brackets_is_not_interpreted_as_markup(self) -> None:
        out = format_status_text(
            current=None,
            current_branch="",
            tasks_root="cdc-[tasks]",
            last_commit=None,
            others=[],
            no_color=True,
            only_current=False,
        )
        assert "cdc-[tasks]" in out
