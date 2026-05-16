from __future__ import annotations

from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _arm_sigint,
    _ctx_opts,
    _resolve_current_task_dir,
    to_rel_path,
)
from cadence.cli_commands.plan import run_plan_mode
from cadence.cli_commands.task import run_task_mode


def _auto_detect_and_run(*, config: Path | None) -> None:
    cfg, _branch, task_dir = _resolve_current_task_dir(config)

    completed = task_dir / "plan-completed"
    if completed.is_file():
        typer.echo(f"plan already completed: {to_rel_path(completed)}")
        return

    plan_file = task_dir / "plan"
    if plan_file.is_file():
        run_task_mode(plan_file, config=config)
        return

    init_file = task_dir / cfg.init_prompt_name
    if not init_file.is_file():
        typer.echo(f"error: init file not found: {init_file}", err=True)
        raise SystemExit(2)

    if not init_file.read_text(encoding="utf-8").strip():
        typer.echo(f"error: init file is empty: {init_file}", err=True)
        raise SystemExit(2)

    run_plan_mode(init_file, config=config)


def cmd_run(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    opts = _ctx_opts(ctx)
    _arm_sigint()
    _auto_detect_and_run(config=opts.config)
