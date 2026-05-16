from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Protocol

from cadence.executor.events import (
    AssistantEvent,
    ClaudeEvent,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ResultEvent,
    TextContent,
    TextDelta,
    ToolUseBlock,
    Usage,
    parse_event,
)
from cadence.executor.process_group import ProcessGroupCleanup
from cadence.status import (
    SignalCompleted,
    SignalFailed,
    SignalPlanDraft,
    SignalPlanReady,
    SignalQuestion,
    SignalReportDone,
    SignalReportFailed,
    SignalReviewDone,
)

RECENT_BLOCK_COUNT = 10


@dataclass
class Result:
    output: str = ""
    recent_text: str = ""
    signal: str = ""
    error: Exception | None = None
    idle_timed_out: bool = False
    usage: Usage | None = None
    session_id: str = ""
    model: str = ""


class PatternMatchError(Exception):
    def __init__(self, pattern: str, help_cmd: str) -> None:
        super().__init__(f"pattern matched: {pattern}")
        self.pattern = pattern
        self.help_cmd = help_cmd


class LimitPatternError(Exception):
    def __init__(self, pattern: str, help_cmd: str) -> None:
        super().__init__(f"limit pattern matched: {pattern}")
        self.pattern = pattern
        self.help_cmd = help_cmd


class CommandRunner(Protocol):
    def run(
        self, name: str, *args: str, cwd: str | None = None
    ) -> tuple[IO[str], Callable[[], int]]: ...


def detect_signal(text: str) -> str:
    for sig in (
        SignalCompleted,
        SignalFailed,
        SignalReviewDone,
        SignalPlanReady,
        SignalQuestion,
        SignalPlanDraft,
        SignalReportFailed,
        SignalReportDone,
    ):
        if sig in text:
            return sig
    return ""


def match_pattern(output: str, patterns: list[str]) -> str:
    """Case-sensitive substring; mirror the exact case Claude emits.

    See detect_signal for matching semantics.
    """
    for pat in patterns:
        if not pat or not pat.strip():
            continue
        if pat in output:
            return pat
    return ""


def _extract_text_from_event(event: ClaudeEvent) -> str:
    match event:
        case AssistantEvent(message=msg) if msg is not None:
            return "".join(c.text for c in msg.content if isinstance(c, TextContent))
        case ContentBlockDeltaEvent(delta=TextDelta(text=t)):
            return t
        case ResultEvent(result=result) if result is not None and not isinstance(result, str):
            return result.output or ""
        case _:
            return ""


def filter_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDECODE", None)
    return env


class _IdleWatchdog:
    def __init__(self, timeout: float, on_idle: Callable[[], None]) -> None:
        self._timeout = timeout
        self._on_idle = on_idle
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self.triggered = threading.Event()

    def active(self) -> bool:
        return self._timeout > 0

    def reset(self) -> None:
        if not self.active():
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._timeout, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _fire(self) -> None:
        self.triggered.set()
        self._on_idle()


@dataclass
class _ProcessHandle:
    stdout: IO[str]
    wait: Callable[[], int]
    cleanup: ProcessGroupCleanup | None = None


def _launch_process(
    cmd: list[str],
    prompt: str,
    cmd_runner: CommandRunner | None,
    cwd: str | None = None,
) -> tuple[_ProcessHandle | None, Exception | None]:
    if cmd_runner is not None:
        if cwd is not None:
            stdout, wait_fn = cmd_runner.run(cmd[0], *cmd[1:], cwd=cwd)
        else:
            stdout, wait_fn = cmd_runner.run(cmd[0], *cmd[1:])
        return _ProcessHandle(stdout=stdout, wait=wait_fn), None

    env = filter_env()
    if cwd is not None:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env=env,
            cwd=cwd,
        )
    else:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env=env,
        )
    cleanup = ProcessGroupCleanup(process)

    try:
        if process.stdin is not None:
            process.stdin.write(prompt)
            process.stdin.close()
    except BrokenPipeError:
        cleanup.kill_process_group()
        cleanup.wait()
        return None, Exception("failed to send prompt: broken pipe")

    assert process.stdout is not None
    return (
        _ProcessHandle(stdout=process.stdout, wait=cleanup.wait, cleanup=cleanup),
        None,
    )


