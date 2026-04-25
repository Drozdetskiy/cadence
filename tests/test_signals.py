from __future__ import annotations

from rlx.processor.signals import (
    is_all_tasks_done,
    is_plan_ready,
    is_review_done,
    is_task_failed,
    parse_plan_draft_payload,
    parse_question_payload,
)
from rlx.status import (
    SignalCompleted,
    SignalFailed,
    SignalPlanReady,
    SignalReviewDone,
)


class TestParseQuestionPayload:
    def test_valid_question(self) -> None:
        output = (
            'Some text\n'
            '<<<RLX:QUESTION>>>\n'
            '{"question": "Which DB?", "options": ["Postgres", "SQLite"]}\n'
            '<<<RLX:END>>>\n'
            'More text'
        )
        result = parse_question_payload(output)
        assert result is not None
        assert result.question == "Which DB?"
        assert result.options == ["Postgres", "SQLite"]

    def test_no_question_signal(self) -> None:
        assert parse_question_payload("just some output") is None

    def test_question_signal_but_no_end(self) -> None:
        output = '<<<RLX:QUESTION>>>\n{"question": "x", "options": ["a"]}'
        assert parse_question_payload(output) is None

    def test_malformed_json(self) -> None:
        output = (
            '<<<RLX:QUESTION>>>\n'
            'not json\n'
            '<<<RLX:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_missing_question_field(self) -> None:
        output = (
            '<<<RLX:QUESTION>>>\n'
            '{"options": ["a", "b"]}\n'
            '<<<RLX:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_missing_options_field(self) -> None:
        output = (
            '<<<RLX:QUESTION>>>\n'
            '{"question": "Which?"}\n'
            '<<<RLX:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_empty_question(self) -> None:
        output = (
            '<<<RLX:QUESTION>>>\n'
            '{"question": "", "options": ["a"]}\n'
            '<<<RLX:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_empty_options(self) -> None:
        output = (
            '<<<RLX:QUESTION>>>\n'
            '{"question": "Which?", "options": []}\n'
            '<<<RLX:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_json_not_dict(self) -> None:
        output = (
            '<<<RLX:QUESTION>>>\n'
            '["a", "b"]\n'
            '<<<RLX:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_empty_body(self) -> None:
        output = (
            '<<<RLX:QUESTION>>>\n'
            '   \n'
            '<<<RLX:END>>>'
        )
        assert parse_question_payload(output) is None

    def test_multiple_options(self) -> None:
        output = (
            '<<<RLX:QUESTION>>>\n'
            '{"question": "Pick", "options": ["A", "B", "C", "D"]}\n'
            '<<<RLX:END>>>'
        )
        result = parse_question_payload(output)
        assert result is not None
        assert len(result.options) == 4


class TestParsePlanDraftPayload:
    def test_valid_draft(self) -> None:
        output = (
            'Preamble\n'
            '<<<RLX:PLAN_DRAFT>>>\n'
            '# My Plan\n'
            '## Overview\n'
            'Do stuff\n'
            '<<<RLX:END>>>\n'
            'Epilogue'
        )
        result = parse_plan_draft_payload(output)
        assert result is not None
        assert "# My Plan" in result
        assert "Do stuff" in result

    def test_no_draft_signal(self) -> None:
        assert parse_plan_draft_payload("no draft here") is None

    def test_draft_signal_but_no_end(self) -> None:
        output = '<<<RLX:PLAN_DRAFT>>>\n# Plan\nContent'
        assert parse_plan_draft_payload(output) is None

    def test_empty_draft_content(self) -> None:
        output = (
            '<<<RLX:PLAN_DRAFT>>>\n'
            '   \n'
            '<<<RLX:END>>>'
        )
        assert parse_plan_draft_payload(output) is None

    def test_multiline_content(self) -> None:
        content = "# Title\n\n## Section\n- item 1\n- item 2"
        output = (
            f"<<<RLX:PLAN_DRAFT>>>\n"
            f"{content}\n"
            f"<<<RLX:END>>>"
        )
        result = parse_plan_draft_payload(output)
        assert result is not None
        assert "# Title" in result
        assert "- item 1" in result


class TestIsPlanReady:
    def test_plan_ready(self) -> None:
        assert is_plan_ready(SignalPlanReady) is True

    def test_not_plan_ready(self) -> None:
        assert is_plan_ready("<<<RLX:TASK_FAILED>>>") is False

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


