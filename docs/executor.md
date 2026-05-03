# Executor Layer

Reference document for the executor layer.

## Overview

The executor module provides a single executor -- ClaudeExecutor -- for running the Claude CLI. The executor encapsulates process launching, streaming output parsing, signal detection, and error pattern matching. The executor knows nothing about orchestration -- it receives a prompt and returns a Result.

Key modules:
- `executor/claude_executor.py` -- CommandRunner protocol, ClaudeExecutor, JSON stream parsing, detect_signal(), match_pattern(), error types
- `executor/events.py` -- typed Claude stream event dataclasses (AssistantEvent, ContentBlockDeltaEvent, ResultEvent) and parse_event()
- `executor/process_group.py` -- process group management: start_new_session, SIGTERM/SIGKILL

## Result dataclass

A single result type:

```python
@dataclass
class Result:
    output: str = ""          # full accumulated text output
    recent_text: str = ""     # last 10 text blocks, used for pattern matching
    signal: str = ""          # detected signal (COMPLETED, FAILED, etc.) or empty string
    error: Exception | None = None  # execution error, if any
    idle_timed_out: bool = False    # True when idle timeout fired (process killed, but not by user request)
```

### The recent_text field

The last `RECENT_BLOCK_COUNT` (10) text blocks, joined in chronological order. Used for pattern matching instead of the full output -- this prevents false positives when Claude analyzes text containing phrases like "rate limit" at the start of a session.

Implementation: a ring buffer (collections.deque(maxlen=10)). When assembling recent_text, the elements are joined in chronological order.

## Error types

### PatternMatchError

Returned when a configured error pattern is detected in the output:

```python
class PatternMatchError(Exception):
    def __init__(self, pattern: str, help_cmd: str):
        self.pattern = pattern    # the pattern that matched
        self.help_cmd = help_cmd  # help command (e.g., "claude /usage")
```

### LimitPatternError

Returned when a rate limit pattern is detected. If `wait_on_limit` is configured, the calling code (processor) retries instead of exiting:

```python
class LimitPatternError(Exception):
    def __init__(self, pattern: str, help_cmd: str):
        self.pattern = pattern    # the pattern that matched
        self.help_cmd = help_cmd  # help command
```

### Pattern check priority

1. Limit patterns are checked first
2. If a limit pattern is found -- `LimitPatternError` is returned
3. Then error patterns are checked
4. If an error pattern is found -- `PatternMatchError` is returned

The function `match_pattern(output, patterns)` is a case-insensitive substring search. Empty patterns and whitespace-only strings are skipped.

## ClaudeExecutor

The single executor for all phases (task, review first, review second, plan creation).

### Class fields

```python
class ClaudeExecutor:
    command: str                        # command to run, defaults to "claude"
    args: str                           # additional arguments (space-separated string), defaults to standard
    model: str                          # model override ("opus", "sonnet", "haiku"); empty = CLI default
    output_handler: Callable[[str], None] | None  # callback for each text chunk
    debug: bool                         # enable debug output
    error_patterns: list[str]           # error patterns
    limit_patterns: list[str]           # rate limit patterns (checked before error patterns)
    idle_timeout: float                 # kill session after silence (seconds), 0 = disabled
    cmd_runner: CommandRunner | None    # for testing, None = real runner
```

### Command construction

1. If `command` is empty, `"claude"` is used
2. If `args` is non-empty, it is parsed via `split_args()` (with quote and escape support) or `shlex.split()`
3. If `args` is empty, the default flags are used:
   - `--dangerously-skip-permissions`
   - `--verbose`
4. If `model` is non-empty, `--model <value>` is appended
5. `--output-format stream-json --print` is always appended at the end (stream-json format and non-interactive mode)
6. The prompt is passed via stdin (not via the `-p` argument) -- this works around the Windows 8191-character limit

### split_args()

Parses an argument string into a list. Supports:
- Single and double quotes (not included in the result)
- Backslash escapes
- Spaces inside quotes are preserved

Alternative: `shlex.split()` from the Python standard library.

### Environment filtering

`filter_env()` removes from `os.environ`:
- `ANTHROPIC_API_KEY` -- claude uses a different authentication mechanism
- `CLAUDECODE` -- prevents nested-session errors

### Idle Timeout

A mechanism for detecting hung sessions:

1. If `idle_timeout > 0`, a `threading.Timer(idle_timeout, kill_process)` is created
2. On every output line: timer.cancel() + a new timer is created (reset)
3. If the timer fires -- the process is killed via process.terminate()

When the idle timeout fires:
- The process is killed, but not at the user's request
- Before returning, limit/error patterns are checked (idle may fire after a rate limit message)
- `result.idle_timed_out = True` is set
- `result.error` is cleared (this is not an error, it is a normal end of an idle session)

### JSON Stream parsing

The method `parse_stream(idle_touch)` reads claude's output line-by-line from `process.stdout`. Each line is parsed as a JSON dict:

```python
# Stream event structure (JSON):
{
    "type": str,                    # event type
    "message": {
        "content": [
            {"type": str, "text": str}
        ]
    },
    "content_block": {
        "type": str,
        "text": str
    },
    "delta": {
        "type": str,
        "text": str
    },
    "result": str | dict            # may be a string or {"output": "..."}
}
```