class ClaudeExecutor:
    def __init__(
        self,
        *,
        command: str = "",
        args: str = "",
        model: str = "",
        output_handler: Callable[[str], None] | None = None,
        activity_handler: Callable[[str], None] | None = None,
        debug: bool = False,
        error_patterns: list[str] | None = None,
        limit_patterns: list[str] | None = None,
        idle_timeout: float = 0,
        cmd_runner: CommandRunner | None = None,
        cwd: str | None = None,
    ) -> None:
        self._command = command or "claude"
        self._args = args
        self._model = model
        self._output_handler = output_handler
        self._activity_handler = activity_handler
        self._debug = debug
        self._error_patterns = error_patterns or []
        self._limit_patterns = limit_patterns or []
        self._idle_timeout = idle_timeout
        self._cmd_runner = cmd_runner
        self._cwd = cwd
        self._active_cleanup: ProcessGroupCleanup | None = None

    def run(self, prompt: str) -> Result:
        cmd = self._build_command()
        handle, launch_err = _launch_process(cmd, prompt, self._cmd_runner, self._cwd)
        if handle is None:
            return Result(error=launch_err)

        self._active_cleanup = handle.cleanup

        def on_idle() -> None:
            if handle.cleanup is not None:
                handle.cleanup.kill_process_group()

        watchdog = _IdleWatchdog(self._idle_timeout, on_idle)
        try:
            result = self._parse_stream(handle, watchdog)
            exit_code = handle.wait()
        finally:
            self._active_cleanup = None

        if watchdog.triggered.is_set():
            return self._finalize_idle(result)
        return self._finalize_exit(result, exit_code)

    def cancel(self) -> None:
        cleanup = self._active_cleanup
        if cleanup is not None:
            cleanup.kill_process_group()

    def _parse_stream(
        self,
        handle: _ProcessHandle,
        watchdog: _IdleWatchdog,
    ) -> Result:
        result = Result()
        recent: deque[str] = deque(maxlen=RECENT_BLOCK_COUNT)
        output_parts: list[str] = []
        last_output_text = ""

        watchdog.reset()

        try:
            for raw_line in handle.stdout:
                line = raw_line.rstrip("\n").rstrip("\r")
                watchdog.reset()

                try:
                    raw = json.loads(line)
                except ValueError:
                    output_parts.append(line + "\n")
                    recent.append(line)
                    if self._output_handler:
                        self._output_handler(line + "\n")
                    last_output_text = line + "\n"
                    continue

                event = parse_event(raw)
                if event is None:
                    continue

                last_output_text = self._handle_event(event, output_parts, result, last_output_text)

        except BaseException:
            if handle.cleanup is not None:
                handle.cleanup.kill_process_group()
                with contextlib.suppress(Exception):
                    handle.cleanup.wait()
            raise
        finally:
            watchdog.cancel()

        result.output = "".join(output_parts)
        result.recent_text = "".join(recent)
        return result

    def _handle_event(
        self,
        event: ClaudeEvent,
        output_parts: list[str],
        result: Result,
        last_output_text: str,
    ) -> str:
        if isinstance(event, AssistantEvent) and self._output_handler:
            if last_output_text and not last_output_text.endswith("\n"):
                self._output_handler("\n")
            last_output_text = ""

        if (
            self._activity_handler
            and isinstance(event, ContentBlockStartEvent)
            and isinstance(event.content_block, ToolUseBlock)
        ):
            self._activity_handler(event.content_block.name)

        if isinstance(event, ResultEvent):
            result.usage = event.usage
            result.session_id = event.session_id
            result.model = event.model

        text = _extract_text_from_event(event)
        if text:
            output_parts.append(text)
            last_output_text = text
            sig = detect_signal(text)
            if sig:
                result.signal = sig
            if self._output_handler:
                self._output_handler(text)

        return last_output_text

    def _finalize_idle(self, result: Result) -> Result:
        limit_pat = match_pattern(result.recent_text, self._limit_patterns)
        if limit_pat:
            result.error = LimitPatternError(limit_pat, "claude /usage")
            return result
        err_pat = match_pattern(result.recent_text, self._error_patterns)
        if err_pat:
            result.error = PatternMatchError(err_pat, "claude /usage")
            return result
        result.idle_timed_out = True
        result.error = None
        return result

    def _finalize_exit(self, result: Result, exit_code: int) -> Result:
        if exit_code != 0:
            if not result.output:
                result.error = Exception(f"claude exited with code {exit_code}")
                return result
            if not result.signal:
                result.error = Exception(f"claude exited with code {exit_code} without completing")
                return result

        if exit_code == 0 and result.signal:
            return result

        limit_pat = match_pattern(result.recent_text, self._limit_patterns)
        if limit_pat:
            result.error = LimitPatternError(limit_pat, "claude /usage")
            return result

        err_pat = match_pattern(result.recent_text, self._error_patterns)
        if err_pat:
            result.error = PatternMatchError(err_pat, "claude /usage")
            return result

        return result

    def _build_command(self) -> list[str]:
        cmd = shlex.split(self._command)
        if self._args:
            cmd.extend(shlex.split(self._args))
        else:
            cmd.extend(
                [
                    "--dangerously-skip-permissions",
                    "--verbose",
                ]
            )
        if self._model:
            cmd.extend(["--model", self._model])
        cmd.extend(["--output-format", "stream-json", "--print"])
        return cmd
