# Progress logging and input system

Reference for the `progress` and `input` modules.

## Overview

Two modules handle I/O and the user interface:

- `progress` -- progress logging to file and stdout with timestamps, colors, and file locking
- `input` -- terminal input: numbered lists, editor, markdown rendering

Key modules:
- `cadence/progress/logger.py` -- Logger class, Config, file format, timestamps
- `cadence/progress/colors.py` -- Colors class, phase-to-color mapping, RGB parsing
- `cadence/progress/flock.py` -- file locking (Unix `fcntl.flock`)
- `cadence/input/input.py` -- TerminalCollector, AskQuestion, AskDraftReview, read_line_with_context

---

## Progress logging (progress)

### Config

```python
@dataclass
class ProgressLoggerConfig:
    progress_path: str             # absolute or relative path to the progress file (computed in cli.py)
    plan_file: str = ""            # path to the plan (header only)
    branch: str = ""               # current branch (header only)
    mode: Mode = Mode.PLAN         # mode (header only)
    plan_description: str = ""     # description (legacy parameter, header only)
    no_color: bool = False         # disable colors
```

The config is intentionally thin: the progress-file path is computed once in `cli.compute_progress_path()` and passed in already resolved. The fields `tasks_root`, `default_branch`, and `head_hash` are no longer in the config -- `cli.py` owns them and uses them only when computing `progress_path`.

### Logger class

```python
class Logger:
    def __init__(self, cfg: Config, colors: Colors, holder: PhaseHolder):
        self._file: IO           # progress-file handle
        self._stdout: IO         # sys.stdout
        self._start_time: datetime  # creation time (for elapsed time)
        self._holder: PhaseHolder   # current phase, for color
        self._colors: Colors        # color configuration
```

Construction procedure:
1. Resolve `cfg.progress_path` to an absolute path
2. Create the parent directory (0o750)
3. Open the file in append mode (0o600)
4. Acquire an exclusive file lock (`fcntl.flock`)
5. Check for the completion footer:
   - Footer present -> truncate the file, write a fresh header
   - No footer, file non-empty -> write a restart separator
   - File empty -> write a header

### Public Logger methods

| Method | Description |
|---|---|
| `path() -> str` | absolute path to the progress file |
| `print(format, *args)` | timestamp + message to file and stdout (current-phase color) |
| `print_section(section)` | section header: "\n--- {label} ---\n" |
| `print_aligned(text)` | timestamp on every line, skip blank lines, word wrap, list indent, signal highlighting |
| `error(format, *args)` | "ERROR: " prefix in red |
| `warn(format, *args)` | "WARN: " prefix in yellow |
| `log_question(question, options)` | "QUESTION: " + "OPTIONS: opt1, opt2, ..." |
| `log_answer(answer)` | "ANSWER: " |
| `log_draft_review(action, feedback)` | "DRAFT REVIEW: " + optional "FEEDBACK: " |
| `log_diff_stats(files, additions, deletions)` | "DIFFSTATS: files=F additions=A deletions=D" (file only) |
| `elapsed() -> str` | formatted time since start (>1h: truncated to minutes; <1h: truncated to seconds) |
| `close()` | footer with separator and completion timestamp, release lock, close file |

### File format

**Header (fresh start):**
```
# CADENCE Progress Log
Plan: path/to/plan.md
Branch: feature-branch
Mode: full
Started: 2006-01-02 15:04:05
------------------------------------------------------------

```

**Timestamped lines:**
```
[YY-MM-DD HH:MM:SS] message text
[YY-MM-DD HH:MM:SS] ERROR: error message
[YY-MM-DD HH:MM:SS] WARN: warning message
[YY-MM-DD HH:MM:SS] QUESTION: what to do?
[YY-MM-DD HH:MM:SS] OPTIONS: opt1, opt2, opt3
[YY-MM-DD HH:MM:SS] ANSWER: opt1
[YY-MM-DD HH:MM:SS] DRAFT REVIEW: accept
[YY-MM-DD HH:MM:SS] FEEDBACK: looks good
[YY-MM-DD HH:MM:SS] DIFFSTATS: files=5 additions=42 deletions=10
```

