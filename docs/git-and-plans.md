# Git operations and plan handling

Reference document for the `git` and `plan` modules of cadence.

## Overview

Two modules provide VCS interaction and plan management:

- `git` -- unified API for git operations (branches, commits, diffs) via subprocess
- `plan` -- markdown plan parsing and branch name extraction

Key modules:
- `cadence/git/service.py` -- Service class, all public methods
- `cadence/git/backend.py` -- ExternalBackend: CLI-based implementation (git)
- `cadence/plan/plan.py` -- ExtractBranchName()
- `cadence/plan/parse.py` -- ParsePlan(), ParsePlanFile(), Task/Checkbox/Plan types

## Logger interface

Compatible with the progress logger and the standard `logging.Logger`. Service methods log through the supplied logger.

## Backend interface

Abstracts low-level git operations:

```python
class Backend(Protocol):
    def root(self) -> str: ...
    def head_hash(self) -> str: ...
    def has_commits(self) -> bool: ...
    def current_branch(self) -> str: ...
    def get_default_branch(self) -> str: ...
    def branch_exists(self, name: str) -> bool: ...
    def create_branch(self, name: str) -> None: ...
    def checkout_branch(self, name: str) -> None: ...
    def diff_fingerprint(self) -> str: ...
    def is_dirty(self) -> bool: ...
    def has_changes_other_than(self, path: str) -> list[str]: ...
    def move_file(self, src: str, dst: str) -> None: ...
    def commit_files(self, msg: str, *paths: str) -> None: ...
    def create_initial_commit(self, msg: str) -> None: ...
    def diff_stats(self, base_branch: str) -> DiffStats: ...
```

The only implementation is `ExternalBackend` (calls the git CLI via subprocess).

## DiffStats

```python
@dataclass
class DiffStats:
    files: int = 0      # number of files changed
    additions: int = 0   # lines added
    deletions: int = 0   # lines deleted
```

Returned from `Service.diff_stats()` and `backend.diff_stats()`. Binary files (`-` in numstat) count as 1 file with no additions/deletions.

## Service class

```python
class Service:
    def __init__(self, path: str, log: Logger):
        self._repo: ExternalBackend
        self._log: Logger
        self._trailer: str = ""  # optional trailer line appended to all commits
```

The only public API of the `git` module. All operations are methods on `Service`.

### Constructor

```python
def __init__(self, path: str, log: Logger) -> None
```

- `path` -- path to the repository (`.` for the current directory)
- Creates an `ExternalBackend`, which validates the path via `rev-parse --show-toplevel`
- Resolves symlinks for consistent path comparison (macOS `/var` -> `/private/var`)

### Commit Trailer

```python
def set_commit_trailer(self, trailer: str) -> None
def _append_trailer(self, msg: str) -> str  # private
```

If `trailer` is non-empty, `_append_trailer()` appends `"\n\n" + trailer` to the commit message. Applied to all commits made through Service:
- `ensure_has_commits()` -- "initial commit"

`mark_plan_completed()` does not create commits -- it is an in-place rename without invoking git.

### Repository state methods

| Method | Description |
|---|---|
| `root() -> str` | Absolute path to the repository root |
| `head_hash() -> str` | SHA of the current HEAD commit |
| `diff_fingerprint() -> str` | SHA256 hash of the working tree state for stalemate detection |
| `current_branch() -> str` | Current branch name; empty string for detached HEAD |
| `is_default_branch(default_branch: str) -> bool` | Checks whether the current branch matches the default |
| `get_default_branch() -> str` | Determines the default branch (algorithm below) |
| `has_commits() -> bool` | Whether at least one commit exists |
| `diff_stats(base_branch: str) -> DiffStats` | Statistics for changes in base...HEAD |

### Default branch detection algorithm

Implemented in `ExternalBackend.get_default_branch()`:

1. Tries `git symbolic-ref refs/remotes/origin/HEAD` -- on success, extracts the branch name from `refs/remotes/origin/<name>`
   - If the local branch `refs/heads/<name>` exists, returns `<name>`
   - Otherwise returns `origin/<name>` (remote-tracking ref)
