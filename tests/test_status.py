from __future__ import annotations

import threading

from cadence.status import (
    PhaseHolder,
    PhasePlan,
    PhaseReview,
    PhaseTask,
    Section,
    SignalCompleted,
    SignalEnd,
    SignalFailed,
    SignalPlanDraft,
    SignalPlanReady,
    SignalQuestion,
    SignalReviewDone,
    new_claude_review_section,
    new_plan_iteration_section,
    new_task_iteration_section,
)


class TestPhaseConstants:
    def test_phase_values(self) -> None:
        assert PhaseTask == "task"
        assert PhaseReview == "review"
        assert PhasePlan == "plan"


class TestSignalConstants:
    def test_signal_format(self) -> None:
        assert SignalCompleted == "<<<CADENCE:ALL_TASKS_DONE>>>"
        assert SignalFailed == "<<<CADENCE:TASK_FAILED>>>"
        assert SignalReviewDone == "<<<CADENCE:REVIEW_DONE>>>"
        assert SignalQuestion == "<<<CADENCE:QUESTION>>>"
        assert SignalPlanReady == "<<<CADENCE:PLAN_READY>>>"
        assert SignalPlanDraft == "<<<CADENCE:PLAN_DRAFT>>>"
        assert SignalEnd == "<<<CADENCE:END>>>"

    def test_all_signals_have_cadence_prefix(self) -> None:
        for sig in [
            SignalCompleted,
            SignalFailed,
            SignalReviewDone,
            SignalQuestion,
            SignalPlanReady,
            SignalPlanDraft,
            SignalEnd,
        ]:
            assert sig.startswith("<<<CADENCE:")
            assert sig.endswith(">>>")


class TestSection:
    def test_section_is_frozen(self) -> None:
        s = Section(label="test")
        assert s.label == "test"

    def test_task_iteration_section(self) -> None:
        s = new_task_iteration_section(3)
        assert s.label == "task iteration 3"

    def test_claude_review_section(self) -> None:
        s = new_claude_review_section(0, "all findings")
        assert s.label == "claude review 0: all findings"

    def test_plan_iteration_section(self) -> None:
        s = new_plan_iteration_section(1)
        assert s.label == "plan iteration 1"


class TestPhaseHolder:
    def test_default_phase(self) -> None:
        ph = PhaseHolder()
        assert ph.get() == PhaseTask

    def test_set_and_get(self) -> None:
        ph = PhaseHolder()
        ph.set(PhaseReview)
        assert ph.get() == PhaseReview

    def test_on_change_callback(self) -> None:
        ph = PhaseHolder()
        received: list[str] = []
        ph.on_change(lambda p: received.append(p))
        ph.set(PhasePlan)
        assert received == [PhasePlan]

    def test_callback_not_called_before_registration(self) -> None:
        ph = PhaseHolder()
        ph.set(PhaseReview)
        received: list[str] = []
        ph.on_change(lambda p: received.append(p))
        assert received == []

    def test_thread_safety(self) -> None:
        ph = PhaseHolder()
        results: list[str] = []
        barrier = threading.Barrier(10)

        def writer(phase: str) -> None:
            barrier.wait()
            ph.set(phase)

        def reader() -> None:
            barrier.wait()
            results.append(ph.get())

        threads = []
        for _i in range(5):
            t = threading.Thread(target=writer, args=(PhaseReview,))
            threads.append(t)
        for _ in range(5):
            t = threading.Thread(target=reader)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5
        for r in results:
            assert r in (PhaseTask, PhaseReview)
