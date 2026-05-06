from __future__ import annotations

import json
import re

from cadence.progress.events import (
    ErrorEvent,
    IterationEndEvent,
    IterationStartEvent,
    PhaseEndEvent,
    PhaseStartEvent,
    SignalEvent,
    now_ts,
)


def _roundtrip(payload: dict[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def test_now_ts_is_utc_iso8601_z() -> None:
    ts = now_ts()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), ts


def test_phase_start_round_trip() -> None:
    ev = PhaseStartEvent(
        ts="2026-05-06T10:23:14Z", phase="task", branch="0043-x", model="claude-opus-4-7"
    )
    out = _roundtrip(ev.to_jsonl_dict())
    assert out == {
        "ts": "2026-05-06T10:23:14Z",
        "phase": "task",
        "event": "phase_start",
        "branch": "0043-x",
        "model": "claude-opus-4-7",
    }


def test_phase_end_round_trip_with_cost() -> None:
    ev = PhaseEndEvent(
        ts="2026-05-06T10:25:00Z",
        phase="task",
        duration_ms=125000,
        iterations=3,
        result="success",
        tokens_in_total=1000,
        tokens_out_total=500,
        cost_usd_estimate=0.0123,
    )
    out = _roundtrip(ev.to_jsonl_dict())
    assert out["event"] == "phase_end"
    assert out["result"] == "success"
    assert out["duration_ms"] == 125000
    assert out["iterations"] == 3
    assert out["tokens_in_total"] == 1000
    assert out["tokens_out_total"] == 500
    assert out["cost_usd_estimate"] == 0.0123


def test_phase_end_drops_cost_when_none() -> None:
    ev = PhaseEndEvent(
        ts="2026-05-06T10:25:00Z",
        phase="plan",
        duration_ms=1000,
        iterations=1,
        result="failure",
        tokens_in_total=10,
        tokens_out_total=20,
        cost_usd_estimate=None,
    )
    out = _roundtrip(ev.to_jsonl_dict())
    assert "cost_usd_estimate" not in out
    assert out["result"] == "failure"


def test_iteration_start_with_task_index() -> None:
    ev = IterationStartEvent(ts="2026-05-06T10:23:14Z", phase="task", iteration=2, task_index=5)
    out = _roundtrip(ev.to_jsonl_dict())
    assert out == {
        "ts": "2026-05-06T10:23:14Z",
        "phase": "task",
        "event": "iteration_start",
        "iteration": 2,
        "task_index": 5,
    }


def test_iteration_start_drops_task_index_when_none() -> None:
    ev = IterationStartEvent(ts="2026-05-06T10:23:14Z", phase="plan", iteration=1)
    out = _roundtrip(ev.to_jsonl_dict())
    assert "task_index" not in out
    assert out["event"] == "iteration_start"
    assert out["iteration"] == 1


def test_iteration_end_round_trip_with_cost() -> None:
    ev = IterationEndEvent(
        ts="2026-05-06T10:24:00Z",
        phase="review",
        iteration=1,
        duration_ms=42000,
        session_id="sess-abc",
        tokens_in=200,
        tokens_out=100,
        cost_usd_estimate=0.005,
    )
    out = _roundtrip(ev.to_jsonl_dict())
    assert out["event"] == "iteration_end"
    assert out["session_id"] == "sess-abc"
    assert out["tokens_in"] == 200
    assert out["tokens_out"] == 100
    assert out["cost_usd_estimate"] == 0.005


def test_iteration_end_drops_cost_when_none() -> None:
    ev = IterationEndEvent(
        ts="2026-05-06T10:24:00Z",
        phase="task",
        iteration=2,
        duration_ms=1000,
        session_id="sess-xyz",
        tokens_in=0,
        tokens_out=0,
        cost_usd_estimate=None,
    )
    out = _roundtrip(ev.to_jsonl_dict())
    assert "cost_usd_estimate" not in out


def test_signal_round_trip() -> None:
    ev = SignalEvent(ts="2026-05-06T10:23:14Z", phase="task", iteration=4, signal="ALL_TASKS_DONE")
    out = _roundtrip(ev.to_jsonl_dict())
    assert out == {
        "ts": "2026-05-06T10:23:14Z",
        "phase": "task",
        "event": "signal",
        "iteration": 4,
        "signal": "ALL_TASKS_DONE",
    }


def test_error_with_iteration() -> None:
    ev = ErrorEvent(ts="2026-05-06T10:23:14Z", phase="task", message="boom", iteration=3)
    out = _roundtrip(ev.to_jsonl_dict())
    assert out == {
        "ts": "2026-05-06T10:23:14Z",
        "phase": "task",
        "event": "error",
        "message": "boom",
        "iteration": 3,
    }


def test_error_drops_iteration_when_none() -> None:
    ev = ErrorEvent(ts="2026-05-06T10:23:14Z", phase="plan", message="setup failed")
    out = _roundtrip(ev.to_jsonl_dict())
    assert "iteration" not in out
    assert out["event"] == "error"
    assert out["message"] == "setup failed"


def test_event_discriminators_match_classes() -> None:
    pairs: list[tuple[object, str]] = [
        (PhaseStartEvent(ts="t", phase="p", branch="b", model="m"), "phase_start"),
        (
            PhaseEndEvent(
                ts="t",
                phase="p",
                duration_ms=0,
                iterations=0,
                result="success",
                tokens_in_total=0,
                tokens_out_total=0,
            ),
            "phase_end",
        ),
        (IterationStartEvent(ts="t", phase="p", iteration=0), "iteration_start"),
        (
            IterationEndEvent(
                ts="t",
                phase="p",
                iteration=0,
                duration_ms=0,
                session_id="s",
                tokens_in=0,
                tokens_out=0,
            ),
            "iteration_end",
        ),
        (SignalEvent(ts="t", phase="p", iteration=0, signal="S"), "signal"),
        (ErrorEvent(ts="t", phase="p", message="m"), "error"),
    ]
    for ev, expected in pairs:
        out = ev.to_jsonl_dict()  # type: ignore[attr-defined]
        assert out["event"] == expected