2. Iterates over `["main", "master", "trunk", "develop"]` -- returns the first existing local branch
3. Fallback -- `"master"`

`Service.is_default_branch(default_branch)` compares the current branch with the supplied `default_branch`:
- Strips the `origin/` prefix before comparison
- Returns `False` if the current branch is empty (detached HEAD) or if `default_branch` is empty

### Branch operations

**create_branch(name: str) -> None**
- Delegates to `backend.create_branch()` -- `git checkout -b <name>`

**create_branch_for_plan(plan_file: str, default_branch: str) -> None**
- The main method for creating a feature branch when starting a plan
- The plan file is not committed: the branch is created (or checked out), the plan stays in the working tree as before
- Sequence:
  1. `_resolve_filesystem_case(plan_file)` -- resolves the case of the file name
  2. `_prepare_plan_branch()` -- validation, branch name extraction, dirty-files check
  3. If already off the default branch -- return (already on a feature branch)
  4. If the branch exists -- `checkout`, otherwise `checkout -b`

**_prepare_plan_branch(plan_file: str, default_branch: str) -> str** (private)
- Checks the current branch: if not on the default branch, returns an empty string (caller skips)
- Extracts the branch name via `plan.extract_branch_name(plan_file)`
- Checks for dirty files via `has_changes_other_than(plan_file)` -- raises if any
- Returns the branch name

### Plan-file operations

**mark_plan_completed(plan_file: str) -> None**
- Renames the plan file in place: `<stem><ext>` -> `<stem>-completed<ext>` in the same directory (e.g. `plan.md` -> `plan-completed.md`, `preprompt` -> `preprompt-completed`)
- Does not call `git add`, `git mv`, or `git commit` -- plan files are usually gitignored, so a commit is unnecessary
- Resolves the case of the file name via `_resolve_filesystem_case()` before renaming
- The target path is computed by the module-level helper `_completed_plan_path(plan_file)`
- If source does not exist but target already exists -- log "plan already marked completed" + return (idempotent)
- If neither source nor target exists -- raise `FileNotFoundError`
- On success uses `os.rename(src, dst)` and logs "marked plan completed: <new path>"

### Auxiliary operations

**ensure_has_commits(prompt_fn: Callable[[], bool]) -> None**
- Checks whether commits exist via `has_commits()`
- If empty -- calls `prompt_fn()` for confirmation
- `create_initial_commit()` -- `git add -A` + `git commit`

### Case-insensitive path resolution

```python
def _resolve_filesystem_case(self, path: str) -> str
```

Handles macOS APFS case-insensitive filesystems, where git may track a file with one case while the caller passes another.

Algorithm:
1. Reads the parent directory via `os.listdir(dir)`
2. If an exact match is found -- returns the original path
3. If a case-insensitive match is found -- returns the path with the actual case
4. Fallback -- the original path

Used in: `create_branch_for_plan()`, `mark_plan_completed()`.

## ExternalBackend

```python
class ExternalBackend:
    def __init__(self, path: str):
        self._path: str   # absolute path to repository root
```

Implements Backend through CLI calls. The `git` command is hard-coded.

### Constructor

```python
def __init__(self, path: str) -> None
```

1. `Path(path).resolve()` -- absolute path
2. `git rev-parse --show-toplevel` -- validation and root retrieval
3. `os.path.realpath(root)` -- symlink resolution for consistency
4. On error -- parses stderr for an informative message

### run() -- command execution

```python
def _run(self, *args: str) -> str
```

- `subprocess.run(["git", *args], cwd=self._path, capture_output=True, text=True)`
- Trailing whitespace is stripped (`rstrip()`); leading whitespace is preserved (needed for porcelain)
- On error, stderr is included in the message
- `_run` delegates to `_run_with_status` and raises `RuntimeError` on a non-zero exit code, so all subprocess logic lives in one place

### DiffFingerprint

SHA256 hash of the working tree state for stalemate detection:

