from __future__ import annotations

import sys
from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _CURRENT_OPTION,
    _JSON_OPTION,
    _apply_yaml_overrides,
    _ctx_opts,
    _sanitize_branch_or_die,
    _StderrLogger,
)
from cadence.config import (
    detect_local_dir,
    load_config,
)
from cadence.diagnostics.status import (
    STATE_EMPTY,
    TaskState,
    collect_task_states,
    format_status_json,
    format_status_text,
    get_task_state,
    query_last_external_commit,
    sort_other_tasks,
)
from cadence.git import Service
from cadence.progress.logger import sanitize_plan_name


def run_status_mode(
    *,
    current_only: bool,
    json_output: bool,
    config: Path | None = None,
) -> None:
    local_dir = detect_local_dir()
    cfg = load_config(local_dir)
    _apply_yaml_overrides(cfg, config, anchor=None)

    try:
        git_svc = Service(path=".", log=_StderrLogger())
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(2) from None

    branch = git_svc.current_branch()
    if branch:
        _sanitize_branch_or_die(branch, tasks_root=cfg.tasks_root)
    tasks_root_path = Path(cfg.tasks_root)
    threshold_seconds = cfg.running_threshold_minutes * 60

    if branch:
        current_state = get_task_state(
            tasks_root_path,
            sanitize_plan_name(branch),
            running_threshold_seconds=threshold_seconds,
        )
    else:
        current_state = None

    if current_state is not None and current_state.state != STATE_EMPTY:
        last_commit = query_last_external_commit(git_svc.root(), tasks_root=cfg.tasks_root)
    else:
        last_commit = None

    if current_only:
        others: list[TaskState] = []
    else:
        all_states = collect_task_states(
            tasks_root_path,
            running_threshold_seconds=threshold_seconds,
        )
        if branch:
            current_name = sanitize_plan_name(branch)
            all_states = [t for t in all_states if t.name != current_name]
        others = sort_other_tasks(all_states)

    if json_output:
        typer.echo(
            format_status_json(
                current=current_state,
                current_branch=branch,
                last_commit=last_commit,
                tasks=others,
                tasks_root=cfg.tasks_root,
            )
        )
        return

    typer.echo(
        format_status_text(
            current=current_state,
            current_branch=branch,
            tasks_root=cfg.tasks_root,
            last_commit=last_commit,
            others=others,
            no_color=not sys.stdout.isatty(),
            only_current=current_only,
        ),
        nl=False,
    )


def cmd_status(
    ctx: typer.Context,
    current: bool = _CURRENT_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    run_status_mode(current_only=current, json_output=json_output, config=opts.config)
