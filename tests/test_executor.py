from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from typing import IO
from unittest.mock import patch

import pytest

from cadence.executor.claude_executor import (
    ClaudeExecutor,
    LimitPatternError,
    PatternMatchError,
    Result,
    _extract_text,
    detect_signal,
    filter_env,
    match_pattern,
)
from cadence.executor.process_group import ProcessGroupCleanup
from cadence.status import (
    SignalCompleted,
    SignalFailed,
    SignalPlanReady,
    SignalReviewDone,
)


class TestDetectSignal:
    def test_completed(self) -> None:
        assert detect_signal("some text <<<CADENCE:ALL_TASKS_DONE>>> more") == SignalCompleted

    def test_failed(self) -> None:
        assert detect_signal("<<<CADENCE:TASK_FAILED>>>") == SignalFailed

    def test_review_done(self) -> None:
        assert detect_signal("output <<<CADENCE:REVIEW_DONE>>>") == SignalReviewDone

    def test_plan_ready(self) -> None:
        assert detect_signal("<<<CADENCE:PLAN_READY>>>") == SignalPlanReady

    def test_no_signal(self) -> None:
        assert detect_signal("just some text without any signals") == ""

    def test_empty(self) -> None:
        assert detect_signal("") == ""

    def test_multiple_signals_returns_first(self) -> None:
        text = "<<<CADENCE:ALL_TASKS_DONE>>> <<<CADENCE:TASK_FAILED>>>"
        assert detect_signal(text) == SignalCompleted

    def test_partial_signal_not_matched(self) -> None:
        assert detect_signal("<<<CADENCE:ALL_TASKS") == ""


class TestMatchPattern:
    def test_case_insensitive(self) -> None:
        result = match_pattern("You've Hit Your Limit", ["you've hit your limit"])
        assert result == "you've hit your limit"

    def test_substring_match(self) -> None:
        assert match_pattern("error: API Error: something", ["API Error:"]) == "API Error:"

    def test_no_match(self) -> None:
        assert match_pattern("everything is fine", ["API Error:", "Not logged in"]) == ""

    def test_empty_pattern_skipped(self) -> None:
        assert match_pattern("some text", ["", "  ", "match"]) == ""

    def test_whitespace_only_skipped(self) -> None:
        assert match_pattern("test", ["   "]) == ""

    def test_empty_output(self) -> None:
        assert match_pattern("", ["pattern"]) == ""

    def test_empty_patterns_list(self) -> None:
        assert match_pattern("text", []) == ""


class TestExtractText:
    def test_assistant_event(self) -> None:
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "hello "},
                    {"type": "text", "text": "world"},
                ]
            },
        }
        assert _extract_text(event) == "hello world"

    def test_content_block_delta(self) -> None:
        event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "chunk"},
        }
        assert _extract_text(event) == "chunk"

    def test_content_block_delta_non_text(self) -> None:
        event = {
            "type": "content_block_delta",
            "delta": {"type": "tool_use_delta", "text": "chunk"},
        }
        assert _extract_text(event) == ""

    def test_message_stop(self) -> None:
        event = {
            "type": "message_stop",
            "message": {
                "content": [{"type": "text", "text": "final"}]
            },
        }
        assert _extract_text(event) == "final"

    def test_result_dict_output(self) -> None:
        event = {
            "type": "result",
            "result": {"output": "session summary"},
        }
        assert _extract_text(event) == "session summary"

    def test_result_string(self) -> None:
        event = {"type": "result", "result": "some string"}
        assert _extract_text(event) == ""

    def test_unknown_type(self) -> None:
        event = {"type": "unknown", "data": "stuff"}
        assert _extract_text(event) == ""


