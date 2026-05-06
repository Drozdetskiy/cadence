from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.text import Text

STATE_INIT_ONLY = "init only"
STATE_PLAN_READY = "plan ready"
STATE_IN_FLIGHT = "in flight"
STATE_COMPLETED = "completed"
STATE_EMPTY = "empty"
STATE_UNKNOWN = "unknown state"

PHASE_PLAN = "plan"
PHASE_TASK = "task"
PHASE_NONE = "—"

_DASHES = "-" * 60
_TAIL_BYTES = 256


@dataclass(frozen=True)
class TaskState:
    name: str
    task_dir: str
    state: str
    phase: str
    has_init: bool
    has_plan: bool
    has_plan_completed: bool
    last_activity_seconds: int | None


def _is_progress_terminated(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return False
            read_size = min(size, _TAIL_BYTES)
            f.seek(size - read_size)
            tail = f.read(read_size).decode("utf-8", errors="replace")
    except OSError:
        return False
    return _DASHES in tail and "Completed:" in tail


def _dir_max_mtime(dir_path: Path) -> float | None:
    try:
        entries = list(dir_path.iterdir())
    except OSError:
        return None
    best: float | None = None
    for entry in entries:
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best:
            best = mtime
    return best


def _classify(
    task_dir: Path,
    *,
    now: float,
    threshold_seconds: int,
) -> tuple[str, str]:
    if not task_dir.is_dir():
        return STATE_EMPTY, PHASE_NONE

    plan_completed = task_dir / "plan-completed"
    plan_file = task_dir / "plan"
    init_file = task_dir / "init"

    if plan_completed.exists():
        return STATE_COMPLETED, PHASE_NONE

    if plan_file.exists():
        progress = task_dir / "progress-task.txt"
        if progress.exists():
            try:
                mtime = progress.stat().st_mtime
            except OSError:
                mtime = None
            if (
                mtime is not None
                and (now - mtime) <= threshold_seconds
                and not _is_progress_terminated(progress)
            ):
                return STATE_IN_FLIGHT, PHASE_TASK
        return STATE_PLAN_READY, PHASE_TASK

    if init_file.exists():
        progress = task_dir / "progress-plan.txt"
        if progress.exists():
            try:
                mtime = progress.stat().st_mtime
            except OSError:
                mtime = None
            if (
                mtime is not None
                and (now - mtime) <= threshold_seconds
                and not _is_progress_terminated(progress)
            ):
                return STATE_IN_FLIGHT, PHASE_PLAN
        return STATE_INIT_ONLY, PHASE_PLAN

    return STATE_UNKNOWN, PHASE_NONE


def _build_task_state(
    tasks_root: Path,
    name: str,
    *,
    now: float,
    threshold_seconds: int,
) -> TaskState:
    task_dir = tasks_root / name
    rel_dir = f"{tasks_root.as_posix()}/{name}"

    if not task_dir.is_dir():
        return TaskState(
            name=name,
            task_dir=rel_dir,
            state=STATE_EMPTY,
            phase=PHASE_NONE,
            has_init=False,
            has_plan=False,
            has_plan_completed=False,
            last_activity_seconds=None,
        )

    has_init = (task_dir / "init").exists()
    has_plan = (task_dir / "plan").exists()
    has_plan_completed = (task_dir / "plan-completed").exists()

    state, phase = _classify(task_dir, now=now, threshold_seconds=threshold_seconds)

    max_mtime = _dir_max_mtime(task_dir)
    if max_mtime is None:
        last_activity: int | None = None
    else:
        last_activity = max(0, int(now - max_mtime))

    return TaskState(
        name=name,
        task_dir=rel_dir,
        state=state,
        phase=phase,
        has_init=has_init,
        has_plan=has_plan,
        has_plan_completed=has_plan_completed,
        last_activity_seconds=last_activity,
    )


def collect_task_states(
    tasks_root: Path,
    *,
    now: float | None = None,
    running_threshold_seconds: int,
) -> list[TaskState]:
    if now is None:
        now = time.time()
    if not tasks_root.is_dir():
        return []
    results: list[TaskState] = []
    try:
        entries = list(tasks_root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        results.append(
            _build_task_state(
                tasks_root,
                entry.name,
                now=now,
                threshold_seconds=running_threshold_seconds,
            )
        )
    return results


def get_task_state(
    tasks_root: Path,
    name: str,
    *,
    now: float | None = None,
    running_threshold_seconds: int,
) -> TaskState:
    if now is None:
        now = time.time()
    return _build_task_state(
        tasks_root,
        name,
        now=now,
        threshold_seconds=running_threshold_seconds,
    )


def _format_age(seconds: int | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 60:
        return "<1 minute ago"
    if seconds < 3600:
        n = seconds // 60
        return f"{n} minute{'s' if n != 1 else ''} ago"
    if seconds < 86400:
        n = seconds // 3600
        return f"{n} hour{'s' if n != 1 else ''} ago"
    if seconds < 86400 * 30:
        n = seconds // 86400
        return f"{n} day{'s' if n != 1 else ''} ago"
    if seconds < 86400 * 365:
        n = seconds // (86400 * 30)
        return f"{n} month{'s' if n != 1 else ''} ago"
    n = seconds // (86400 * 365)
    return f"{n} year{'s' if n != 1 else ''} ago"


_STATE_SORT: dict[str, int] = {
    STATE_IN_FLIGHT: 0,
    STATE_PLAN_READY: 1,
    STATE_INIT_ONLY: 2,
    STATE_UNKNOWN: 3,
    STATE_COMPLETED: 4,
    STATE_EMPTY: 5,
}


def _state_sort_key(state: str) -> int:
    return _STATE_SORT.get(state, 99)


def sort_other_tasks(states: list[TaskState]) -> list[TaskState]:
    def key(t: TaskState) -> tuple[int, int, int]:
        # None activity sorts last; otherwise smaller seconds (more recent) first.
        activity_present = 0 if t.last_activity_seconds is not None else 1
        activity_value = t.last_activity_seconds if t.last_activity_seconds is not None else 0
        return (_state_sort_key(t.state), activity_present, activity_value)

    return sorted(states, key=key)


_STATE_STYLE: dict[str, str] = {
    STATE_COMPLETED: "green",
    STATE_IN_FLIGHT: "yellow",
    STATE_PLAN_READY: "cyan",
    STATE_INIT_ONLY: "dim",
    STATE_UNKNOWN: "red",
    STATE_EMPTY: "dim",
}


@dataclass(frozen=True)
class CommitInfo:
    hash: str
    age: str
    subject: str


def query_last_external_commit(repo_path: str, *, tasks_root: str) -> CommitInfo | None:
    fmt = "%h%x1f%ar%x1f%s"
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                repo_path,
                "log",
                "-1",
                f"--format={fmt}",
                "--",
                ".",
                f":(exclude,glob){tasks_root}/**",
                f":(exclude){tasks_root}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip()
    if not line:
        return None
    parts = line.split("\x1f", 2)
    if len(parts) != 3:
        return None
    return CommitInfo(hash=parts[0], age=parts[1], subject=parts[2])


def _render_console(no_color: bool) -> Console:
    buf = StringIO()
    return Console(
        file=buf,
        force_terminal=not no_color,
        no_color=no_color,
        width=120,
        highlight=False,
        soft_wrap=True,
        markup=False,
    )


def _styled_state(state: str) -> Text:
    style = _STATE_STYLE.get(state, "")
    return Text(state, style=style)


def format_status_text(
    *,
    current: TaskState | None,
    current_branch: str,
    tasks_root: str,
    last_commit: CommitInfo | None,
    others: list[TaskState],
    no_color: bool,
    only_current: bool,
) -> str:
    console = _render_console(no_color)

    rendered_current = False
    if current is not None and current_branch and current.state != STATE_EMPTY:
        rendered_current = True
        console.print(f"current branch: {current_branch}")
        console.print(f"  task: {current.task_dir}/")
        rows: list[tuple[str, Text | str]] = []
        if current.has_init:
            rows.append(("init", "✓"))
        if current.has_plan:
            rows.append(("plan", "✓"))
        if current.has_plan_completed:
            rows.append(("plan-completed", "✓"))
        if last_commit is not None:
            commit_text = f'{last_commit.hash} ({last_commit.age}) "{last_commit.subject}"'
            rows.append(("last commit", commit_text))
        rows.append(("state", _styled_state(current.state)))

        label_width = max(len(label) for label, _ in rows)
        for label, value in rows:
            line = Text("    ")
            line.append(label.ljust(label_width))
            line.append("  ")
            if isinstance(value, Text):
                line.append_text(value)
            else:
                line.append(value)
            console.print(line)
    elif current is not None and current_branch and current.state == STATE_EMPTY:
        rendered_current = True
        console.print(f"current branch: {current_branch} — no task dir under {tasks_root}/")

    if only_current:
        if not rendered_current:
            console.print("no current cadence task")
        return _drain(console)

    if not others:
        if not rendered_current:
            console.print(f"no tasks under {tasks_root}/")
        return _drain(console)

    if rendered_current:
        console.print("")
    console.print(f"other tasks under {tasks_root}/:")

    branch_w = max(6, max(len(t.name) for t in others))
    state_w = max(12, max(len(t.state) for t in others))
    phase_w = 8
    header = Text("  ")
    header.append("branch".ljust(branch_w))
    header.append("  ")
    header.append("state".ljust(state_w))
    header.append("  ")
    header.append("phase".ljust(phase_w))
    header.append("  ")
    header.append("last activity")
    console.print(header)

    for t in others:
        row = Text("  ")
        row.append(t.name.ljust(branch_w))
        row.append("  ")
        state_text = _styled_state(t.state)
        # pad state column accounting for visible length
        pad = max(0, state_w - len(t.state))
        row.append_text(state_text)
        row.append(" " * pad)
        row.append("  ")
        phase = t.phase if t.phase else PHASE_NONE
        row.append(phase.ljust(phase_w))
        row.append("  ")
        row.append(_format_age(t.last_activity_seconds))
        console.print(row)

    return _drain(console)


def _drain(console: Console) -> str:
    file = console.file
    assert isinstance(file, StringIO)
    return file.getvalue()


def _phase_for_json(phase: str) -> str | None:
    if phase == PHASE_NONE:
        return None
    return phase


def _task_state_to_json(t: TaskState) -> dict[str, object]:
    return {
        "name": t.name,
        "task_dir": t.task_dir,
        "state": t.state,
        "phase": _phase_for_json(t.phase),
        "last_activity_seconds": t.last_activity_seconds,
        "files": {
            "init": t.has_init,
            "plan": t.has_plan,
            "plan-completed": t.has_plan_completed,
        },
    }


def format_status_json(
    *,
    current: TaskState | None,
    current_branch: str,
    last_commit: CommitInfo | None,
    tasks: list[TaskState],
    tasks_root: str,
) -> str:
    if current is None:
        current_payload: dict[str, object] | None = None
    else:
        commit_payload: dict[str, object] | None = None
        if last_commit is not None:
            commit_payload = {
                "hash": last_commit.hash,
                "age": last_commit.age,
                "subject": last_commit.subject,
            }
        current_payload = {
            "branch": current_branch,
            "task_dir": current.task_dir,
            "state": current.state,
            "phase": _phase_for_json(current.phase),
            "files": {
                "init": current.has_init,
                "plan": current.has_plan,
                "plan-completed": current.has_plan_completed,
            },
            "last_activity_seconds": current.last_activity_seconds,
            "last_commit": commit_payload,
        }

    payload: dict[str, object] = {
        "tasks_root": tasks_root,
        "current": current_payload,
        "tasks": [_task_state_to_json(t) for t in tasks],
    }
    return json.dumps(payload, indent=2, sort_keys=False)