1. `git diff HEAD` -- tracked changes
2. `git ls-files -z --others --exclude-standard` -- untracked files (null-terminated for special characters)
3. Hashes: diff output + for each untracked file: name + `git hash-object` (blob hash of the contents)
4. This ensures detection of changes inside existing untracked files, not only newly created ones

### has_commits

- `git rev-parse HEAD` with `LC_ALL=C` for English stderr
- Exit code 128 + `"ambiguous argument"` in stderr -> empty repository (return False)
- Other exit-128 reasons (corruption, permission) -> propagate error

### current_branch

- `git symbolic-ref --short HEAD` with `LC_ALL=C`
- Exit 128 + `"not a symbolic ref"` -> detached HEAD (return "")
- Other exit-128 reasons -> propagate error

### is_dirty

- `git status --porcelain`
- Walks the lines, ignores untracked (`??`) -- they are not considered dirty
- Any other line (modified, staged, deleted) -> dirty

### has_changes_other_than

`has_changes_other_than(path)`:
- `git status --porcelain -uall` -- all files
- Parses each line through `_extract_path_from_porcelain()`
- Case-insensitive comparison to exclude the plan file
- Returns the list of dirty files

### _extract_path_from_porcelain

```python
def _extract_path_from_porcelain(self, line: str) -> str
```

Parses the `"XY path"` or `"XY original -> renamed"` format:
- Skips the first 3 characters (2-char status + space)
- Handles rename (`" -> "`) -- takes the new name

### diff_stats

```python
def diff_stats(self, base_branch: str) -> DiffStats
```

1. `_resolve_ref(base_branch)` -- resolves the branch name to a ref
2. If the ref is not found or HEAD == base hash -- return zero stats
3. `git diff --numstat <baseRef>...HEAD`
4. Parses lines `additions\tdeletions\tfile`
5. Binary files (`-` for additions/deletions) -- only +1 to Files

### _resolve_ref

```python
def _resolve_ref(self, branch_name: str) -> str
```

Tries to resolve the name in this order:
1. Local branch: `refs/heads/<name>`
2. Remote tracking: `refs/remotes/origin/<name>`
3. As-is for `origin/`-prefixed: `refs/remotes/origin/<remoteName>`
4. Arbitrary ref via `git rev-parse --verify --quiet <name>` (commit hash, tag)
5. Empty string if nothing is found

### _ref_exists

- `git show-ref --verify --quiet <ref>` -- exit 0 = exists

### _to_relative

```python
def _to_relative(self, path: str) -> str
```

Converts a path to one relative to the repository root:
- If the path is relative -- `os.path.normpath()`, check for `..` (escape)
- If absolute -- `os.path.realpath` for the dir + `os.path.relpath` from `self._path`
- Error if the path is outside the repository

### File and commit operations

| Backend method | git command |
|---|---|
| `move_file(src, dst)` | `git mv -- <srcRel> <dstRel>` |
| `commit_files(msg, *paths)` | `git commit -m <msg> -- <rel1> <rel2> ...` |
| `create_initial_commit(msg)` | `git add -A` + staged check + `git commit -m <msg>` |
| `create_branch(name)` | `git checkout -b <name>` |
| `checkout_branch(name)` | `git checkout <name>` |
| `branch_exists(name)` | `git show-ref --verify --quiet refs/heads/<name>` |

---

## plan module

### Data types

```python
class TaskStatus(str, Enum):
    PENDING = "pending"   # no checkboxes checked
    ACTIVE = "active"     # some checkboxes checked
    DONE = "done"         # all checkboxes checked
    FAILED = "failed"     # defined but not set by the parser

@dataclass
class Checkbox:
    text: str
    checked: bool

@dataclass
class Task:
    number: int
    title: str
    status: TaskStatus
    checkboxes: list[Checkbox]

@dataclass
class Plan:
    title: str
    tasks: list[Task]
```

### Plan-file format

Markdown with a defined structure:
- **Title**: the first `# heading` (h1)
- **Task headers**: `### Task N: Title` or `### Iteration N: Title` (regex: `^###\s+(?:Task|Iteration)\s+([^:]+?):\s*(.*)$`)
- **Checkboxes**: lines `- [ ] text` or `- [x] text` (regex: `^\s*-\s+\[([ xX])\]\s*(.*)$`); indentation supported
- **Section boundaries**: `##` (h2) or `#` (h1, when the title is already set) closes the current task -- checkboxes below are not bound to a task
- Important: `###` and `####` do NOT close a task (they are subsections)