class TestFilterEnv:
    def test_removes_anthropic_key(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret", "HOME": "/home"}):
            env = filter_env()
            assert "ANTHROPIC_API_KEY" not in env
            assert env["HOME"] == "/home"

    def test_removes_claudecode(self) -> None:
        with patch.dict(os.environ, {"CLAUDECODE": "1"}):
            env = filter_env()
            assert "CLAUDECODE" not in env

    def test_missing_keys_no_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            env = filter_env()
            assert "ANTHROPIC_API_KEY" not in env


class MockCommandRunner:
    def __init__(self, lines: list[str], exit_code: int = 0) -> None:
        self._lines = lines
        self._exit_code = exit_code

    def run(self, name: str, *args: str) -> tuple[IO[str], Callable[[], int]]:
        content = "\n".join(self._lines) + "\n" if self._lines else ""
        stream = io.StringIO(content)
        return stream, lambda: self._exit_code


class TestClaudeExecutorWithMockRunner:
    def test_basic_text_output(self) -> None:
        delta = {"type": "text_delta", "text": "hello"}
        delta2 = {"type": "text_delta", "text": " world"}
        lines = [
            json.dumps({"type": "content_block_delta", "delta": delta}),
            json.dumps({"type": "content_block_delta", "delta": delta2}),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("test prompt")
        assert result.output == "hello world"
        assert result.error is None
        assert result.signal == ""

    def test_signal_detection(self) -> None:
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "done <<<CADENCE:ALL_TASKS_DONE>>>"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert result.signal == SignalCompleted

    def test_failed_signal(self) -> None:
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "<<<CADENCE:TASK_FAILED>>>"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert result.signal == SignalFailed

    def test_error_pattern_detection(self) -> None:
        lines = ["API Error: something bad"]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(
            cmd_runner=runner,
            error_patterns=["API Error:"],
        )
        result = executor.run("prompt")
        assert isinstance(result.error, PatternMatchError)
        assert result.error.pattern == "API Error:"

    def test_limit_pattern_takes_priority(self) -> None:
        lines = ["You've hit your limit"]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(
            cmd_runner=runner,
            error_patterns=["You've hit your limit"],
            limit_patterns=["You've hit your limit"],
        )
        result = executor.run("prompt")
        assert isinstance(result.error, LimitPatternError)

    def test_pattern_in_assistant_content_not_matched(self) -> None:
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {
                    "type": "text_delta",
                    "text": "discussing You've hit your limit pattern in code",
                },
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(
            cmd_runner=runner,
            error_patterns=["You've hit your limit"],
            limit_patterns=["You've hit your limit"],
        )
        result = executor.run("prompt")
        assert result.error is None
        assert "You've hit your limit" in result.output

    def test_pattern_in_result_event_not_matched(self) -> None:
        lines = [
            json.dumps({
                "type": "result",
                "result": "summary echoing You've hit your limit literal",
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(
            cmd_runner=runner,
            error_patterns=["You've hit your limit"],
            limit_patterns=["You've hit your limit"],
        )
        result = executor.run("prompt")
        assert result.error is None

    def test_pattern_in_unrecognized_json_event_not_matched(self) -> None:
        lines = [
            json.dumps({
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "content": "matched: You've hit your limit",
                        }
                    ]
                },
            }),
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "done <<<CADENCE:ALL_TASKS_DONE>>>"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(
            cmd_runner=runner,
            error_patterns=["You've hit your limit"],
            limit_patterns=["You've hit your limit"],
        )
        result = executor.run("prompt")
        assert result.error is None
        assert result.signal == SignalCompleted
        assert "You've hit your limit" in result.output

    def test_pattern_in_unrecognized_json_event_no_signal_not_matched(self) -> None:
        lines = [
            json.dumps({
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "content": "matched: You've hit your limit",
                        }
                    ]
                },
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(
            cmd_runner=runner,
            error_patterns=["You've hit your limit"],
            limit_patterns=["You've hit your limit"],
        )
        result = executor.run("prompt")
        assert result.error is None
        assert result.signal == ""
        assert "You've hit your limit" in result.output

    def test_pattern_in_non_json_line_still_matches(self) -> None:
        lines = ["You've hit your limit"]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(
            cmd_runner=runner,
            error_patterns=["You've hit your limit"],
            limit_patterns=["You've hit your limit"],
        )
        result = executor.run("prompt")
        assert isinstance(result.error, LimitPatternError)

    def test_limit_pattern_skipped_on_clean_signal_exit(self) -> None:
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "plan body <<<CADENCE:PLAN_READY>>>"},
            }),
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "quoting You've hit your limit pattern"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(
            cmd_runner=runner,
            error_patterns=["You've hit your limit"],
            limit_patterns=["You've hit your limit"],
        )
        result = executor.run("prompt")
        assert result.error is None
        assert result.signal == SignalPlanReady

    def test_limit_pattern_skipped_on_question_turn(self) -> None:
        from cadence.status import SignalQuestion

        question_text = (
            '<<<CADENCE:QUESTION>>>\n'
            '{"question": "How does YAML override TOML?", '
            '"options": ["a quoting You\'ve hit your limit", "b"]}\n'
            '<<<CADENCE:END>>>'
        )
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": question_text},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(
            cmd_runner=runner,
            error_patterns=["You've hit your limit"],
            limit_patterns=["You've hit your limit"],
        )
        result = executor.run("prompt")
        assert result.error is None
        assert result.signal == SignalQuestion

    def test_nonzero_exit_no_output(self) -> None:
        runner = MockCommandRunner([], exit_code=1)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert result.error is not None
        assert "exited with code 1" in str(result.error)

    def test_nonzero_exit_with_signal_ignored(self) -> None:
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "work done <<<CADENCE:ALL_TASKS_DONE>>>"},
            }),
        ]
        runner = MockCommandRunner(lines, exit_code=1)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert result.error is None
        assert result.signal == SignalCompleted

    def test_nonzero_exit_with_output_no_signal(self) -> None:
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "partial work"},
            }),
        ]
        runner = MockCommandRunner(lines, exit_code=1)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert result.error is not None
        assert "without completing" in str(result.error)

    def test_non_json_lines_added_to_output(self) -> None:
        lines = ["not json at all", "also not json"]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert "not json at all" in result.output
        assert "also not json" in result.output

    def test_output_handler_called(self) -> None:
        captured: list[str] = []
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner, output_handler=captured.append)
        executor.run("prompt")
        assert captured == ["hello"]

    def test_output_handler_newline_at_message_stop(self) -> None:
        captured: list[str] = []
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "first message."},
            }),
            json.dumps({"type": "message_stop"}),
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "second message."},
            }),
            json.dumps({"type": "message_stop"}),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner, output_handler=captured.append)
        executor.run("prompt")
        assert captured == ["first message.", "\n", "second message.", "\n"]

    def test_output_handler_newline_at_assistant_boundary(self) -> None:
        captured: list[str] = []
        lines = [
            json.dumps({
                "type": "assistant",
                "message": {"content": []},
            }),
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "first message."},
            }),
            json.dumps({
                "type": "assistant",
                "message": {"content": []},
            }),
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "second message."},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner, output_handler=captured.append)
        executor.run("prompt")
        assert captured == ["first message.", "\n", "second message."]

    def test_output_handler_no_double_newline_with_both_boundaries(self) -> None:
        captured: list[str] = []
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "first."},
            }),
            json.dumps({"type": "message_stop"}),
            json.dumps({
                "type": "assistant",
                "message": {"content": []},
            }),
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "second."},
            }),
            json.dumps({"type": "message_stop"}),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner, output_handler=captured.append)
        executor.run("prompt")
        assert captured == ["first.", "\n", "second.", "\n"]

    def test_output_handler_no_extra_newline_when_text_ends_with_newline(self) -> None:
        captured: list[str] = []
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "has newline\n"},
            }),
            json.dumps({"type": "message_stop"}),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner, output_handler=captured.append)
        executor.run("prompt")
        assert captured == ["has newline\n"]

    def test_output_handler_no_newline_for_tool_only_message(self) -> None:
        captured: list[str] = []
        lines = [
            json.dumps({
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read"},
            }),
            json.dumps({"type": "message_stop"}),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner, output_handler=captured.append)
        executor.run("prompt")
        assert captured == []

    def test_recent_text_deque(self) -> None:
        lines = [f"envelope-block{i}" for i in range(15)]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert "envelope-block5" in result.recent_text
        assert "envelope-block14" in result.recent_text
        assert "envelope-block0" not in result.recent_text

    def test_assistant_event_parsing(self) -> None:
        lines = [
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "first part "},
                        {"type": "text", "text": "second part"},
                    ]
                },
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert result.output == "first part second part"

    def test_result_event_with_output(self) -> None:
        lines = [
            json.dumps({
                "type": "result",
                "result": {"output": "summary text"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert "summary text" in result.output


class TestClaudeExecutorCommandBuilding:
    def test_default_command(self) -> None:
        executor = ClaudeExecutor(cmd_runner=MockCommandRunner([]))
        cmd = executor._build_command()
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd
        assert "--verbose" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"
        assert cmd[-1] == "--print"

    def test_custom_command(self) -> None:
        executor = ClaudeExecutor(command="my-claude", cmd_runner=MockCommandRunner([]))
        cmd = executor._build_command()
        assert cmd[0] == "my-claude"

    def test_custom_args_override_defaults(self) -> None:
        executor = ClaudeExecutor(args="--custom-flag val", cmd_runner=MockCommandRunner([]))
        cmd = executor._build_command()
        assert "--custom-flag" in cmd
        assert "val" in cmd
        assert "--dangerously-skip-permissions" not in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"

    def test_model_flag(self) -> None:
        executor = ClaudeExecutor(model="opus", cmd_runner=MockCommandRunner([]))
        cmd = executor._build_command()
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "opus"

    def test_print_always_last(self) -> None:
        executor = ClaudeExecutor(model="sonnet", args="--flag x", cmd_runner=MockCommandRunner([]))
        cmd = executor._build_command()
        assert cmd[-1] == "--print"


class TestClaudeExecutorIdleTimeout:
    def test_idle_timeout_sets_flag(self) -> None:
        import threading as _threading

        class SlowRunner:
            def run(self, name: str, *args: str) -> tuple[IO[str], Callable[[], int]]:
                r, w = os.pipe()
                rf = os.fdopen(r, "r")
                wf = os.fdopen(w, "w")
                line = json.dumps({
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "hi"},
                })
                wf.write(line + "\n")
                wf.flush()

                def close_later() -> None:
                    time.sleep(0.4)
                    wf.close()

                t = _threading.Thread(target=close_later, daemon=True)
                t.start()
                return rf, lambda: 0

        executor = ClaudeExecutor(cmd_runner=SlowRunner(), idle_timeout=0.1)
        result = executor.run("prompt")
        assert result.idle_timed_out is True
        assert result.error is None

    def test_idle_timeout_with_limit_pattern(self) -> None:
        import threading as _threading

        class LimitRunner:
            def run(self, name: str, *args: str) -> tuple[IO[str], Callable[[], int]]:
                r, w = os.pipe()
                rf = os.fdopen(r, "r")
                wf = os.fdopen(w, "w")
                wf.write("You've hit your limit\n")
                wf.flush()

                def close_later() -> None:
                    time.sleep(0.4)
                    wf.close()

                t = _threading.Thread(target=close_later, daemon=True)
                t.start()
                return rf, lambda: 0

        executor = ClaudeExecutor(
            cmd_runner=LimitRunner(),
            idle_timeout=0.1,
            limit_patterns=["You've hit your limit"],
        )
        result = executor.run("prompt")
        assert isinstance(result.error, LimitPatternError)


class TestProcessGroupCleanup:
    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_wait_returns_returncode(self) -> None:
        process = subprocess.Popen(
            ["true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        cleanup = ProcessGroupCleanup(process)
        code = cleanup.wait()
        assert code == 0

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_kill_already_exited(self) -> None:
        process = subprocess.Popen(
            ["true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        process.wait()
        cleanup = ProcessGroupCleanup(process)
        cleanup.kill_process_group()

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_kill_idempotent(self) -> None:
        process = subprocess.Popen(
            ["sleep", "10"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        cleanup = ProcessGroupCleanup(process)
        cleanup.kill_process_group()
        cleanup.kill_process_group()
        process.wait()


class TestActivityHandler:
    def test_called_with_tool_name(self) -> None:
        captured: list[str] = []
        lines = [
            json.dumps({
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner, activity_handler=captured.append)
        executor.run("prompt")
        assert captured == ["Read"]

    def test_not_called_for_text_events(self) -> None:
        captured: list[str] = []
        lines = [
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner, activity_handler=captured.append)
        executor.run("prompt")
        assert captured == []

    def test_none_handler_no_crash(self) -> None:
        lines = [
            json.dumps({
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Grep"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner)
        result = executor.run("prompt")
        assert result.error is None

    def test_multiple_tools(self) -> None:
        captured: list[str] = []
        lines = [
            json.dumps({
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read"},
            }),
            json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "some text"},
            }),
            json.dumps({
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Grep"},
            }),
            json.dumps({
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Edit"},
            }),
        ]
        runner = MockCommandRunner(lines)
        executor = ClaudeExecutor(cmd_runner=runner, activity_handler=captured.append)
        executor.run("prompt")
        assert captured == ["Read", "Grep", "Edit"]


class TestResult:
    def test_defaults(self) -> None:
        r = Result()
        assert r.output == ""
        assert r.recent_text == ""
        assert r.signal == ""
        assert r.error is None
        assert r.idle_timed_out is False


class TestClaudeExecutorCancel:
    def test_cancel_without_active_run_is_noop(self) -> None:
        executor = ClaudeExecutor()
        executor.cancel()

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_cancel_kills_running_subprocess(self) -> None:
        import threading

        executor = ClaudeExecutor(
            command="sh",
            args="-c 'sleep 30'",
            idle_timeout=0,
        )

        result_holder: list[Result] = []

        def run_it() -> None:
            result_holder.append(executor.run(""))

        t = threading.Thread(target=run_it)
        t.start()

        for _ in range(100):
            if executor._active_cleanup is not None:
                break
            time.sleep(0.02)

        executor.cancel()
        t.join(timeout=5.0)
        assert not t.is_alive()
        assert executor._active_cleanup is None
