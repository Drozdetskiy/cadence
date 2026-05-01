from __future__ import annotations

from cadence.processor.signals import (
    is_all_tasks_done,
    is_plan_ready,
    is_review_done,
    is_task_failed,
    parse_plan_draft_payload,
    parse_question_payload,
)
from cadence.status import (
    SignalCompleted,
    SignalFailed,
    SignalPlanReady,
    SignalReviewDone,
)


class TestParseQuestionPayload:
    def test_valid_question(self) -> None:
        output = (
            'Some text\n'
            '<<<CADENCE:QUESTION>>>\n'
            '{"question": "Which DB?", "options": ["Postgres", "SQLite"]}\n'
            '<<<CADENCE:END>>>\n'
            'More text'
        )
        result = parse_question_payload(output)
        assert result is not None
        assert result.question == "Which DB?"
        assert result.options == ["Postgres", "SQLite"]

    def test_no_question_signal(self) -> None:
        assert parse_question_payload("just some output") is None

    def test_question_signal_but_no_end(self) -> None:
        output = '<<<CADENCE:QUESTION>>>\n{"question": "x", "options": ["a"]}'
        assert parse_question_payload(output) is None

    def test_malformed_json(self) -> None:
        output = (
            '<<<CADENCE:QUESTION>>>\n'
            'not json\n'
            '<<<CADENCE:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_missing_question_field(self) -> None:
        output = (
            '<<<CADENCE:QUESTION>>>\n'
            '{"options": ["a", "b"]}\n'
            '<<<CADENCE:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_missing_options_field(self) -> None:
        output = (
            '<<<CADENCE:QUESTION>>>\n'
            '{"question": "Which?"}\n'
            '<<<CADENCE:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_empty_question(self) -> None:
        output = (
            '<<<CADENCE:QUESTION>>>\n'
            '{"question": "", "options": ["a"]}\n'
            '<<<CADENCE:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_empty_options(self) -> None:
        output = (
            '<<<CADENCE:QUESTION>>>\n'
            '{"question": "Which?", "options": []}\n'
            '<<<CADENCE:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_json_not_dict(self) -> None:
        output = (
            '<<<CADENCE:QUESTION>>>\n'
            '["a", "b"]\n'
            '<<<CADENCE:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_empty_body(self) -> None:
        output = (
            '<<<CADENCE:QUESTION>>>\n'
            '   \n'
            '<<<CADENCE:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_multiple_options(self) -> None:
        output = (
            '<<<CADENCE:QUESTION>>>\n'
            '{"question": "Pick", "options": ["A", "B", "C", "D"]}\n'
            '<<<CADENCE:END>>>'
        )
        result = parse_question_payload(output)
        assert result is not None
        assert len(result.options) == 4


class TestParsePlanDraftPayload:
    def test_valid_draft(self) -> None:
        output = (
            'Preamble\n'
            '<<<CADENCE:PLAN_DRAFT>>>\n'
            '# My Plan\n'
            '## Overview\n'
            'Do stuff\n'
            '<<<CADENCE:END>>>\n'
            'Epilogue'
        )
        result = parse_plan_draft_payload(output)
        assert result is not None
        assert "# My Plan" in result
        assert "Do stuff" in result

    def test_no_draft_signal(self) -> None:
        assert parse_plan_draft_payload("no draft here") is None

    def test_draft_signal_but_no_end(self) -> None:
        output = '<<<CADENCE:PLAN_DRAFT>>>\n# Plan\nContent'
        assert parse_plan_draft_payload(output) is None

    def test_empty_draft_content(self) -> None:
        output = (
            '<<<CADENCE:PLAN_DRAFT>>>\n'
            '   \n'
            '<<<CADENCE:END>>>'
        )
        assert parse_plan_draft_payload(output) is None

    def test_multiline_content(self) -> None:
        content = "# Title\n\n## Section\n- item 1\n- item 2"
        output = (
            f"<<<CADENCE:PLAN_DRAFT>>>\n"
            f"{content}\n"
            f"<<<CADENCE:END>>>"
        )
        result = parse_plan_draft_payload(output)
        assert result is not None
        assert "# Title" in result
        assert "- item 1" in result


class TestIsPlanReady:
    def test_plan_ready(self) -> None:
        assert is_plan_ready(SignalPlanReady) is True

    def test_not_plan_ready(self) -> None:
        assert is_plan_ready("<<<CADENCE:TASK_FAILED>>>") is False

    def test_empty(self) -> None:
        assert is_plan_ready("") is False


class TestIsReviewDone:
    def test_review_done(self) -> None:
        assert is_review_done(SignalReviewDone) is True

    def test_not_review_done(self) -> None:
        assert is_review_done(SignalCompleted) is False
        assert is_review_done(SignalFailed) is False

    def test_empty(self) -> None:
        assert is_review_done("") is False


class TestIsTaskFailed:
    def test_task_failed(self) -> None:
        assert is_task_failed(SignalFailed) is True

    def test_not_task_failed(self) -> None:
        assert is_task_failed(SignalCompleted) is False
        assert is_task_failed(SignalReviewDone) is False

    def test_empty(self) -> None:
        assert is_task_failed("") is False


class TestIsAllTasksDone:
    def test_all_tasks_done(self) -> None:
        assert is_all_tasks_done(SignalCompleted) is True

    def test_not_all_tasks_done(self) -> None:
        assert is_all_tasks_done(SignalFailed) is False
        assert is_all_tasks_done(SignalReviewDone) is False

    def test_empty(self) -> None:
        assert is_all_tasks_done("") is False