Example:
```markdown
# Feature Implementation Plan

## Overview
Description here...

### Task 1: Setup project structure
- [x] Create directory layout
- [ ] Add configuration files
  - [ ] Add config.yaml (indented sub-items supported)

### Task 2: Implement core logic
- [ ] Write parser
- [ ] Add validation

## Success criteria
- All tests pass
```

### Format-description checkboxes

The regex `format_in_text = re.compile(r'\[\s*[ xX]?\s*\]')` identifies checkboxes whose text contains a `[ ]` or `[x]` pattern. These are format descriptions, not actionable items. They are ignored when determining completion status.

Example: `- [ ] Plan format: Checkboxes (\`- [ ]\` / \`- [x]\`) belong only in Task sections` -- the text contains `[ ]`, so it is a format description, not actionable.

### parse_plan(content: str) -> Plan

Parses a markdown string into a structured Plan:
1. Looks for the first `# heading` as the title
2. On each `### Task N: Title` / `### Iteration N: Title`:
   - Saves the previous task (if any) with its computed status
   - Creates a new task with the extracted number and title
3. Checkbox lines inside a task context are appended to the current task
4. `##` or `# (after the title)` closes the current task
5. The last task is saved after end-of-file

`_parse_task_num(s)` -- `int(s)`, returns 0 for non-numeric values.

### parse_plan_file(path: str) -> Plan

Wrapper: `Path(path).read_text()` -> `parse_plan(content)`.

### file_has_uncompleted_checkbox(path: str) -> bool

Scans a file for unfinished actionable checkboxes without binding to task headers. Used for malformed plans (without `### Task` headers) so they are not treated as completed.

- Ignores format-description checkboxes (via `format_in_text`)
- Returns True at the first `- [ ]` found with actionable text

### determine_task_status(checkboxes: list[Checkbox]) -> TaskStatus

- Empty list -> `Pending`
- All checked -> `Done`
- Some checked -> `Active`
- None checked -> `Pending`

### Task.has_uncompleted_actionable_work() -> bool

Returns True if there is at least one unchecked actionable checkbox (text without a `[ ]`/`[x]` pattern).

### Checkbox.is_actionable() -> bool

Returns False if `format_in_text` matches the checkbox text.

### extract_branch_name(plan_file: str) -> str

Extracts the branch name from the plan file name:
1. `Path(plan_file).stem` -- file name without extension
2. Regex `^[\d-]+` strips a date prefix (e.g. `2024-01-15-`)
3. Strips leading dashes
4. If the result is empty (date only) -- returns the original name without `.md`

Examples:
- `2024-01-15-auth-refactor.md` -> `auth-refactor`
- `feature-login.md` -> `feature-login`
- `2024-01-15.md` -> `2024-01-15` (fallback to the full name)

---

## Module relationships

The `git` module imports `plan` for one function: `plan.extract_branch_name()` is used in `_prepare_plan_branch()` to extract the branch name from the plan file. This is the only dependency.

`mark_plan_completed()` historically lives in the `git` module (as a replacement for `move_plan_to_completed`); today it is just `os.rename` without any git invocation, but the method is kept on `Service` next to the other plan-file operations (`create_branch_for_plan`).

## Python port considerations

### Git backend
The direct equivalent of Go's `exec.CommandContext` is `subprocess.run()`. All operations go through subprocess for simplicity.

### Path resolution
`os.path.abspath` / `pathlib.Path.resolve()`. `os.path.realpath()` for symlinks. `os.path.relpath()` for relative paths. Case-insensitive resolution: `os.listdir()` + casefold comparison.

### Plan parsing
Markdown parsing through regex -- a direct port. Python's `re` module is fully compatible. Iteration via `str.splitlines()`.

### JSON serialization
`dataclasses.asdict()` + `json.dumps()`.
