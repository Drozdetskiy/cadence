# Configuration system

Reference for cadence's configuration system.

## Loading cascade

Configuration is loaded from two levels, with priority from highest to lowest:

```
CLI flags  >  local config (.cadence/config.yaml)  >  defaults (in code)
```

There is no global level (`~/.config/`). Configuration is bound to a specific project.

Main functions:
- `config.load(config_dir)` -- discovers `.cadence/`, loads config.yaml, merges with defaults
- `config.detect_local_dir()` -- looks for `.cadence/` in the cwd

### Loading order

1. Load defaults from code (dict/dataclass)
2. If `.cadence/config.yaml` exists -- parse it via `PyYAML`
3. Merge: YAML values overwrite defaults (absent key = not set, default is used)
4. Load prompts: per-file fallback local -> embedded via `importlib.resources`
5. Load agents: union of all .txt files, per-file fallback local -> embedded
6. Build the final `Config` object
7. Apply CLI overrides

### Merge strategy

**Config (values):** per-field merge. A YAML value overwrites the default. A missing key in YAML means the default is preserved. There is no need for `*Set` tracking: in YAML an absent key unambiguously means "not set", and an explicit `false`/`0` means set.

**Prompts:** per-file fallback. For each prompt file: local `.cadence/prompts/` -> embedded (via `importlib.resources`). If the file contains only comments/whitespace -- fall back to the embedded default.

**Agents:** per-file fallback + union of files. The union of .txt files from embedded and local is collected. For each unique file: local -> embedded.

**Colors:** per-field merge. A YAML value overwrites the default hex.

## YAML format

- Parser: `PyYAML` (`yaml.safe_load`)
- File: `.cadence/config.yaml`
- Comments are supported (`# ...`)
- Lists: native YAML lists (`- item` blocks)
- Duration: a string with a suffix, parsed manually -- `"30m"`, `"1h"`, `"90s"`, `"1h30m"`
- Boolean: `true`/`false` (YAML standard)

### Example `.cadence/config.yaml`

```yaml
# Claude executor
claude_command: claude
claude_args: "--dangerously-skip-permissions --verbose"
plan_model: claude-opus-4-7
task_model: claude-opus-4-7
review_model: claude-opus-4-7

# Timing
iteration_delay_ms: 2000
task_retry_count: 1
max_iterations: 50
session_timeout: "0"
idle_timeout: "0"
wait_on_limit: "0"

# Paths and VCS
default_branch: main
commit_trailer: ""
init_prompt_name: init
commit_format: |
  Format: subject line `<branch-name>.`, then a blank line, then a body with
  one clause per line — `Added: <what>`, `Changed: <what>`, `Deleted: <what>`.
  Include only the lines that apply. English.

# Error patterns
claude_error_patterns:
  - "You've hit your limit"
  - "API Error:"
  - "cannot be launched inside another Claude Code session"
  - "Not logged in"
claude_limit_patterns:
  - "You've hit your limit"

# Colors (hex format)
colors:
  task: "#2e8b57"
  review: "#1a9e9e"
  warn: "#d4930d"
  error: "#cc0000"
  signal: "#d25252"
  timestamp: "#707070"
  info: "#808080"
```

## All configuration fields

### Claude executor

| YAML key | Type | Default | Description |
|----------|------|---------|-------------|
| `claude_command` | string | `"claude"` | Command used to launch Claude Code |
| `claude_args` | string | `"--dangerously-skip-permissions --verbose"` | Arguments passed to claude |
| `plan_model` | string | `"claude-opus-4-7"` | Model used for the plan creation phase |
| `task_model` | string | `"claude-opus-4-7"` | Model used for the task execution phase |
| `review_model` | string | `"claude-opus-4-7"` | Model used for review phases |

YAML overrides via the global `--config` option (or auto-discovered `config.yaml` next to the plan/task file) override `plan_model` / `task_model` / `review_model` and `default_branch` at load time.

### Timing and iteration control

| YAML key | Type | Default | Validation | Description |
|----------|------|---------|------------|-------------|
| `iteration_delay_ms` | int | `2000` | >= 0 | Delay between iterations (ms) |
| `task_retry_count` | int | `1` | >= 0 | Number of retries on FAILED (0 = none, 1 = one retry) |
| `max_iterations` | int | `50` | >= 1 | Maximum task iterations per plan |

### Timeouts and rate limit

