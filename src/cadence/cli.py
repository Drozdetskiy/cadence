from __future__ import annotations

from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _CONFIG_OPTION,
    _VERSION_OPTION,
    GlobalOpts,
)
from cadence.cli_commands.auto import cmd_run
from cadence.cli_commands.chain import cmd_chain
from cadence.cli_commands.doctor import cmd_doctor
from cadence.cli_commands.init import cmd_init
from cadence.cli_commands.plan import cmd_plan, cmd_run_plan
from cadence.cli_commands.report import (
    cmd_report_api_changes,
    cmd_report_test_cases,
)
from cadence.cli_commands.review import cmd_review
from cadence.cli_commands.squash import cmd_squash
from cadence.cli_commands.status import cmd_status
from cadence.cli_commands.task import cmd_run_task, cmd_task

app = typer.Typer(add_completion=True, no_args_is_help=True)
run_app = typer.Typer(no_args_is_help=False)
app.add_typer(run_app, name="run", help="Run plan/task on the current branch (auto-detect)")
report_app = typer.Typer(no_args_is_help=True)
app.add_typer(
    report_app,
    name="report",
    help="Generate analysis reports about the current branch",
)


@app.callback()
def app_callback(
    ctx: typer.Context,
    config: Path | None = _CONFIG_OPTION,
    version: bool = _VERSION_OPTION,
) -> None:
    _ = version
    ctx.obj = GlobalOpts(config=config)


run_app.callback(invoke_without_command=True)(cmd_run)
run_app.command("plan", help="Run plan creation on the current branch's init file")(cmd_run_plan)
run_app.command("task", help="Run task execution on the current branch's plan file")(cmd_run_task)


app.command("init", help="Scaffold a new task: branch + tasks_root/<name>/init [+ config.yaml]")(
    cmd_init
)
app.command("plan", help="Create a plan from a prompt file at <path>")(cmd_plan)
app.command("task", help="Execute tasks from a plan file at <path>")(cmd_task)
app.command("review", help="Review the current branch")(cmd_review)
app.command("squash", help="Squash all commits on the current branch into one")(cmd_squash)
app.command("status", help="Show the status of cadence tasks under tasks_root")(cmd_status)
app.command("doctor", help="Run pre-flight environment & config checks (no Claude calls)")(
    cmd_doctor
)
app.command("chain", help="Run a sequence of tasks listed in a file (one task name per line)")(
    cmd_chain
)


report_app.command(
    "api-changes",
    help="Generate an API-changes report for the current branch",
)(cmd_report_api_changes)
report_app.command(
    "test-cases",
    help="Generate a manual-QA test-case report for the current branch",
)(cmd_report_test_cases)
