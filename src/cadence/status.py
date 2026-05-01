from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

type Phase = str

PhaseTask: Phase = "task"
PhaseReview: Phase = "review"
PhasePlan: Phase = "plan"
PhaseFinalize: Phase = "finalize"

SignalCompleted = "<<<CADENCE:ALL_TASKS_DONE>>>"
SignalFailed = "<<<CADENCE:TASK_FAILED>>>"
SignalReviewDone = "<<<CADENCE:REVIEW_DONE>>>"
SignalQuestion = "<<<CADENCE:QUESTION>>>"
SignalPlanReady = "<<<CADENCE:PLAN_READY>>>"
SignalPlanDraft = "<<<CADENCE:PLAN_DRAFT>>>"
SignalEnd = "<<<CADENCE:END>>>"


class Mode(StrEnum):
    PLAN = "plan"
    FULL = "full"
    REVIEW = "review"


@dataclass(frozen=True)
class Section:
    label: str


def new_task_iteration_section(n: int) -> Section:
    return Section(label=f"task iteration {n}")


def new_claude_review_section(n: int, suffix: str) -> Section:
    return Section(label=f"claude review {n}: {suffix}")


def new_plan_iteration_section(n: int) -> Section:
    return Section(label=f"plan iteration {n}")


def new_generic_section(label: str) -> Section:
    return Section(label=label)


def new_finalize_section() -> Section:
    return Section(label="finalize step")


class PhaseHolder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._phase: Phase = PhaseTask
        self._callback: Callable[[Phase], None] | None = None

    def get(self) -> Phase:
        with self._lock:
            return self._phase

    def set(self, phase: Phase) -> None:
        with self._lock:
            self._phase = phase
            cb = self._callback
        if cb is not None:
            cb(phase)

    def on_change(self, callback: Callable[[Phase], None]) -> None:
        with self._lock:
            self._callback = callback
