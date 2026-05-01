from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rlx.config import Config as AppConfig
from rlx.config import parse_duration
from rlx.executor.claude_executor import (
    LimitPatternError,
    PatternMatchError,
    Result,
)
from rlx.git.service import completed_plan_path
from rlx.plan import (
    file_has_uncompleted_checkbox,
    parse_plan_file,
)
from rlx.processor.prompts import (
    build_finalize_prompt,
    build_plan_prompt,
    build_review_first_prompt,
    build_review_second_prompt,
    build_task_prompt,
)
from rlx.processor.signals import (
    is_all_tasks_done,
    is_plan_ready,
    is_review_done,
    is_task_failed,
    parse_plan_draft_payload,
    parse_question_payload,
)
from rlx.status import (
    Mode,
    PhaseFinalize,
    PhaseHolder,
    PhasePlan,
    PhaseReview,
    PhaseTask,
    Section,
    new_claude_review_section,
    new_finalize_section,
    new_plan_iteration_section,
    new_task_iteration_section,
)

MIN_PLAN_ITERATIONS = 5
PLAN_ITERATION_DIVISOR = 5
MIN_REVIEW_ITERATIONS = 3
REVIEW_ITERATION_DIVISOR = 10
_LIMIT_RETRY_MAX = 10


class Executor(Protocol):
    def run(self, prompt: str) -> Result: ...


class Logger(Protocol):
    def print(self, fmt: str, *args: object) -> None: ...
    def print_section(self, section: Section) -> None: ...
    def print_aligned(self, text: str) -> None: ...
    def log_question(self, question: str, options: list[str]) -> None: ...
    def log_answer(self, answer: str) -> None: ...
    def error(self, fmt: str, *args: object) -> None: ...
    def warn(self, fmt: str, *args: object) -> None: ...
    @property
    def path(self) -> str: ...


class InputCollector(Protocol):
    def ask_question(self, question: str, options: list[str]) -> str: ...


class GitChecker(Protocol):
    def head_hash(self) -> str: ...
    def diff_fingerprint(self) -> str: ...


class UserAbortedError(Exception):
    pass


@dataclass
class RunContext:
    mode: Mode
    plan_file: str = ""
    plan_description: str = ""
    progress_path: str = ""
    default_branch: str = ""
    local_dir: Path | None = None
    derived_plan_path: str = ""


@dataclass
class Dependencies:
    executor: Executor
    input_collector: InputCollector
    logger: Logger
    holder: PhaseHolder
    review_executor: Executor | None = None