### Extracting text from events (extract_text)

| Event type | Extraction logic |
|---|---|
| `"assistant"` | All elements of `message["content"]` with `type == "text"` -- joined into a string |
| `"content_block_delta"` | If `delta["type"] == "text_delta"` -- returns `delta["text"]` |
| `"message_stop"` | The first element of `message["content"]` with `type == "text"` |
| `"result"` | Tried as a string (session summary -- skipped, content was already streamed). Then as `{"output": "..."}` -- returns output |

Non-JSON lines are written as-is to output and recent blocks (with a debug log if enabled).

### Signal detection (detect_signal)

The function `detect_signal(text)` searches the text for known signals via `in`:
- `<<<CADENCE:ALL_TASKS_DONE>>>` (Completed)
- `<<<CADENCE:TASK_FAILED>>>` (Failed)
- `<<<CADENCE:REVIEW_DONE>>>` (ReviewDone)
- `<<<CADENCE:PLAN_READY>>>` (PlanReady)
- `<<<CADENCE:QUESTION>>>` (Question)
- `<<<CADENCE:PLAN_DRAFT>>>` (PlanDraft)

The most recent detected signal overwrites the previous one (no accumulation).

### Error handling on exit

Logic after `process.wait()`:
1. Idle timeout path: if the process was killed by idle timeout -- check patterns, return idle_timed_out=True
2. If `process.returncode != 0`:
   - If the process was killed by the user (cancelled) -- return cancellation error (bypasses pattern checks)
   - If output is empty -- return the error directly ("claude exited with error")
   - If output is non-empty and signal is empty -- claude did no useful work, return an error
   - If output is non-empty and signal is non-empty -- work was done, ignore the exit code
3. Check limit patterns (priority)
4. Check error patterns
5. Return the result

Important nuance: cancellation paths bypass pattern checks. This prevents a situation where cancellation is masked as a pattern match.

## CommandRunner protocol

An interface for abstracting process launching (used for testing):

```python
class CommandRunner(Protocol):
    def run(self, name: str, *args: str) -> tuple[IO[str], Callable[[], int]]:
        """
        Returns:
            output: readable stream (stdout + stderr merged)
            wait: callable that waits for process exit, returns returncode
        """
        ...
```

The real implementation uses `subprocess.Popen` with `start_new_session=True`.

## Reading lines from process.stdout

The Python equivalent of Go's `readLines()` is iteration over `process.stdout`:

```python
for line in process.stdout:
    line = line.rstrip('\n').rstrip('\r')
    handler(line)
```

Details:
- `subprocess.Popen(stdout=PIPE, stderr=STDOUT, text=True)` -- stdout + stderr merged, text mode
- Iteration over `process.stdout` blocks until a line is received or EOF
- Cancellation: idle timeout kills the process via process.terminate(), which closes the pipe and breaks the iteration
- `line.rstrip()` to strip trailing newlines
- EOF ends iteration naturally

## Process management

### Unix

Full process group management via subprocess.Popen:

**Process startup:**
```python
process = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    start_new_session=True,  # equivalent of Go Setsid: true
    env=filtered_env,
)
```

`start_new_session=True` creates a new session, detaching the child from the parent's controlling terminal. Prevents SIGTTIN/SIGTTOU signals from descendants. The child becomes the session leader of its process group.

**ProcessGroupCleanup class:**
- `process: subprocess.Popen` -- the process
- `_killed: bool` -- guard against repeated kills

**Lifecycle:**
1. Construction: ProcessGroupCleanup(process)
2. `kill_process_group()`:
   - `os.killpg(process.pid, signal.SIGTERM)` -- sends to the entire process group
   - If ProcessLookupError (group no longer exists) -- early return
   - `time.sleep(0.1)` -- graceful shutdown delay
   - `os.killpg(process.pid, signal.SIGKILL)` -- force kill
3. `wait() -> int`:
   - `process.wait()`
   - `kill_process_group()` -- kills orphaned descendants (node subagents, MCP servers)
   - return process.returncode

### Windows

A simplified version:

- `start_new_session` is not supported in the same way as on Unix
- `process.terminate()` -- targets only the direct process (not child processes)
- No post-exit orphan cleanup (would require Job Objects)
- No graceful shutdown (SIGTERM is not supported)

Note: the SIGQUIT (Ctrl+\) break mechanism is not supported on Windows.

## Python port considerations

### JSON stream parsing
`for line in process.stdout:` + `json.loads(line)`. Line-by-line reading blocks until a line is received. Cancellation via process kill (pipe closes, iteration ends).

### Process group management
`subprocess.Popen(start_new_session=True)` + `os.killpg(process.pid, signal.SIGTERM)` -- a direct equivalent of Go Setsid + syscall.Kill(-pid). On Windows -- `CREATE_NEW_PROCESS_GROUP` or Job Objects.

### Idle timeout
`threading.Timer` with cancel/restart on each line. When it fires -- process.terminate() + os.killpg(). The timer is recreated after each cancel (threading.Timer does not support reset).

### Pattern matching
`match_pattern()` is a trivial function. Case-insensitive substring via `pattern.lower() in text.lower()`.
