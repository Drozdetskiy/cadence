from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

import typer
import yaml

from cadence.cli_commands._shared import (
    _TASK_NAME_ARG,
    _TEMPLATE_OPTION,
    _apply_yaml_overrides,
    _ctx_opts,
    _StderrLogger,
)
from cadence.config import (
    detect_local_dir,
    load_config,
)
from cadence.git import Service
from cadence.input import ask_yes_no
from cadence.progress.logger import sanitize_plan_name
from cadence.templates import load_template, render_template


def run_task_init_mode(
    task_name: str,
    *,
    config: Path | None = None,
    template: str | None = None,
) -> None:
    if not task_name or "/" in task_name or "\\" in task_name or task_name.startswith((".", "-")):
        typer.echo(f"error: invalid task name: {task_name!r}", err=True)
        raise SystemExit(2)

    try:
        sanitize_plan_name(task_name)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        typer.echo("hint: shorten the task name", err=True)
        raise SystemExit(2) from None

    if template is not None and (
        not template or "/" in template or "\\" in template or template.startswith((".", "-"))
    ):
        typer.echo(f"error: invalid template name: {template!r}", err=True)
        raise SystemExit(2)

    local_dir = detect_local_dir()
    cfg = load_config(local_dir)
    _apply_yaml_overrides(cfg, config, anchor=None)

    try:
        git_svc = Service(path=".", log=_StderrLogger())
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    git_svc.ensure_local_ignore(cfg.tasks_root)

    try:
        git_svc.ensure_has_commits(
            lambda: ask_yes_no("repository has no commits. create an initial commit?")
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    parent_branch = git_svc.current_branch()
    if not parent_branch:
        typer.echo("error: cannot init from a detached HEAD", err=True)
        raise SystemExit(2)

    task_dir = Path(cfg.tasks_root) / task_name
    if task_dir.exists():
        typer.echo(f"error: task directory already exists: {task_dir}", err=True)
        raise SystemExit(2)

    if git_svc.branch_exists(task_name):
        typer.echo(f"error: branch already exists: {task_name}", err=True)
        raise SystemExit(2)

    init_content: str | None = None
    if template is not None:
        try:
            template_text = load_template(Path(cfg.templates_dir), template)
        except FileNotFoundError as exc:
            resolved = getattr(exc, "path", None) or (Path(cfg.templates_dir) / f"{template}.txt")
            typer.echo(
                f'error: template "{template}" not found at {resolved}',
                err=True,
            )
            raise SystemExit(2) from None
        try:
            author_proc = subprocess.run(
                ["git", "config", "user.name"],
                capture_output=True,
                text=True,
                check=False,
            )
            author = author_proc.stdout.strip() if author_proc.returncode == 0 else ""
        except OSError:
            author = ""
        context = {
            "task_name": task_name,
            "branch": task_name,
            "date": datetime.date.today().strftime("%Y-%m-%d"),
            "author": author,
        }
        init_content = render_template(template_text, context)

    try:
        git_svc.create_branch(task_name)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    task_dir.mkdir(parents=True, exist_ok=False)
    init_file = task_dir / "init"
    if init_content is None:
        init_file.touch()
    else:
        init_file.write_text(init_content, encoding="utf-8")

    typer.echo(f"created branch: {task_name}")
    typer.echo(f"created directory: {task_dir}")

    default = cfg.default_branch
    if default.startswith("origin/"):
        default = default[len("origin/") :]
    if parent_branch != default:
        config_path = task_dir / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"default_branch": parent_branch}, sort_keys=False),
            encoding="utf-8",
        )
        typer.echo(f"wrote config: {config_path}")

    typer.echo("next: cadence run")


def cmd_init(
    ctx: typer.Context,
    task_name: str = _TASK_NAME_ARG,
    template: str | None = _TEMPLATE_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    run_task_init_mode(task_name, config=opts.config, template=template)
