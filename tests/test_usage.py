from __future__ import annotations

import pytest

from cadence.executor.events import Usage
from cadence.usage import (
    PRICING_USD_PER_MTOK,
    UsageStats,
    estimate_cost,
    format_chain_summary,
    format_duration_ms,
    format_iteration_summary,
    format_phase_summary,
    format_token_count,
)


class TestUsageStatsAdd:
    def test_add_with_usage_accumulates_and_sets_had_usage(self) -> None:
        stats = UsageStats()
        stats.add(
            Usage(
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=200,
                cache_creation_tokens=10,
            ),
            duration_ms=1500,
        )
        assert stats.iterations == 1
        assert stats.duration_ms == 1500
        assert stats.input == 100
        assert stats.output == 50
        assert stats.cache_read == 200
        assert stats.cache_creation == 10
        assert stats.had_usage is True

    def test_add_with_none_increments_iters_and_duration_only(self) -> None:
        stats = UsageStats()
        stats.add(None, duration_ms=500)
        assert stats.iterations == 1
        assert stats.duration_ms == 500
        assert stats.input == 0
        assert stats.output == 0
        assert stats.cache_read == 0
        assert stats.cache_creation == 0
        assert stats.had_usage is False

    def test_add_called_twice_sums(self) -> None:
        stats = UsageStats()
        stats.add(Usage(input_tokens=10, output_tokens=20), duration_ms=100)
        stats.add(Usage(input_tokens=5, output_tokens=7), duration_ms=300)
        assert stats.iterations == 2
        assert stats.duration_ms == 400
        assert stats.input == 15
        assert stats.output == 27
        assert stats.had_usage is True


class TestUsageStatsMerge:
    def test_merge_sums_every_field_and_ors_had_usage(self) -> None:
        a = UsageStats(
            iterations=1,
            duration_ms=1000,
            input=100,
            output=50,
            cache_read=200,
            cache_creation=10,
            had_usage=True,
            cost_usd=0.5,
            cost_known=True,
        )
        b = UsageStats(
            iterations=2,
            duration_ms=2000,
            input=300,
            output=150,
            cache_read=400,
            cache_creation=20,
            had_usage=False,
            cost_usd=0.0,
            cost_known=False,
        )
        a.merge(b)
        assert a.iterations == 3
        assert a.duration_ms == 3000
        assert a.input == 400
        assert a.output == 200
        assert a.cache_read == 600
        assert a.cache_creation == 30
        assert a.had_usage is True
        assert a.cost_usd == pytest.approx(0.5)
        assert a.cost_known is True

    def test_merge_ors_had_usage_false_into_true(self) -> None:
        a = UsageStats(had_usage=False)
        b = UsageStats(had_usage=True)
        a.merge(b)
        assert a.had_usage is True

    def test_merge_ors_cost_known(self) -> None:
        a = UsageStats(cost_usd=1.0, cost_known=False)
        b = UsageStats(cost_usd=2.5, cost_known=True)
        a.merge(b)
        assert a.cost_usd == pytest.approx(3.5)
        assert a.cost_known is True


class TestUsageStatsSetCost:
    def test_set_cost_with_value_marks_known(self) -> None:
        stats = UsageStats()
        stats.set_cost(1.23)
        assert stats.cost_usd == pytest.approx(1.23)
        assert stats.cost_known is True

    def test_set_cost_with_none_is_noop(self) -> None:
        stats = UsageStats()
        stats.set_cost(None)
        assert stats.cost_usd == 0.0
        assert stats.cost_known is False