class Runner:
    def __init__(
        self,
        ctx: RunContext,
        app_cfg: AppConfig,
        deps: Dependencies,
    ) -> None:
        self._ctx = ctx
        self._app = app_cfg
        self._deps = deps
        self._iteration_delay = app_cfg.iteration_delay_ms / 1000.0
        self._wait_on_limit = parse_duration(app_cfg.wait_on_limit)
        self._session_timeout = parse_duration(app_cfg.session_timeout)
        self._task_retry_count = app_cfg.task_retry_count
        self._break_event: threading.Event | None = None
        self._pause_handler: Callable[[], bool] | None = None
        self._git_checker: GitChecker | None = None
        self.last_session_timed_out = False

    def set_break_event(self, event: threading.Event) -> None:
        self._break_event = event

    def set_pause_handler(self, fn: Callable[[], bool]) -> None:
        self._pause_handler = fn

    def set_git_checker(self, checker: GitChecker) -> None:
        self._git_checker = checker

    @property
    def _review_executor(self) -> Executor:
        return self._deps.review_executor or self._deps.executor

    def run(self) -> bool:
        if self._ctx.mode == Mode.PLAN:
            return self.run_plan_creation()
        if self._ctx.mode == Mode.FULL:
            return self.run_full()
        if self._ctx.mode == Mode.REVIEW:
            return self.run_review_only()
        raise ValueError(f"unsupported mode: {self._ctx.mode}")

    def run_full(self) -> bool:
        if not self._ctx.plan_file:
            raise ValueError("run_full requires plan_file")
        self._deps.holder.set(PhaseTask)
        if not self.run_task_phase():
            return False
        self._deps.holder.set(PhaseReview)
        review_prompt = build_review_first_prompt(
            local_dir=self._ctx.local_dir,
            plan_file=self._ctx.plan_file,
            progress_file=self._ctx.progress_path or self._deps.logger.path,
            default_branch=self._ctx.default_branch,
            commit_trailer=self._app.commit_trailer,
            warn=self._deps.logger.warn,
        )
        if not self.run_claude_review(review_prompt):
            return False
        if not self.run_claude_review_loop():
            return False
        self.run_finalize()
        return True

    def run_review_only(self) -> bool:
        self._deps.holder.set(PhaseReview)
        review_prompt = build_review_first_prompt(
            local_dir=self._ctx.local_dir,
            plan_file=self._ctx.plan_file,
            progress_file=self._ctx.progress_path or self._deps.logger.path,
            default_branch=self._ctx.default_branch,
            commit_trailer=self._app.commit_trailer,
            warn=self._deps.logger.warn,
        )
        if not self.run_claude_review(review_prompt):
            return False
        if not self.run_claude_review_loop():
            return False
        self.run_finalize()
        return True

    def run_tasks_only(self) -> bool:
        if not self._ctx.plan_file:
            raise ValueError("run_tasks_only requires plan_file")
        self._deps.holder.set(PhaseTask)
        return self.run_task_phase()

    def run_task_phase(self) -> bool:
        log = self._deps.logger
        claude = self._deps.executor

        prompt = build_task_prompt(
            local_dir=self._ctx.local_dir,
            plan_file=self._ctx.plan_file,
            progress_file=self._ctx.progress_path or log.path,
            default_branch=self._ctx.default_branch,
            commit_trailer=self._app.commit_trailer,
        )

        max_iterations = self._app.max_iterations
        retry_count = 0
        i = 1
        while i <= max_iterations:
            task_num = self.next_plan_task_position()
            if task_num == 0:
                task_num = i
            log.print_section(new_task_iteration_section(task_num))

            result = self._run_with_limit_retry(claude, prompt)

            if self._is_break():
                assert self._break_event is not None
                self._break_event.clear()
                if self._pause_handler is None or not self._pause_handler():
                    raise UserAbortedError("user aborted during break")
                self._break_event.clear()
                retry_count = 0
                continue

            if result.error is not None:
                if isinstance(result.error, (PatternMatchError, LimitPatternError)):
                    self._handle_pattern_match_error(result.error)
                    return False
                log.error("%s", str(result.error))
                raise result.error

            if is_all_tasks_done(result.signal):
                if self.has_uncompleted_tasks():
                    log.warn(
                        "COMPLETED signal received but uncompleted tasks remain"
                    )
                    retry_count = 0
                    self._sleep_with_cancel(self._iteration_delay)
                    i += 1
                    continue
                log.print("all tasks done")
                return True

            if is_task_failed(result.signal):
                if retry_count < self._task_retry_count:
                    retry_count += 1
                    log.warn(
                        "task failed, retrying (%d/%d)",
                        retry_count,
                        self._task_retry_count,
                    )
                    self._sleep_with_cancel(self._iteration_delay)
                    i += 1
                    continue
                log.error("task failed after %d retries", retry_count)
                raise RuntimeError("task execution failed")

            retry_count = 0
            self._sleep_with_cancel(self._iteration_delay)
            i += 1

        log.warn("max iterations reached")
        return False

    def has_uncompleted_tasks(self) -> bool:
        path = self.resolve_plan_file_path()
        if not path:
            return False
        try:
            plan = parse_plan_file(path)
        except OSError:
            return False
        if not plan.tasks:
            return file_has_uncompleted_checkbox(path)
        return any(t.has_uncompleted_actionable_work() for t in plan.tasks)

    def next_plan_task_position(self) -> int:
        path = self.resolve_plan_file_path()
        if not path:
            return 0
        try:
            plan = parse_plan_file(path)
        except OSError:
            return 0
        for idx, task in enumerate(plan.tasks):
            if task.has_uncompleted_actionable_work():
                return idx + 1
        return 0

    def resolve_plan_file_path(self) -> str:
        plan_file = self._ctx.plan_file
        if not plan_file:
            return ""
        try:
            if Path(plan_file).exists():
                return plan_file
        except OSError:
            return plan_file
        completed = completed_plan_path(plan_file)
        if completed.exists():
            return str(completed)
        return plan_file

    def run_claude_review(self, prompt: str) -> bool:
        log = self._deps.logger
        log.print_section(new_claude_review_section(0, "all findings"))

        result = self._run_with_limit_retry(
            self._review_executor, prompt
        )

        if result.error is not None:
            if isinstance(result.error, (PatternMatchError, LimitPatternError)):
                self._handle_pattern_match_error(result.error)
                return False
            log.error("%s", str(result.error))
            raise result.error

        if is_task_failed(result.signal):
            log.error("review reported failure")
            raise RuntimeError("review failed")

        if is_review_done(result.signal):
            log.print("review completed, no issues found")
            return True

        log.warn("review did not complete cleanly")
        return True

    def run_claude_review_loop(self) -> bool:
        log = self._deps.logger
        max_review_iterations = max(
            MIN_REVIEW_ITERATIONS,
            self._app.max_iterations // REVIEW_ITERATION_DIVISOR,
        )

        prompt = build_review_second_prompt(
            local_dir=self._ctx.local_dir,
            plan_file=self._ctx.plan_file,
            progress_file=self._ctx.progress_path or log.path,
            default_branch=self._ctx.default_branch,
            commit_trailer=self._app.commit_trailer,
            warn=log.warn,
        )

        for i in range(1, max_review_iterations + 1):
            log.print_section(new_claude_review_section(i, "critical/major"))

            head_before = (
                self._git_checker.head_hash() if self._git_checker else ""
            )

            result = self._run_with_limit_retry(
                self._review_executor, prompt
            )

            if result.error is not None:
                if isinstance(
                    result.error, (PatternMatchError, LimitPatternError)
                ):
                    self._handle_pattern_match_error(result.error)
                    return False
                log.error("%s", str(result.error))
                raise result.error

            if is_task_failed(result.signal):
                log.error("review reported failure")
                raise RuntimeError("review failed")

            if is_review_done(result.signal):
                log.print("review loop complete, no more findings")
                return True

            if self.last_session_timed_out:
                log.print("session timed out, continuing review loop")
                self._sleep_with_cancel(self._iteration_delay)
                continue

            if self._git_checker is not None:
                head_after = self._git_checker.head_hash()
                if head_after == head_before:
                    log.print("no changes detected, stopping review loop")
                    return True

            log.print("issues fixed, running another review iteration")
            self._sleep_with_cancel(self._iteration_delay)

        log.warn("max review iterations reached")
        return True

    def run_finalize(self) -> None:
        if not self._app.finalize_enabled:
            return
        log = self._deps.logger
        self._deps.holder.set(PhaseFinalize)
        log.print_section(new_finalize_section())

        prompt = build_finalize_prompt(
            local_dir=self._ctx.local_dir,
            plan_file=self._ctx.plan_file,
            progress_file=self._ctx.progress_path or log.path,
            default_branch=self._ctx.default_branch,
            commit_trailer=self._app.commit_trailer,
            warn=log.warn,
        )

        try:
            result = self._run_with_limit_retry(
                self._review_executor, prompt
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.warn("finalize error: %s", exc)
            return

        if result.error is not None:
            if isinstance(
                result.error, (PatternMatchError, LimitPatternError)
            ):
                self._handle_pattern_match_error(result.error)
                return
            log.warn("finalize error: %s", str(result.error))
            return

        if is_task_failed(result.signal):
            log.warn("finalize reported failure")
            return

        log.print("finalize step completed")

    def run_plan_creation(self) -> bool:
        log = self._deps.logger
        claude = self._deps.executor
        self._deps.holder.set(PhasePlan)

        max_plan_iterations = max(
            MIN_PLAN_ITERATIONS,
            self._app.max_iterations // PLAN_ITERATION_DIVISOR,
        )

        for i in range(1, max_plan_iterations + 1):
            log.print_section(new_plan_iteration_section(i))

            prompt = build_plan_prompt(
                self._ctx.plan_description,
                local_dir=self._ctx.local_dir,
                plan_file=self._ctx.plan_file,
                progress_file=self._ctx.progress_path or log.path,
                default_branch=self._ctx.default_branch,
                plans_dir=self._app.plans_dir,
                commit_trailer=self._app.commit_trailer,
                derived_plan_path=self._ctx.derived_plan_path,
            )

            result = self._run_with_limit_retry(claude, prompt)

            if result.error is not None:
                if isinstance(result.error, (PatternMatchError, LimitPatternError)):
                    self._handle_pattern_match_error(result.error)
                    return False
                log.error("%s", str(result.error))
                raise result.error

            if is_task_failed(result.signal):
                log.error("plan creation failed")
                raise RuntimeError("plan creation failed")

            if is_plan_ready(result.signal):
                log.print("plan is ready")
                return True

            if result.idle_timed_out:
                if i < max_plan_iterations:
                    self._sleep_with_cancel(self._iteration_delay)
                continue

            draft = parse_plan_draft_payload(result.output)
            if draft is not None:
                log.print("draft received, auto-accepting")
                if i < max_plan_iterations:
                    self._sleep_with_cancel(self._iteration_delay)
                continue

            qp = parse_question_payload(result.output)
            if qp is not None:
                self._handle_plan_question(qp.question, qp.options)
                if i < max_plan_iterations:
                    self._sleep_with_cancel(self._iteration_delay)
                continue

            log.warn("no recognized signal in response, retrying")
            if i < max_plan_iterations:
                self._sleep_with_cancel(self._iteration_delay)

        log.warn("max plan iterations reached")
        return False

    def _handle_plan_question(self, question: str, options: list[str]) -> None:
        log = self._deps.logger
        log.log_question(question, options)
        answer = self._deps.input_collector.ask_question(question, options)
        log.log_answer(answer)

    def _handle_pattern_match_error(
        self, err: PatternMatchError | LimitPatternError
    ) -> None:
        self._deps.logger.error("pattern matched: %s", err.pattern)

    def _is_break(self) -> bool:
        return self._break_event is not None and self._break_event.is_set()

    def _sleep_with_cancel(self, duration: float) -> None:
        if duration <= 0:
            return
        event = self._break_event
        if event is None:
            event = threading.Event()
        event.wait(duration)

    def _run_with_session_timeout(
        self,
        executor: Executor,
        prompt: str,
    ) -> Result:
        if self._session_timeout <= 0:
            result = executor.run(prompt)
            self.last_session_timed_out = (
                result.idle_timed_out and not result.signal
            )
            return result

        cancel_fn = getattr(executor, "cancel", None)
        timed_out = threading.Event()
        completed = threading.Event()
        state_lock = threading.Lock()

        def on_timeout() -> None:
            with state_lock:
                if completed.is_set():
                    return
                timed_out.set()
                if callable(cancel_fn):
                    with contextlib.suppress(Exception):
                        cancel_fn()

        timer = threading.Timer(self._session_timeout, on_timeout)
        timer.daemon = True
        timer.start()
        try:
            result = executor.run(prompt)
        finally:
            with state_lock:
                completed.set()
                timer.cancel()

        if timed_out.is_set():
            result.error = None
            result.signal = ""
            result.idle_timed_out = True
            self.last_session_timed_out = True
            return result

        self.last_session_timed_out = (
            result.idle_timed_out and not result.signal
        )
        return result

    def _run_with_limit_retry(
        self,
        executor: Executor,
        prompt: str,
    ) -> Result:
        result = self._run_with_session_timeout(executor, prompt)
        for _ in range(_LIMIT_RETRY_MAX):
            if result.error is None:
                return result
            if not isinstance(result.error, LimitPatternError):
                return result
            if self._wait_on_limit <= 0:
                return result
            self._deps.logger.warn("rate limit detected, waiting...")
            self._sleep_with_cancel(self._wait_on_limit)
            result = self._run_with_session_timeout(executor, prompt)
        return result


__all__ = [
    "MIN_PLAN_ITERATIONS",
    "MIN_REVIEW_ITERATIONS",
    "PLAN_ITERATION_DIVISOR",
    "REVIEW_ITERATION_DIVISOR",
    "Dependencies",
    "Executor",
    "GitChecker",
    "InputCollector",
    "Logger",
    "RunContext",
    "Runner",
    "UserAbortedError",
]
