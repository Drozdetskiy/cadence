# Configuration

## Config Architecture

Two-level cascade with CLI overrides:

```
CLI flags  >  .rlx/config.toml  >  defaults (in code)
```

No global config (`~/.config/`). Config is project-local only.

## Config Loading

1. Defaults defined as dataclass with default values
2. If `.rlx/config.toml` exists — parse via `tomllib` (stdlib)
3. Merge: TOML values override defaults (absent key = not set, default preserved)
4. Load prompts: per-file fallback (local `.rlx/prompts/` -> embedded via `importlib.resources`)
5. Load agents: union of .txt files, per-file fallback (local `.rlx/agents/` -> embedded)
6. Apply CLI overrides (typer flags) last

## TOML Format

Parser: `tomllib` (stdlib, Python 3.11+). File: `.rlx/config.toml`.

```toml
# Example .rlx/config.toml
max_iterations = 50
iteration_delay_ms = 2000
task_retry_count = 1
session_timeout = "30m"
idle_timeout = "5m"
wait_on_limit = "1h"
claude_model = "sonnet"
review_model = "opus"

[colors]
task = "#2e8b57"
review = "#1a9e9e"
warn = "#d4930d"
error = "#cc0000"
```

Key rules:
- Duration: string with suffix, parsed manually — `"30m"`, `"1h"`, `"90s"`, `"1h30m"`
- Colors: hex string `"#rrggbb"` (passed directly to Rich Style)
- Lists: native TOML arrays `["pattern1", "pattern2"]`
- Boolean: `true`/`false`
- Absent key = not set (no need for `*Set` tracking, TOML is unambiguous)

## Prompt/Agent Loading

Two-level per-file fallback:
1. Local: `.rlx/prompts/<name>.txt` or `.rlx/agents/<name>.txt`
2. Embedded: `src/rlx/defaults/prompts/` or `src/rlx/defaults/agents/` via `importlib.resources`

Local file completely replaces embedded (no merge). File with only whitespace/comments falls back to embedded.

## Template Variables

Prompts support `{{NAME}}` variables, substituted at load time:

| Variable | Description |
|----------|-------------|
| `{{PLAN_FILE}}` | Path to plan file |
| `{{PROGRESS_FILE}}` | Path to progress log |
| `{{GOAL}}` | Work goal description |
| `{{DEFAULT_BRANCH}}` | Default git branch |
| `{{PLANS_DIR}}` | Plans directory |
| `{{PLAN_DESCRIPTION}}` | Plan description (make_plan only) |
| `{{agent:name}}` | Expands to Task tool instruction for agent |

## Environment Variables

- `RLX_*` namespace (if needed in future)
- No env vars for config fields currently — all via TOML or CLI flags
- `$VISUAL` / `$EDITOR` / `vi` — editor for interactive plan review

## Local Project Directory

```
.rlx/
├── config.toml     # TOML config, per-field merge with defaults
├── prompts/        # per-file fallback: local -> embedded
│   └── task.txt    # override specific prompt
└── agents/         # per-file fallback: local -> embedded
    └── custom.txt  # project-specific agent
```