| YAML key | Type | Default | Description |
|----------|------|---------|-------------|
| `session_timeout` | duration string | `"0"` (disabled) | Maximum duration of a single claude session |
| `idle_timeout` | duration string | `"0"` (disabled) | Kill the session if there is no output for the given time |
| `wait_on_limit` | duration string | `"0"` (disabled) | Wait time before retrying on rate limit |

### Paths and VCS

| YAML key | Type | Default | Description |
|----------|------|---------|-------------|
| `default_branch` | string | `"main"` | Name of the default branch; can be overridden via local `.cadence/config.yaml` |
| `init_prompt_name` | string | `"init"` | Name of the prompt file mapped to `plan` (used in `derive_plan_path`) |
| `commit_trailer` | string | `""` (disabled) | Trailer appended to all commits (e.g. Co-authored-by) |
| `commit_format` | string | built-in multi-line default (subject `<branch-name>.` + blank line + `Added:`/`Changed:`/`Deleted:` body lines) | Block of commit-message formatting rules; appended to task/review prompts |

### Error pattern detection

| YAML key | Type | Default | Description |
|----------|------|---------|-------------|
| `claude_error_patterns` | list[string] | `["You've hit your limit", "API Error:", "cannot be launched inside another Claude Code session", "Not logged in"]` | Claude error patterns (case-insensitive substring) |
| `claude_limit_patterns` | list[string] | `["You've hit your limit"]` | Claude rate-limit patterns (for wait+retry) |

Check priority: limit patterns are checked first. If a match occurs and `wait_on_limit > 0` -> wait and retry. If a match occurs and `wait_on_limit == 0` -> fall through to error-pattern behaviour (exit). Limit patterns intentionally overlap with error patterns; `wait_on_limit` acts as a toggle.

Pattern matching is applied only to raw non-JSON CLI output -- pattern literals inside stream-json events (assistant/user/tool_result, etc.) are not checked, so matches in code, documentation, or tests do not produce false positives.

### Output colors

| YAML key | Default hex | Default RGB | Description |
|----------|-------------|-------------|-------------|
| `colors.task` | `#2e8b57` | `46,139,87` | Task execution phase (green) |
| `colors.review` | `#1a9e9e` | `26,158,158` | Review phase (teal) |
| `colors.warn` | `#d4930d` | `212,147,13` | Warning messages (amber) |
| `colors.error` | `#cc0000` | `204,0,0` | Error messages (red) |
| `colors.signal` | `#d25252` | `210,82,82` | Completion/failure signals (salmon red) |
| `colors.timestamp` | `#707070` | `112,112,112` | Timestamp prefix (gray) |
| `colors.info` | `#808080` | `128,128,128` | Informational messages (gray) |

YAML format: `#RRGGBB` hex string under the `colors:` section.

## Template variable system

### Base variables (all prompts)

| Variable | Fallback | Source |
|----------|----------|--------|
| `{{PLAN_FILE}}` | `"(no plan file - reviewing current branch)"` | Checks the original path, then the sibling `<stem>-completed<ext>` |
| `{{PROGRESS_FILE}}` | `"(no progress file available)"` | Path to the progress file |
| `{{GOAL}}` | -- | `"implementation of plan at <path>"` or `"current branch vs <branch>"` |
| `{{DEFAULT_BRANCH}}` | `"main"` | config `default_branch` (default `"main"`) |

### Iteration-aware variables (review prompts)

| Variable | First iteration | Subsequent iterations |
|----------|-----------------|----------------------|
| `{{DIFF_INSTRUCTION}}` | `"git diff <DEFAULT_BRANCH>...HEAD"` | `"git diff"` |
| `{{PREVIOUS_REVIEW_CONTEXT}}` | `""` (empty) | Formatted block containing Claude's previous response |

### Special variables (specific prompts)

| Variable | Used in | Description |
|----------|---------|-------------|
| `{{PLAN_DESCRIPTION}}` | make_plan.txt | Contents of the file passed to `cadence plan` (or the `init` file picked up by `cadence run plan`) |

### Agent references

| Pattern | Description |
|---------|-------------|
| `{{agent:name}}` | Expands into a Task tool instruction with the agent's prompt |

Regex: `\{\{agent:([a-zA-Z0-9_-]+)\}\}`

Expansion format:
```
Use the Task tool[ with model=X] to launch a <subagent-type> agent with this prompt:
"<agent prompt with base variables expanded>"

Report findings only - no positive observations.
```

- The agent lookup map is built from the loaded custom agents
- Missing agents: a warning is logged, the reference is left unexpanded
- Agent content: base variables are expanded, but agent references inside agent content are not recursively expanded
- Frontmatter `model` and `agent` type are honoured during expansion

