from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class TaskStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


_format_in_text = re.compile(r"\[\s*[ xX]?\s*\]")
_task_header_re = re.compile(r"^###\s+(?:Task|Iteration)\s+([^:]+?):\s*(.*)$")
_checkbox_re = re.compile(r"^\s*-\s+\[([ xX])\]\s*(.*)$")
_title_re = re.compile(r"^#\s+(.*)$")
_h2_re = re.compile(r"^##\s+")
_h1_re = re.compile(r"^#\s+")


@dataclass
class Checkbox:
    text: str
    checked: bool

    def is_actionable(self) -> bool:
        return _format_in_text.search(self.text) is None


@dataclass
class Task:
    number: int
    title: str
    status: TaskStatus
    checkboxes: list[Checkbox] = field(default_factory=list)

    def has_uncompleted_actionable_work(self) -> bool:
        return any(not cb.checked and cb.is_actionable() for cb in self.checkboxes)


@dataclass
class Plan:
    title: str
    tasks: list[Task] = field(default_factory=list)


def determine_task_status(checkboxes: list[Checkbox]) -> TaskStatus:
    if not checkboxes:
        return TaskStatus.PENDING
    total = len(checkboxes)
    checked = sum(1 for cb in checkboxes if cb.checked)
    if checked == 0:
        return TaskStatus.PENDING
    if checked == total:
        return TaskStatus.DONE
    return TaskStatus.ACTIVE


def _parse_task_num(s: str) -> int:
    try:
        return int(s.strip())
    except ValueError:
        return 0


def parse_plan(content: str) -> Plan:
    title = ""
    title_set = False
    tasks: list[Task] = []
    current: Task | None = None

    def _finalize_current() -> None:
        nonlocal current
        if current is not None:
            current.status = determine_task_status(current.checkboxes)
            tasks.append(current)
            current = None

    for raw_line in content.splitlines():
        line = raw_line.rstrip("\r")

        m_task = _task_header_re.match(line)
        if m_task is not None:
            _finalize_current()
            num = _parse_task_num(m_task.group(1))
            heading = m_task.group(2).strip()
            current = Task(number=num, title=heading, status=TaskStatus.PENDING, checkboxes=[])
            continue

        if _h2_re.match(line):
            _finalize_current()
            continue

        if _h1_re.match(line) and not _task_header_re.match(line):
            if not title_set:
                m_title = _title_re.match(line)
                if m_title is not None:
                    title = m_title.group(1).strip()
                    title_set = True
                continue
            _finalize_current()
            continue

        m_cb = _checkbox_re.match(line)
        if m_cb is not None and current is not None:
            mark = m_cb.group(1)
            text = m_cb.group(2).strip()
            checked = mark in ("x", "X")
            current.checkboxes.append(Checkbox(text=text, checked=checked))
            continue

    _finalize_current()

    return Plan(title=title, tasks=tasks)


def parse_plan_file(path: str) -> Plan:
    content = Path(path).read_text(encoding="utf-8")
    return parse_plan(content)


def file_has_uncompleted_checkbox(path: str) -> bool:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    for raw in content.splitlines():
        m = _checkbox_re.match(raw)
        if m is None:
            continue
        mark = m.group(1)
        text = m.group(2).strip()
        if mark in ("x", "X"):
            continue
        if _format_in_text.search(text) is not None:
            continue
        return True
    return False
