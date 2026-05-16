from __future__ import annotations

import json
import re
from dataclasses import dataclass

from cadence.status import (
    SignalCommitMsgBegin,
    SignalCommitMsgEnd,
    SignalCompleted,
    SignalEnd,
    SignalFailed,
    SignalPlanDraft,
    SignalPlanReady,
    SignalQuestion,
    SignalReportBegin,
    SignalReportDone,
    SignalReportEnd,
    SignalReportFailed,
    SignalReviewDone,
    SignalReviewSecondDone,
)


def _payload_regex(opener: str) -> re.Pattern[str]:
    return re.compile(
        rf"{re.escape(opener)}\s*(.*?)\s*{re.escape(SignalEnd)}",
        re.DOTALL,
    )


_QUESTION_RE = _payload_regex(SignalQuestion)
_PLAN_DRAFT_RE = _payload_regex(SignalPlanDraft)
_COMMIT_MSG_RE = re.compile(
    rf"{re.escape(SignalCommitMsgBegin)}\s*(.*?)\s*{re.escape(SignalCommitMsgEnd)}",
    re.DOTALL,
)
_REPORT_BODY_RE = re.compile(
    rf"{re.escape(SignalReportBegin)}\s*(.*?)\s*{re.escape(SignalReportEnd)}",
    re.DOTALL,
)


@dataclass
class QuestionPayload:
    question: str
    options: list[str]


def _extract_payload(output: str, opener: str, regex: re.Pattern[str]) -> str | None:
    if opener not in output:
        return None
    m = regex.search(output)
    if m is None:
        return None
    raw = m.group(1).strip()
    return raw or None


def parse_question_payload(output: str) -> QuestionPayload | None:
    raw = _extract_payload(output, SignalQuestion, _QUESTION_RE)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError, ValueError:
        return None
    if not isinstance(data, dict):
        return None
    question = data.get("question")
    options = data.get("options")
    if not isinstance(question, str) or not question:
        return None
    if not isinstance(options, list) or not options:
        return None
    if not all(isinstance(o, str) for o in options):
        return None
    return QuestionPayload(question=question, options=options)


def parse_plan_draft_payload(output: str) -> str | None:
    return _extract_payload(output, SignalPlanDraft, _PLAN_DRAFT_RE)


def parse_squash_commit_message(text: str) -> str | None:
    if SignalCommitMsgBegin not in text or SignalCommitMsgEnd not in text:
        return None
    matches = list(_COMMIT_MSG_RE.finditer(text))
    for m in reversed(matches):
        body = m.group(1).strip()
        if body:
            return body
    return None


def parse_report_body(text: str) -> str | None:
    if SignalReportBegin not in text or SignalReportEnd not in text:
        return None
    matches = list(_REPORT_BODY_RE.finditer(text))
    for m in reversed(matches):
        body = m.group(1).strip()
        if body:
            return body
    return None


def is_plan_ready(signal: str) -> bool:
    return signal == SignalPlanReady


def is_review_done(signal: str) -> bool:
    return signal == SignalReviewDone


def is_review_second_done(signal: str) -> bool:
    return signal == SignalReviewSecondDone


def is_task_failed(signal: str) -> bool:
    return signal == SignalFailed


def is_all_tasks_done(signal: str) -> bool:
    return signal == SignalCompleted


def is_report_done(signal: str) -> bool:
    return signal == SignalReportDone


def is_report_failed(signal: str) -> bool:
    return signal == SignalReportFailed