**Section headers:** `\n--- section label ---\n`

**Signals:** `<<<CADENCE:SIGNAL_NAME>>>` -- rendered in the signal color

**Restart separator** (when appending to an unfinished file):
```


--- restarted at 2006-01-02 15:04:05 ---


```

**Footer (on close):**
```
------------------------------------------------------------
Completed: 2006-01-02 15:04:05 (1h23m45s)
```

### Fresh start: truncating completed files

Completion detection (`_is_progress_completed`):
1. Read the last ~256 bytes of the file
2. Look for the pattern: 60-dash separator + "Completed:" line
3. A naive "Completed:" check would false-positive if Claude mentioned the word in its output

Check ordering:
1. Lock is acquired BEFORE stat (prevents a TOCTOU race)
2. File > 0 with footer -> truncate
3. File > 0 without footer -> restart separator, existing content preserved
4. File empty -> fresh header

### Path computation (cli.compute_progress_path)

The progress-file path is computed in `cli.py` via `compute_progress_path(mode, *, plan_file, branch, default_branch, head_hash, tasks_root)` and passed into `ProgressLoggerConfig.progress_path`. `Logger` does not compute the path itself.

Rules per mode:

- `Mode.PLAN` (requires `plan_file`): `<dirname(plan_file)>/progress-plan.txt`
- `Mode.FULL` (requires `plan_file`): `<dirname(plan_file)>/progress-task.txt`
- `Mode.REVIEW`: `<tasks_root>/<segment>/progress-review.txt`, where `segment` is
  `_sanitize_plan_name(branch)` if the branch is non-empty and not equal to `default_branch`
  (after stripping any `origin/` prefix); otherwise `head_hash[:12]` (on the default branch
  or detached HEAD). If both branch and hash are empty -- `RuntimeError`.

If `Mode.PLAN`/`Mode.FULL` is invoked without `plan_file` -- `RuntimeError`. There is no fallback to the old `.cadence/progress/` directory.

Sanitization (`_sanitize_plan_name`): lowercase, `/` and `\` -> hyphens, spaces -> hyphens, alphanumeric + hyphens only, collapse runs, trim, limit 50 chars, fallback "unnamed". So branch `feat/foo` becomes segment `feat-foo`.

### File locking

**Unix (`fcntl.flock`):**

```python
def lock_file(f: IO) -> None      # fcntl.flock(fd, LOCK_EX) -- blocking exclusive lock
def unlock_file(f: IO) -> None    # fcntl.flock(fd, LOCK_UN)
def try_lock_file(f: IO) -> bool  # LOCK_EX|LOCK_NB -- non-blocking
```

`try_lock_file` returns:
- `True` -- lock acquired (file was not locked)
- `False` -- file locked by another process (EWOULDBLOCK)

A lock acquired via `try_lock_file` is released immediately (the goal is the check only).

File locking guarantees exclusive access to the progress file -- two cadence processes will not write to the same file simultaneously.

### Colors class

```python
class Colors:
    def __init__(self, cfg: ColorConfig):
        self._task: Style       # phase color
        self._review: Style     # phase color
        self._warn: Style       # service color
        self._err: Style        # service color
        self._signal: Style     # service color
        self._timestamp: Style  # UI color
        self._info: Style       # UI color
        self._phases: dict[Phase, Style]  # mapping for for_phase()
```

All colors are stored as hex strings (`#RRGGBB`) and passed straight to `rich.style.Style(color=...)`. An invalid value is a configuration error, not a runtime one.

Phase-to-color mapping (`_phases` contains only `PhaseReview`; everything else falls back to task via `.get(phase, self._task)`):

