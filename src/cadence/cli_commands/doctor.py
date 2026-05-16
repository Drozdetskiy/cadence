from __future__ import annotations

import sys
from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _apply_yaml_overrides,
    _ctx_opts,
)
from cadence.config import (
    Config,
    detect_local_dir,
    load_config,
)
from cadence.diagnostics.doctor import render as render_doctor
from cadence.diagnostics.doctor import run_doctor


def run_doctor_mode(*, config: Path | None = None) -> None:
    local_dir = detect_local_dir()
    try:
        cfg = load_config(local_dir)
    except ValueError:
        cfg = Config()
    _apply_yaml_overrides(cfg, config, anchor=None)

    results, exit_code = run_doctor(cfg=cfg, local_dir=local_dir)
    typer.echo(render_doctor(results, no_color=not sys.stdout.isatty()), nl=False)
    if exit_code != 0:
        raise SystemExit(exit_code)


def cmd_doctor(ctx: typer.Context) -> None:
    opts = _ctx_opts(ctx)
    run_doctor_mode(config=opts.config)
