from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


def now_ts() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class PhaseStartEvent:
    ts: str
    phase: str
    branch: str
    model: str
    event: str = "phase_start"

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "phase": self.phase,
            "event": self.event,
            "branch": self.branch,
            "model": self.model,
        }


@dataclass(frozen=True)
class PhaseEndEvent:
    ts: str
    phase: str
    duration_ms: int
    iterations: int
    result: str
    tokens_in_total: int
    tokens_out_total: int
    cost_usd_estimate: float | None = None
    event: str = "phase_end"

    def to_jsonl_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "ts": self.ts,
            "phase": self.phase,
            "event": self.event,
            "duration_ms": self.duration_ms,
            "iterations": self.iterations,
            "result": self.result,
            "tokens_in_total": self.tokens_in_total,
            "tokens_out_total": self.tokens_out_total,
        }
        if self.cost_usd_estimate is not None:
            d["cost_usd_estimate"] = self.cost_usd_estimate
        return d


@dataclass(frozen=True)
class IterationStartEvent:
    ts: str
    phase: str
    iteration: int
    task_index: int | None = None
    event: str = "iteration_start"

    def to_jsonl_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "ts": self.ts,
            "phase": self.phase,
            "event": self.event,
            "iteration": self.iteration,
        }
        if self.task_index is not None:
            d["task_index"] = self.task_index
        return d


@dataclass(frozen=True)
class IterationEndEvent:
    ts: str
    phase: str
    iteration: int
    duration_ms: int
    session_id: str
    tokens_in: int
    tokens_out: int
    cost_usd_estimate: float | None = None
    event: str = "iteration_end"

    def to_jsonl_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "ts": self.ts,
            "phase": self.phase,
            "event": self.event,
            "iteration": self.iteration,
            "duration_ms": self.duration_ms,
            "session_id": self.session_id,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }
        if self.cost_usd_estimate is not None:
            d["cost_usd_estimate"] = self.cost_usd_estimate
        return d


@dataclass(frozen=True)
class SignalEvent:
    ts: str
    phase: str
    iteration: int
    signal: str
    event: str = "signal"

    def to_jsonl_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "phase": self.phase,
            "event": self.event,
            "iteration": self.iteration,
            "signal": self.signal,
        }


@dataclass(frozen=True)
class ErrorEvent:
    ts: str
    phase: str
    message: str
    iteration: int | None = None
    event: str = "error"

    def to_jsonl_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "ts": self.ts,
            "phase": self.phase,
            "event": self.event,
            "message": self.message,
        }
        if self.iteration is not None:
            d["iteration"] = self.iteration
        return d
