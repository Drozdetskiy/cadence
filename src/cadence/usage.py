from __future__ import annotations

from dataclasses import dataclass

from cadence.executor.events import Usage

PRICING_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25},
}


@dataclass
class UsageStats:
    iterations: int = 0
    duration_ms: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    had_usage: bool = False
    cost_usd: float = 0.0
    cost_known: bool = False

    def add(self, usage: Usage | None, *, duration_ms: int) -> None:
        self.iterations += 1
        self.duration_ms += duration_ms
        if usage is not None:
            self.input += usage.input_tokens
            self.output += usage.output_tokens
            self.cache_read += usage.cache_read_tokens
            self.cache_creation += usage.cache_creation_tokens
            self.had_usage = True

    def merge(self, other: UsageStats) -> None:
        self.iterations += other.iterations
        self.duration_ms += other.duration_ms
        self.input += other.input
        self.output += other.output
        self.cache_read += other.cache_read
        self.cache_creation += other.cache_creation
        self.had_usage = self.had_usage or other.had_usage
        self.cost_usd += other.cost_usd
        self.cost_known = self.cost_known or other.cost_known

    def set_cost(self, cost: float | None) -> None:
        if cost is None:
            return
        self.cost_usd = cost
        self.cost_known = True


def _lookup_pricing(model: str) -> dict[str, float] | None:
    if not model:
        return None
    if model in PRICING_USD_PER_MTOK:
        return PRICING_USD_PER_MTOK[model]
    base, sep, suffix = model.rpartition("-")
    if sep and suffix.isdigit() and len(suffix) == 8:
        return PRICING_USD_PER_MTOK.get(base)
    return None


def estimate_cost(stats: UsageStats, model: str) -> float | None:
    pricing = _lookup_pricing(model)
    if pricing is None:
        return None
    return (
        stats.input * pricing["input"]
        + stats.output * pricing["output"]
        + stats.cache_read * pricing["cache_read"]
        + stats.cache_creation * pricing["cache_write"]
    ) / 1_000_000


def format_duration_ms(ms: int) -> str:
    if ms < 0:
        ms = 0
    total_seconds = ms // 1000
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {seconds}s"


def format_token_count(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        rounded = round(n / 1000.0, 1)
        if rounded == int(rounded):
            return f"{int(rounded)}k"
        return f"{rounded:.1f}k"
    rounded = round(n / 1_000_000.0, 1)
    if rounded == int(rounded):
        return f"{int(rounded)}M"
    return f"{rounded:.1f}M"


def _format_cost_segment(cost: float | None) -> str:
    if cost is None:
        return "cost ≈ ?"
    return f"cost ≈ ${cost:.2f}"


def _format_token_block(stats: UsageStats) -> str:
    parts = [
        f"in {format_token_count(stats.input)}",
        f"out {format_token_count(stats.output)}",
        f"cache_read {format_token_count(stats.cache_read)}",
    ]
    if stats.cache_creation > 0:
        parts.append(f"cache_create {format_token_count(stats.cache_creation)}")
    return " · ".join(parts)


def format_iteration_summary(
    stats_for_iter: UsageStats,
    model: str,
    *,
    session_id: str,
    iteration: int,
    cost_estimates: bool,
) -> str:
    parts = [f"iter {iteration} done in {format_duration_ms(stats_for_iter.duration_ms)}"]
    if not stats_for_iter.had_usage:
        parts.append("usage unavailable")
    else:
        parts.append(_format_token_block(stats_for_iter))
        if cost_estimates:
            parts.append(_format_cost_segment(estimate_cost(stats_for_iter, model)))
    if session_id:
        parts.append(f"session {session_id}")
    return " · ".join(parts)


def format_phase_summary(
    stats: UsageStats,
    model: str,
    phase: str,
    *,
    cost_estimates: bool,
) -> str:
    parts = [
        f"phase {phase} done in {format_duration_ms(stats.duration_ms)}",
        f"iters {stats.iterations}",
    ]
    if not stats.had_usage:
        parts.append("usage unavailable")
    else:
        parts.append(_format_token_block(stats))
        if cost_estimates:
            parts.append(_format_cost_segment(estimate_cost(stats, model)))
    return " · ".join(parts)


def format_chain_summary(
    stats: UsageStats,
    *,
    cost_estimates: bool,
    tasks: int,
) -> str:
    parts = [
        f"chain done in {format_duration_ms(stats.duration_ms)}",
        f"tasks {tasks}",
        f"iters {stats.iterations}",
    ]
    if not stats.had_usage:
        parts.append("usage unavailable")
    else:
        parts.append(_format_token_block(stats))
        if cost_estimates:
            cost = stats.cost_usd if stats.cost_known else None
            parts.append(_format_cost_segment(cost))
    return " · ".join(parts)
