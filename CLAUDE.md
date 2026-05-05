# cadence

Python CLI for autonomous task execution via Claude Code. Subcommand-style surface (one action per command), with global `--config <path>` on the Typer callback. Subcommands: `cadence init <task-name>` (scaffolds a new branch + `<tasks_root>/<task-name>/init`, plus `config.yaml` recording the parent branch when not on `default_branch`; no Claude); `cadence run` (auto-detects the current branch's task phase under `tasks_root` — dispatches to plan creation when only `init` exists, plan execution when `plan` exists, or reports "already completed" when `plan-completed` exists); `cadence run plan` and `cadence run task` (explicit branch-bound forms of the same dispatch); `cadence plan <path>` (path-bound plan creation: interactive Q&A → review → final plan written next to the input); `cadence task <path>` (path-bound task execution: branch creation → iterative task execution → review_first → review_loop); `cadence review [--base <branch>]` (review-only of the current branch: review_first → review_loop, no plan, no branch creation); `cadence squash` (squashes branch commits into one with a Claude-authored message summarizing the diff against the default branch); `cadence chain <path>` (reads an ordered list of task names from a file and runs each end-to-end as plan → task → squash on its own branch branched off each task's resolved `default_branch`, after a pre-flight check that every listed task has a directory + `init` file under `tasks_root`; fails fast and leaves the repo on the failing task's branch). Pipeline composition between top-level subcommands uses shell `&&`. Exit codes follow git: `0` success, `1` runtime failure, `2` misuse.

## Package structure

```
src/cadence/
  cli.py            - Typer entrypoint, subcommand dispatch (init/run/plan/task/review/squash/chain), global `--config` callback, SIGINT/SIGQUIT handling
  config.py         - Config/ColorConfig dataclasses, YAML loading via PyYAML, parse_duration(), YAML model overrides (load_yaml_config/apply_yaml_overrides/find_yaml_config); `tasks_root` (default `cdc-tasks`) is configurable in `.cadence/config.yaml`
  status.py         - Phase/Signal constants, Section dataclass, PhaseHolder
  input.py          - TerminalCollector: interactive Q&A with numbered picker, ask_yes_no()
  executor/
    claude_executor.py - ClaudeExecutor: subprocess + JSON stream parsing, idle timeout, activity callbacks
    process_group.py   - ProcessGroupCleanup: SIGTERM/SIGKILL process group management
    events.py          - Typed Claude stream event dataclasses (AssistantEvent, ContentBlockDeltaEvent, ResultEvent) + parse_event()
  git/
    __init__.py     - Re-exports: Service, ExternalBackend, DiffStats
    backend.py      - ExternalBackend: git subprocess wrapper (hard-coded `git` command); DiffStats dataclass
    service.py      - Service: high-level git ops (constructor raises if path is not a repo, branch creation for plan (no plan commit), commit trailer, rename plan in-place with -completed suffix); satisfies the `GitChecker` Protocol declared in `processor/runner.py`
  plan/
    __init__.py     - Re-exports: Plan, Task, Checkbox, TaskStatus, parse_plan, extract_branch_name
    parse.py        - Plan/Task/Checkbox dataclasses, markdown parsing, file_has_uncompleted_checkbox
    plan.py         - extract_branch_name
  processor/
    signals.py      - Signal payload parsing (QUESTION, PLAN_READY, ALL_TASKS_DONE, TASK_FAILED, REVIEW_DONE) + is_* helpers
    prompts.py      - Prompt loading with local override fallback; build_plan_prompt, build_task_prompt, build_review_first_prompt, build_review_second_prompt; expand_agent_references / format_agent_expansion / replace_prompt_variables
    agents.py       - Agent loader (local .cadence/agents/<name>.txt → embedded cadence.defaults.agents); AgentDef, frontmatter parser, model normalization
    runner.py       - Runner: orchestrates plan creation, task execution, and review (run_claude_review + run_claude_review_loop) phases via Protocol dependencies; supports an optional second review_executor; break/pause + session timeout; Mode.REVIEW dispatch
  progress/
    colors.py       - Rich Style mapping from ColorConfig
    flock.py        - File locking via fcntl.flock
    logger.py       - Dual file+stdout logger with timestamps and signal highlighting; resolves the progress path per mode (`progress-plan.txt`/`progress-task.txt` next to the plan file for plan/full; `<tasks_root>/<branch-or-head-hash>/progress-review.txt` for review)
  defaults/
    prompts/        - Embedded prompt templates (make_plan.txt, task.txt, review_first.txt, review_second.txt)
    agents/         - Embedded agent bodies (quality.txt, implementation.txt, testing.txt, simplification.txt) referenced from review prompts via {{agent:<name>}} markers
```

## Key commands

Run tools directly from the project venv (`source venv/bin/activate`). Do NOT use `pdm run`.

For package operations (build, install, publish, dependency management) always use `pdm` — `pdm build`, `pdm add`, `pdm install`, `pdm publish`. Do NOT use raw `pip install`, `python -m build`, or other pip-based workflows; the project is configured around PDM (`pdm.lock`, `pdm-backend`).

```bash
pytest tests/ -v                # run tests
ruff check src/ tests/          # lint
ruff format src/ tests/         # format
mypy src/                       # strict type check
cadence --version               # verify CLI
make check                      # all of the above
```

## Coding conventions

- Python 3.14+, strict mypy
- Protocol-based interfaces for all Runner dependencies (Executor, Logger, InputCollector, GitChecker)
- Signal format: `<<<CADENCE:SIGNAL_NAME>>>` (e.g. `<<<CADENCE:PLAN_READY>>>`, `<<<CADENCE:QUESTION>>>`)

## Testing patterns

- Mock `CommandRunner` protocol for executor tests (avoid real Claude subprocess)
- Mock stdin/stdout for input/terminal tests
- Use `tmp_path` fixtures for file-based tests (config YAML, logger output, git repos)
- Never launch real Claude or require real git repos except via tmp_path

## Deeper reference

Module-level details live in `docs/`: `config.md`, `processor.md`, `executor.md`, `git-and-plans.md`, `progress-and-input.md`. Read on demand.

## Branch and commit flow

Never commit directly on `main`. Every change — features, fixes, release prep, version bumps, metadata edits — lands on a numbered feature branch named `<NNNN>-<slug>` (continuing the sequence visible in `git log`, e.g. after `0019-…` use `0020-…`). The user pushes the branch and merges via GitHub PR. If you find yourself on `main` with edits to commit, create the branch first (`git switch -c <NNNN>-<slug>`).

## Commit messages

Format: a single line `<branch-name>. <Clause>: <what>.` where `<Clause>` is `Added`, `Changed`, or `Deleted`. English. No blank line, no multi-line body — the whole commit message is one line. The PR title will carry that line verbatim; expand on details in the PR description if needed.

A single commit can carry any combination of `Added`, `Changed`, and `Deleted` clauses, separated by `. ` (period + space). Within one clause, list multiple items separated by `; ` (semicolon + space). Always include only the clauses that apply.

Each item is **one short clause** in plain language describing the user-visible outcome — what someone reading `git log --oneline` cares about. Implementation details (method/test/file names, renames, formatter passes, doc syncs) belong in the diff, not the commit. When squashing, write a fresh summary — do not concatenate the sub-commit messages.

Good (single clause):
```
0030-chain. Added: cadence chain command runs an ordered list of tasks from a file, each on its own branch, failing fast if a task fails.
```

Good (multiple clauses, multiple items):
```
0014-no-plan-commit-on-start. Changed: cadence no longer auto-commits the plan file when starting a task. Deleted: now-unused commit_plan_file; file_has_changes helpers.
```

Bad (verbose, name-listing, sub-commit concat): `0014-... Changed: _prepare_plan_branch returns only branch name (drops needs_commit), create_branch_for_plan no longer auto-commits, ruff format applied, test_creates_branch_and_commits renamed to test_creates_branch_no_commit, ...`

Author as the user — no `Co-Authored-By` trailer.

## Releasing a new version

The package is published as `cadence-runner` on PyPI; the Homebrew formula lives in [Drozdetskiy/homebrew-cadence](https://github.com/Drozdetskiy/homebrew-cadence) and exposes the CLI as `cadence`.

1. **Add a CHANGELOG.md entry** for the new version using the existing format (`## vX.Y.Z - YYYY-MM-DD`, then sections like New Features / Fixes / Other). Focus on user-visible changes since the previous tag — new flags, behavior changes, fixes — not internal refactors.
2. **Bump version** in `src/cadence/__init__.py` on a `<NNNN>-<slug>` branch (same branch as the changelog entry); merge to `main` via PR.
3. **Build and publish to PyPI**:
   ```bash
   rm -rf dist/ && pdm build
   python3 - <<'PY'
   import configparser, os, subprocess
   c = configparser.ConfigParser(); c.read(os.path.expanduser("~/.pypirc"))
   env = {**os.environ, "PDM_PUBLISH_USERNAME": "__token__", "PDM_PUBLISH_PASSWORD": c["pypi"]["password"]}
   subprocess.run(["pdm", "publish", "--repository", "pypi", "--no-build"], env=env, check=True)
   PY
   ```
   `--no-build` ensures the artifact whose `sha256` you'll paste into the formula is byte-identical to what PyPI serves.
4. **Tag and create a GitHub Release**:
   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
   Then write release notes at https://github.com/Drozdetskiy/cadence/releases/new (the CHANGELOG.md entry is a good starting point; focus on user-visible changes).
5. **Update the Homebrew formula** in `homebrew-cadence/Formula/cadence.rb`:
   - Replace `url` and `sha256` with the new sdist values from `https://pypi.org/pypi/cadence-runner/X.Y.Z/json` (look for the entry where `packagetype == "sdist"`).
   - **Only if `pyproject.toml` dependencies changed**, regenerate the `resource` blocks. `brew update-python-resources` cannot see packages newer than its internal PyPI snapshot, so resolve manually:
     ```bash
     python3.14 -m venv /tmp/r && /tmp/r/bin/pip install --dry-run --report /tmp/r.json cadence-runner==X.Y.Z
     ```
     Then for each resolved dependency fetch the sdist URL/sha256 from `https://pypi.org/pypi/<name>/<version>/json` and write the `resource "<name>" do … end` block.
   - Verify locally: `brew audit --strict drozdetskiy/cadence/cadence && brew install --build-from-source drozdetskiy/cadence/cadence && brew test drozdetskiy/cadence/cadence`.
   - Commit, push.
6. **End-to-end check**: from a clean state — `brew untap drozdetskiy/cadence && brew tap drozdetskiy/cadence && brew install drozdetskiy/cadence/cadence && cadence --version`. Use the fully tap-qualified name (`drozdetskiy/cadence/cadence`) — `brew install cadence` would resolve to the homebrew-core formula of the same name (Flow smart-contract language).

PyPI versions are immutable (no re-uploads under the same `X.Y.Z`); if anything goes wrong after step 2, bump the patch version and start again.
