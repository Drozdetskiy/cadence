from __future__ import annotations

from cadence.processor.signals import (
    is_all_tasks_done,
    is_plan_ready,
    is_report_done,
    is_report_failed,
    is_review_done,
    is_task_failed,
    parse_plan_draft_payload,
    parse_question_payload,
    parse_report_body,
    parse_squash_commit_message,
)
from cadence.status import (
    SignalCompleted,
    SignalFailed,
    SignalPlanReady,
    SignalReportDone,
    SignalReportFailed,
    SignalReviewDone,
)


class TestParseQuestionPayload:
    def test_valid_question(self) -> None:
        output = (
            "Some text\n"
            "<<<CADENCE:QUESTION>>>\n"
            '{"question": "Which DB?", "options": ["Postgres", "SQLite"]}\n'
            "<<<CADENCE:END>>>\n"
            "More text"
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
        output = "<<<CADENCE:QUESTION>>>\nnot json\n<<<CADENCE:END>>>"
        assert parse_question_payload(output) is None

    def test_missing_question_field(self) -> None:
        output = '<<<CADENCE:QUESTION>>>\n{"options": ["a", "b"]}\n<<<CADENCE:END>>>'
        assert parse_question_payload(output) is None

    def test_missing_options_field(self) -> None:
        output = '<<<CADENCE:QUESTION>>>\n{"question": "Which?"}\n<<<CADENCE:END>>>'
        assert parse_question_payload(output) is None

    def test_empty_question(self) -> None:
        output = '<<<CADENCE:QUESTION>>>\n{"question": "", "options": ["a"]}\n<<<CADENCE:END>>>'
        assert parse_question_payload(output) is None

    def test_empty_options(self) -> None:
        output = '<<<CADENCE:QUESTION>>>\n{"question": "Which?", "options": []}\n<<<CADENCE:END>>>'
        assert parse_question_payload(output) is None

    def test_json_not_dict(self) -> None:
        output = '<<<CADENCE:QUESTION>>>\n["a", "b"]\n<<<CADENCE:END>>>'
        assert parse_question_payload(output) is None

    def test_empty_body(self) -> None:
        output = "<<<CADENCE:QUESTION>>>\n   \n<<<CADENCE:END>>>"
        assert parse_question_payload(output) is None

    def test_multiple_options(self) -> None:
        output = (
            "<<<CADENCE:QUESTION>>>\n"
            '{"question": "Pick", "options": ["A", "B", "C", "D"]}\n'
            "<<<CADENCE:END>>>"
        )
        result = parse_question_payload(output)
        assert result is not None
        assert len(result.options) == 4


class TestParsePlanDraftPayload:
    def test_valid_draft(self) -> None:
        output = (
            "Preamble\n"
            "<<<CADENCE:PLAN_DRAFT>>>\n"
            "# My Plan\n"
            "## Overview\n"
            "Do stuff\n"
            "<<<CADENCE:END>>>\n"
            "Epilogue"
        )
        result = parse_plan_draft_payload(output)
        assert result is not None
        assert "# My Plan" in result
        assert "Do stuff" in result

    def test_no_draft_signal(self) -> None:
        assert parse_plan_draft_payload("no draft here") is None

    def test_draft_signal_but_no_end(self) -> None:
        output = "<<<CADENCE:PLAN_DRAFT>>>\n# Plan\nContent"
        assert parse_plan_draft_payload(output) is None

    def test_empty_draft_content(self) -> None:
        output = "<<<CADENCE:PLAN_DRAFT>>>\n   \n<<<CADENCE:END>>>"
        assert parse_plan_draft_payload(output) is None

    def test_multiline_content(self) -> None:
        content = "# Title\n\n## Section\n- item 1\n- item 2"
        output = f"<<<CADENCE:PLAN_DRAFT>>>\n{content}\n<<<CADENCE:END>>>"
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


class TestParseSquashCommitMessage:
    def test_valid_message(self) -> None:
        output = (
            "<<<CADENCE:COMMIT_MSG_BEGIN>>>\n"
            "0029-foo.\n"
            "\n"
            "Added: thing.\n"
            "<<<CADENCE:COMMIT_MSG_END>>>"
        )
        result = parse_squash_commit_message(output)
        assert result is not None
        assert result.startswith("0029-foo.")
        assert "Added: thing." in result

    def test_message_surrounded_by_prose(self) -> None:
        output = (
            "Sure, here is the message:\n"
            "<<<CADENCE:COMMIT_MSG_BEGIN>>>\n"
            "branch-x.\n\nAdded: y.\n"
            "<<<CADENCE:COMMIT_MSG_END>>>\n"
            "Hope that helps."
        )
        result = parse_squash_commit_message(output)
        assert result is not None
        assert result == "branch-x.\n\nAdded: y."

    def test_missing_begin_marker(self) -> None:
        output = "branch.\n\nAdded: y.\n<<<CADENCE:COMMIT_MSG_END>>>"
        assert parse_squash_commit_message(output) is None

    def test_missing_end_marker(self) -> None:
        output = "<<<CADENCE:COMMIT_MSG_BEGIN>>>\nbranch.\n\nAdded: y."
        assert parse_squash_commit_message(output) is None

    def test_no_markers(self) -> None:
        assert parse_squash_commit_message("just some text") is None

    def test_empty_body(self) -> None:
        output = "<<<CADENCE:COMMIT_MSG_BEGIN>>>\n   \n<<<CADENCE:COMMIT_MSG_END>>>"
        assert parse_squash_commit_message(output) is None

    def test_multiline_body_preserved(self) -> None:
        body = "subj.\n\nAdded: a.\nChanged: b.\nDeleted: c."
        output = f"<<<CADENCE:COMMIT_MSG_BEGIN>>>\n{body}\n<<<CADENCE:COMMIT_MSG_END>>>"
        result = parse_squash_commit_message(output)
        assert result == body

    def test_takes_last_marker_pair_when_prompt_echoed(self) -> None:
        output = (
            "Output ONLY between markers:\n"
            "<<<CADENCE:COMMIT_MSG_BEGIN>>>\n"
            "<your commit message here>\n"
            "<<<CADENCE:COMMIT_MSG_END>>>\n"
            "Real message:\n"
            "<<<CADENCE:COMMIT_MSG_BEGIN>>>\n"
            "branch.\n\nAdded: a.\n"
            "<<<CADENCE:COMMIT_MSG_END>>>"
        )
        result = parse_squash_commit_message(output)
        assert result == "branch.\n\nAdded: a."


class TestParseReportBody:
    def test_valid_body(self) -> None:
        body = "# API changes: feat vs main\n\n## Added\n- foo - new endpoint - abc123"
        output = f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>"
        result = parse_report_body(output)
        assert result == body

    def test_body_surrounded_by_prose(self) -> None:
        output = (
            "Here is the report:\n"
            "<<<CADENCE:REPORT_BEGIN>>>\n"
            "# API changes: x vs main\n"
            "<<<CADENCE:REPORT_END>>>\n"
            "Done."
        )
        result = parse_report_body(output)
        assert result == "# API changes: x vs main"

    def test_missing_begin_marker(self) -> None:
        output = "# API changes\n<<<CADENCE:REPORT_END>>>"
        assert parse_report_body(output) is None

    def test_missing_end_marker(self) -> None:
        output = "<<<CADENCE:REPORT_BEGIN>>>\n# API changes"
        assert parse_report_body(output) is None

    def test_no_markers(self) -> None:
        assert parse_report_body("just some text") is None

    def test_empty_body(self) -> None:
        output = "<<<CADENCE:REPORT_BEGIN>>>\n   \n<<<CADENCE:REPORT_END>>>"
        assert parse_report_body(output) is None

    def test_takes_last_non_empty_when_multiple_pairs(self) -> None:
        output = (
            "<<<CADENCE:REPORT_BEGIN>>>\n"
            "<placeholder>\n"
            "<<<CADENCE:REPORT_END>>>\n"
            "Real:\n"
            "<<<CADENCE:REPORT_BEGIN>>>\n"
            "# real report\n"
            "<<<CADENCE:REPORT_END>>>"
        )
        result = parse_report_body(output)
        assert result == "# real report"

    def test_multiline_body_preserved(self) -> None:
        body = "# API changes: x vs main\n\n## Added\n- a\n\n## Removed\n- b"
        output = f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>"
        assert parse_report_body(output) == body


class TestIsReportDone:
    def test_report_done(self) -> None:
        assert is_report_done(SignalReportDone) is True

    def test_not_report_done(self) -> None:
        assert is_report_done(SignalReportFailed) is False
        assert is_report_done(SignalCompleted) is False

    def test_empty(self) -> None:
        assert is_report_done("") is False


class TestIsReportFailed:
    def test_report_failed(self) -> None:
        assert is_report_failed(SignalReportFailed) is True

    def test_not_report_failed(self) -> None:
        assert is_report_failed(SignalReportDone) is False
        assert is_report_failed(SignalFailed) is False

    def test_empty(self) -> None:
        assert is_report_failed("") is False
