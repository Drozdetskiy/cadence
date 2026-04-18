from __future__ import annotations

import time
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
from rlx.processor.prompts import build_plan_prompt
from rlx.processor.signals import (
    is_plan_ready,
    parse_plan_draft_payload,
    parse_question_payload,
)
from rlx.status import (
    Mode,
    PhaseHolder,
    PhasePlan,
    Section,
    SignalFailed,
    new_plan_iteration_section,
)

MIN_PLAN_ITERATIONS = 5
PLAN_ITERATION_DIVISOR = 5


class Executor(Protocol):
    def run(self, prompt: str) -> Result: ...


class Logger(Protocol):
    def print(self, fmt: str, *args: object) -> None: ...
    def print_section(self, section: Section) -> None: ...
    def log_question(self, question: str, options: list[str]) -> None: ...
    def log_answer(self, answer: str) -> None: ...
    def error(self, fmt: str, *args: object) -> None: ...
    def warn(self, fmt: str, *args: object) -> None: ...
    @property
    def path(self) -> str: ...


class InputCollector(Protocol):
    def ask_question(self, question: str, options: list[str]) -> str: ...


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


@dataclass
class Dependencies:
    executor: Executor
    input_collector: InputCollector
    logger: Logger
    holder: PhaseHolder


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

    def run(self) -> bool:
        if self._ctx.mode == Mode.PLAN:
            return self.run_plan_creation()
        raise ValueError(f"unsupported mode: {self._ctx.mode}")

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
            )

            result = self._run_with_limit_retry(claude.run, prompt)

            if result.error is not None:
                if isinstance(result.error, (PatternMatchError, LimitPatternError)):
                    self._handle_pattern_match_error(result.error)
                    return False
                log.error("%s", str(result.error))
                raise result.error

            if result.signal == SignalFailed:
                log.error("plan creation failed")
                raise RuntimeError("plan creation failed")

            if is_plan_ready(result.signal):
                log.print("plan is ready")
                return True

            if result.idle_timed_out:
                if i < max_plan_iterations:
                    time.sleep(self._iteration_delay)
                continue

            draft = parse_plan_draft_payload(result.output)
            if draft is not None:
                log.print("draft received, auto-accepting")
                if i < max_plan_iterations:
                    time.sleep(self._iteration_delay)
                continue

            qp = parse_question_payload(result.output)
            if qp is not None:
                self._handle_plan_question(qp.question, qp.options)
                if i < max_plan_iterations:
                    time.sleep(self._iteration_delay)
                continue

            log.warn("no recognized signal in response, retrying")
            if i < max_plan_iterations:
                time.sleep(self._iteration_delay)

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

    def _run_with_limit_retry(
        self,
        run_fn: Callable[[str], Result],
        prompt: str,
        *,
        max_retries: int = 10,
    ) -> Result:
        result = run_fn(prompt)
        for _ in range(max_retries):
            if result.error is None:
                return result
            if not isinstance(result.error, LimitPatternError):
                return result
            if self._wait_on_limit <= 0:
                return result
            self._deps.logger.warn("rate limit detected, waiting...")
            time.sleep(self._wait_on_limit)
            result = run_fn(prompt)
        return result