| Phase | Color |
|---|---|
| PhaseReview | review |
| PhaseTask / PhasePlan / unknown | task (green, via fallback) |

Methods:
- `for_phase(p: Phase) -> Style` -- color for the phase (fallback: task)
- `timestamp() -> Style`
- `warn() -> Style`
- `error() -> Style`
- `signal() -> Style`
- `info() -> Style`

---

## Input system (input)

### TerminalCollector class

```python
class TerminalCollector:
    def __init__(self, no_color: bool = False):
        self._stdin: IO = sys.stdin
        self._stdout: IO = sys.stdout
        self._no_color: bool
```

### ask_question

`ask_question(question: str, options: list[str]) -> str`

Offers a choice from a numbered list of options:
1. Appends "Other (type your own answer)" to the end of the list
2. Filters incoming options to avoid collisions with the "Other" sentinel
3. Prints the numbered list ("Enter number (1-N):")
4. When "Other" is selected -- prompts for free-form input

### ask_yes_no

`ask_yes_no(prompt: str) -> bool`

Prompt in `[y/N]` form:
- "y", "yes" (case-insensitive) -> True
- Anything else -> False
- EOF, empty input, read errors -> False

### ask_draft_review

`ask_draft_review(question: str, plan_content: str) -> tuple[str, str]`

Shows the plan for review:
1. Renders markdown via rich (when `no_color=False`)
2. Displays it with a frame
3. Numbered menu with 4 options:
   - **Accept** -- returns `ACTION_ACCEPT`, feedback=""
   - **Revise** -- prompts for revision text, returns `ACTION_REVISE` + feedback
   - **Interactive review** -- opens `$EDITOR`, computes a unified diff
   - **Reject** -- returns `ACTION_REJECT`, feedback=""

Action constants:
```python
ACTION_ACCEPT = "accept"
ACTION_REVISE = "revise"
ACTION_REJECT = "reject"
```

Interactive review flow:
1. Opens `$EDITOR` on a temp file (`cadence-plan-*.md`)
2. `difflib.unified_diff(original, edited)` -- unified diff with context
3. If the diff is empty -- "no changes detected", menu repeats
4. If the diff is non-empty -- returns `ACTION_REVISE` with the diff wrapped in instructions for Claude

Editor lookup order: `$VISUAL` -> `$EDITOR` -> `vi`. Editors with arguments are supported (e.g. `"code --wait"`).

### read_line_with_context

`read_line_with_context(reader: IO) -> str`

Reads a line from the reader with cancellation support:
1. Checks for cancellation
2. Reads the line via `readline()`

Allows Ctrl+C (SIGINT) to interrupt a blocking stdin read.

### Call graph

```
ask_question
  └─ _select_with_numbers() → _read_custom_answer() → read_line_with_context()

ask_draft_review
  ├─ _render_markdown() [rich]
  ├─ _select_with_numbers() [in retry loop]
  ├─ read_line_with_context() [for feedback]
  ├─ _open_editor() [subprocess $EDITOR]
  └─ _compute_diff() [difflib]

ask_yes_no
  └─ read_line_with_context()
```

---

## Python port considerations

### File locking
`fcntl.flock` (Unix). On Windows -- `msvcrt.locking` or a no-op.

### Progress file format
The format is plain text, parsed line by line. `open(file, 'a')` for append, `fcntl.flock` for locking, `datetime.strftime` for timestamps.

### Terminal input
Numbered selection -- `input()` with validation. Markdown rendering -- `rich.markdown.Markdown`. Editor -- `subprocess.run([$EDITOR, tmpfile])`.

### Colors
`rich` (full RGB support). `rich.console.Console` with `style` parameters.

### Word wrap and list indent
`shutil.get_terminal_size()`. Word wrap -> `textwrap.fill()`. List indent is trivial.

### Unified diff
`difflib.unified_diff` (stdlib). Format matches.