class TestEstimateCost:
    def test_opus_arithmetic(self) -> None:
        stats = UsageStats(input=1_000_000, output=0, cache_read=0, cache_creation=0)
        assert estimate_cost(stats, "claude-opus-4-7") == pytest.approx(15.0, abs=0.001)

    def test_sonnet_arithmetic(self) -> None:
        stats = UsageStats(input=0, output=1_000_000, cache_read=0, cache_creation=0)
        assert estimate_cost(stats, "claude-sonnet-4-6") == pytest.approx(15.0, abs=0.001)

    def test_haiku_arithmetic(self) -> None:
        stats = UsageStats(input=0, output=0, cache_read=1_000_000, cache_creation=0)
        assert estimate_cost(stats, "claude-haiku-4-5") == pytest.approx(0.1, abs=0.001)

    def test_mixed_components(self) -> None:
        stats = UsageStats(input=124_000, output=8_200, cache_read=612_000, cache_creation=4_100)
        # opus: 124000*15 + 8200*75 + 612000*1.5 + 4100*18.75 = 3_469_875 / 1M
        assert estimate_cost(stats, "claude-opus-4-7") == pytest.approx(3.469875, abs=0.001)

    def test_unknown_model_returns_none(self) -> None:
        stats = UsageStats(input=100)
        assert estimate_cost(stats, "claude-fictitious-9-9") is None

    def test_empty_model_returns_none(self) -> None:
        stats = UsageStats(input=100)
        assert estimate_cost(stats, "") is None

    def test_dated_suffix_resolves_to_base(self) -> None:
        stats = UsageStats(input=1_000_000)
        assert estimate_cost(stats, "claude-opus-4-7-20250514") == pytest.approx(15.0, abs=0.001)

    def test_pricing_table_has_expected_models(self) -> None:
        assert set(PRICING_USD_PER_MTOK) == {
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
        }


class TestFormatTokenCount:
    @pytest.mark.parametrize(
        "n,expected",
        [
            (0, "0"),
            (1, "1"),
            (999, "999"),
            (1000, "1k"),
            (1500, "1.5k"),
            (8200, "8.2k"),
            (124000, "124k"),
            (999_999, "1000k"),
            (1_000_000, "1M"),
            (1_200_000, "1.2M"),
        ],
    )
    def test_boundaries(self, n: int, expected: str) -> None:
        assert format_token_count(n) == expected


class TestFormatDurationMs:
    @pytest.mark.parametrize(
        "ms,expected",
        [
            (0, "0s"),
            (999, "0s"),
            (1000, "1s"),
            (59_000, "59s"),
            (60_000, "1m 0s"),
            (133_000, "2m 13s"),
            (3_599_000, "59m 59s"),
            (3_600_000, "1h 0m 0s"),
            (3_661_000, "1h 1m 1s"),
        ],
    )
    def test_boundaries(self, ms: int, expected: str) -> None:
        assert format_duration_ms(ms) == expected


class TestFormatIterationSummary:
    def test_full_usage_with_opus_model(self) -> None:
        stats = UsageStats(
            iterations=1,
            duration_ms=133_000,
            input=124_000,
            output=8_200,
            cache_read=612_000,
            cache_creation=4_100,
            had_usage=True,
        )
        line = format_iteration_summary(
            stats,
            "claude-opus-4-7",
            session_id="abc123",
            iteration=3,
            cost_estimates=True,
        )
        assert line == (
            "iter 3 done in 2m 13s · in 124k · out 8.2k · cache_read 612k · "
            "cache_create 4.1k · cost ≈ $3.47 · session abc123"
        )

    def test_drops_cache_create_when_zero(self) -> None:
        stats = UsageStats(
            iterations=1,
            duration_ms=2_000,
            input=124_000,
            output=8_200,
            cache_read=612_000,
            cache_creation=0,
            had_usage=True,
        )
        line = format_iteration_summary(
            stats,
            "claude-opus-4-7",
            session_id="abc",
            iteration=1,
            cost_estimates=True,
        )
        assert "cache_create" not in line
        assert "cache_read 612k" in line

    def test_drops_session_when_empty(self) -> None:
        stats = UsageStats(iterations=1, duration_ms=1000, input=10, had_usage=True)
        line = format_iteration_summary(
            stats,
            "claude-opus-4-7",
            session_id="",
            iteration=1,
            cost_estimates=True,
        )
        assert "session" not in line

    def test_missing_usage_replaces_token_block(self) -> None:
        stats = UsageStats(iterations=1, duration_ms=1000, had_usage=False)
        line = format_iteration_summary(
            stats,
            "claude-opus-4-7",
            session_id="abc",
            iteration=2,
            cost_estimates=True,
        )
        assert line == "iter 2 done in 1s · usage unavailable · session abc"

    def test_unknown_model_shows_question_mark_cost(self) -> None:
        stats = UsageStats(
            iterations=1,
            duration_ms=1000,
            input=100,
            output=50,
            cache_read=200,
            had_usage=True,
        )
        line = format_iteration_summary(
            stats,
            "claude-mystery-9-9",
            session_id="",
            iteration=1,
            cost_estimates=True,
        )
        assert "cost ≈ ?" in line
        assert "in 100" in line
        assert "out 50" in line

    def test_cost_estimates_false_drops_cost_segment(self) -> None:
        stats = UsageStats(
            iterations=1,
            duration_ms=1000,
            input=100,
            output=50,
            had_usage=True,
        )
        line = format_iteration_summary(
            stats,
            "claude-opus-4-7",
            session_id="",
            iteration=1,
            cost_estimates=False,
        )
        assert "cost" not in line
        assert "in 100" in line
        assert "out 50" in line