### Commit trailer instruction

When `commit_trailer` is configured, the following instruction is appended to every prompt:
```
When making git commits, add the following trailer after a blank line at the end of the commit message:
<trailer value>
```

Applied once to the final assembled prompt.

### Expansion functions

| Function | Variables | Used for |
|----------|-----------|----------|
| `replace_base_variables()` | PLAN_FILE, PROGRESS_FILE, GOAL, DEFAULT_BRANCH | Base set for all prompts |
| `replace_prompt_variables()` | base + `{{agent:name}}` + commit trailer | Task and review prompts |
| `replace_variables_with_iteration()` | base + DIFF_INSTRUCTION + `{{agent:name}}` + PREVIOUS_REVIEW_CONTEXT + commit trailer | Review prompts with iteration context |
| `build_plan_prompt()` | base + PLAN_DESCRIPTION + commit trailer | Plan creation prompt |

## Comment handling and fallback

### Comment functions

| Function | Behaviour | Used for |
|----------|-----------|----------|
| `strip_comments()` | Removes every line that starts with `#` | Emptiness check (a fully commented-out file -> fallback) |
| `strip_leading_comments()` | Removes a block of 2+ consecutive `#`-lines at the start. A single `# Title` is preserved | Prompt loading (meta-comment block stripped, markdown header preserved) |
| `strip_leading_comment_lines()` | Removes every consecutive `#`-line at the start (including a single one) | Agent frontmatter detection (comments before `---` are stripped) |
| `normalize_crlf()` | CRLF -> LF | All files before processing |

### Fallback chain for prompt files

1. Read the file from the local dir `.cadence/prompts/`
2. `normalize_crlf` -> `strip_comments` -> emptiness check
3. If empty (comments/whitespace only) -> fall back to embedded
4. If non-empty -> `strip_leading_comments` -> trim -> return
5. Embedded (via `importlib.resources`): `strip_leading_comments` -> trim -> return

### Fallback chain for agent files

1. Collect the union of all .txt filenames from embedded + local
2. For each filename: local `.cadence/agents/` -> embedded
3. When loading from file: `strip_comments` checks emptiness, `parse_options` checks for the presence of a body
4. If there is no body -> fall back to the embedded default
5. If there are frontmatter options but no body -> warning + fallback (frontmatter dropped)
6. `build_agent()`: tries `parse_options` on the raw content; on failure -- `strip_leading_comment_lines` + retry

## Frontmatter for agents

### Format

```yaml
---
model: sonnet
agent: custom-reviewer
---
Agent prompt text here...
```

### Options

```python
@dataclass
class AgentOptions:
    model: str = ""      # keyword form: haiku, sonnet, opus
    agent_type: str = ""  # subagent type for the Task tool
```

### Parsing logic (`parse_options`)

1. Checks for the prefix `---\n`
2. Looks for the closing `\n---` (must be on its own line)
3. YAML parsing via `PyYAML` or the standard parser
4. `normalize_model()`: extracts the keyword from a full ID (e.g. `"claude-sonnet-4-5-20250929"` -> `"sonnet"`)
5. If the YAML is malformed -> treat as no frontmatter, return the original content
6. Returns parsed Options + body (trimmed)

### Validation

- Valid models: `haiku`, `sonnet`, `opus` (after normalisation)
- Invalid model: warning logged, Options reset to defaults
- Empty model: allowed (the default model is used)

### Agent build flow

```
build_agent(name, prompt):
  1. parse_options(prompt) -> opts, body
  2. if there is no frontmatter -> strip_leading_comment_lines(prompt) + retry parse_options
  3. if body is empty -> use the raw prompt with default Options
  4. validate() -> warnings -> if there are warnings, Options = default
  5. Return CustomAgent(name=name, prompt=body, options=opts)
```

## Embedded defaults

### Package resources (importlib.resources)

Package layout:
```
cadence/
  defaults/
    prompts/
      task.txt
      review_first.txt
      review_second.txt
      make_plan.txt
    agents/
      implementation.txt
      quality.txt
      simplification.txt
      testing.txt
```

Defaults for config values are stored in code (dataclass/dict), not in a file.

## Configuration directory

```
.cadence/
```

Looked up in the current working directory (cwd). There is no global directory.

## Environment variables

| Env var | Description |
|---------|-------------|
| `CADENCE_CONFIG_DIR` | Override the path to the configuration directory |

## Signals (output markers)

Signal format: `<<<CADENCE:...>>>`.