class TestFormatPhaseSummary:
    def test_full_golden_with_opus(self) -> None:
        stats = UsageStats(
            iterations=7,
            duration_ms=872_000,
            input=1_200_000,
            output=80_000,
            cache_read=4_100_000,
            cache_creation=0,
            had_usage=True,
        )
        line = format_phase_summary(stats, "claude-opus-4-7", "task", cost_estimates=True)
        # cost: 1.2M*15 + 80k*75 + 4.1M*1.5 = 18M + 6M + 6.15M = 30.15M / 1M = $30.15
        assert line == (
            "phase task done in 14m 32s · iters 7 · in 1.2M · out 80k · "
            "cache_read 4.1M · cost ≈ $30.15"
        )

    def test_missing_usage(self) -> None:
        stats = UsageStats(iterations=3, duration_ms=5000, had_usage=False)
        line = format_phase_summary(stats, "claude-opus-4-7", "review", cost_estimates=True)
        assert line == "phase review done in 5s · iters 3 · usage unavailable"

    def test_unknown_model(self) -> None:
        stats = UsageStats(iterations=2, duration_ms=2000, input=100, had_usage=True)
        line = format_phase_summary(stats, "claude-mystery-9-9", "plan", cost_estimates=True)
        assert "cost ≈ ?" in line

    def test_cost_estimates_false(self) -> None:
        stats = UsageStats(iterations=2, duration_ms=2000, input=100, had_usage=True)
        line = format_phase_summary(stats, "claude-opus-4-7", "plan", cost_estimates=False)
        assert "cost" not in line
        assert "iters 2" in line


class TestFormatChainSummary:
    def test_full_golden_with_known_cost(self) -> None:
        stats = UsageStats(
            iterations=10,
            duration_ms=900_000,
            input=2_000_000,
            output=120_000,
            cache_read=5_000_000,
            cache_creation=50_000,
            had_usage=True,
            cost_usd=42.50,
            cost_known=True,
        )
        line = format_chain_summary(stats, cost_estimates=True, tasks=2)
        assert line == (
            "chain done in 15m 0s · tasks 2 · iters 10 · in 2M · out 120k · "
            "cache_read 5M · cache_create 50k · cost ≈ $42.50"
        )

    def test_unknown_cost_renders_question_mark(self) -> None:
        stats = UsageStats(
            iterations=1,
            duration_ms=1000,
            input=100,
            had_usage=True,
            cost_known=False,
        )
        line = format_chain_summary(stats, cost_estimates=True, tasks=1)
        assert "cost ≈ ?" in line

    def test_cost_estimates_false(self) -> None:
        stats = UsageStats(
            iterations=1,
            duration_ms=1000,
            input=100,
            had_usage=True,
            cost_usd=1.0,
            cost_known=True,
        )
        line = format_chain_summary(stats, cost_estimates=False, tasks=1)
        assert "cost" not in line

    def test_missing_usage(self) -> None:
        stats = UsageStats(iterations=2, duration_ms=2000, had_usage=False)
        line = format_chain_summary(stats, cost_estimates=True, tasks=2)
        assert line == "chain done in 2s · tasks 2 · iters 2 · usage unavailable"
