from __future__ import annotations

import os
import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import typer

from cadence.config import (
    Config,
    apply_yaml_overrides,
    detect_local_dir,
    find_yaml_config,
    load_config,
    load_yaml_config,
    parse_duration,
)
from cadence.executor.claude_executor import ClaudeExecutor
from cadence.git import DiffStats, Service
from cadence.hooks import run_hook
from cadence.progress.colors import Colors
from cadence.progress.logger import (
    MAX_TASK_NAME_LEN,
    Logger,
    ProgressLoggerConfig,
    sanitize_plan_name,
)
from cadence.status import Mode, PhaseHolder


@dataclass(frozen=True)
class GlobalOpts:
    config: Path | None = None


class SigintHandler:
    def __init__(self) -> None:
        self.shutdown_event = threading.Event()
        self._last_time = 0.0

    def reset(self) -> None:
        self.shutdown_event.clear()
        self._last_time = 0.0

    def install(self) -> None:
        signal.signal(signal.SIGINT, self)

    def __call__(self, signum: int, frame: object) -> None:
        now = time.monotonic()
        if self.shutdown_event.is_set() and (now - self._last_time) < 5.0:
            sys.exit(1)
        self._last_time = now
        self.shutdown_event.set()
        raise KeyboardInterrupt


_sigint = SigintHandler()


def resolve_version() -> str:
    try:
        from importlib.metadata import version

        return version("cadence-runner")
    except Exception:
        return "unknown"


def check_claude_dep(cfg: Config) -> None:
    cmd = cfg.claude_command or "claude"
    if shutil.which(cmd) is None:
        typer.echo(f"error: '{cmd}' not found in PATH", err=True)
        raise SystemExit(1)


def to_rel_path(p: Path) -> str:
    try:
        return str(p.relative_to(Path.cwd()))
    except ValueError:
        return str(p)


def derive_plan_path(prompt_file: Path, init_prompt_name: str = "init") -> str:
    name = prompt_file.name
    if init_prompt_name in name:
        plan_name = name.replace(init_prompt_name, "plan", 1)
    else:
        idx = name.rfind("prompt")
        if idx != -1:
            plan_name = name[:idx] + "plan" + name[idx + len("prompt") :]
        else:
            stem = prompt_file.stem
            plan_name = f"{stem}-plan{prompt_file.suffix}"
    return str(prompt_file.parent / plan_name)


def _parse_chain_file(path: Path) -> list[str]:
    if not path.is_file():
        typer.echo(f"error: file not found: {path}", err=True)
        raise SystemExit(2)
    text = path.read_text(encoding="utf-8")
    names: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "/" in line or "\\" in line or line.startswith((".", "-")):
            typer.echo(f"error: invalid task name in chain file: {line}", err=True)
            raise SystemExit(2)
        try:
            sanitize_plan_name(line)
        except ValueError:
            typer.echo(
                f"error: task name too long in chain file: {line} "
                f"({len(line)} chars, max {MAX_TASK_NAME_LEN})",
                err=True,
            )
            raise SystemExit(2) from None
        names.append(line)
    if not names:
        typer.echo("error: chain file is empty", err=True)
        raise SystemExit(2)
    seen: set[str] = set()
    duplicates: list[str] = []
    for n in names:
        if n in seen and n not in duplicates:
            duplicates.append(n)
        seen.add(n)
    if duplicates:
        typer.echo(
            f"error: duplicate task names in chain file: {', '.join(duplicates)}",
            err=True,
        )
        raise SystemExit(2)
    return names


def _validate_chain_tasks(tasks_root: str, names: list[str]) -> list[str]:
    warnings: list[str] = []
    for name in names:
        task_dir = Path(tasks_root) / name
        if not task_dir.is_dir():
            warnings.append(f"task directory not found: {task_dir}")
            continue
        init_file = task_dir / "init"
        if not init_file.is_file():
            warnings.append(f"init file not found: {init_file}")
    return warnings


def _echo_dirty_status(lines: list[str], header: str | None = None) -> None:
    typer.echo(header or "error: uncommitted changes present:", err=True)
    for line in lines:
        typer.echo(f"  {line}", err=True)


def _resolve_chain_default_branch(tasks_root: str, name: str, global_default: str) -> str:
    task_dir = Path(tasks_root) / name
    yaml_path = find_yaml_config(task_dir)
    if yaml_path is None:
        return global_default
    try:
        overrides = load_yaml_config(yaml_path)
    except ValueError as exc:
        typer.echo(f"error: invalid config.yaml for task {name}: {exc}", err=True)
        raise SystemExit(1) from None
    if overrides.default_branch is not None:
        return overrides.default_branch
    return global_default


def _read_plan_file(plan_file: Path) -> str:
    if not plan_file.is_file():
        typer.echo(f"error: file not found: {plan_file}", err=True)
        raise SystemExit(2)
    content = plan_file.read_text(encoding="utf-8").strip()
    if not content:
        typer.echo("error: plan file is empty", err=True)
        raise SystemExit(2)
    return content


def _read_import_file(path: Path, max_bytes: int) -> tuple[str, str]:
    if not path.is_file():
        typer.echo(f"error: import file not found: {path}", err=True)
        raise SystemExit(2)
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        typer.echo(
            f"error: import file too large ({len(raw)} bytes > {max_bytes} limit); "
            f"either trim or split",
            err=True,
        )
        raise SystemExit(2)
    content = raw.decode("utf-8", errors="replace")
    return content, str(path.resolve())


def _apply_yaml_overrides(
    cfg: Config,
    config_arg: Path | None,
    anchor: Path | None,
) -> None:
    if config_arg is not None:
        if not config_arg.is_file():
            typer.echo(f"error: config file not found: {config_arg}", err=True)
            raise SystemExit(2)
        yaml_path: Path | None = config_arg
    elif anchor is not None:
        yaml_path = find_yaml_config(anchor.parent)
    else:
        yaml_path = None

    if yaml_path is None:
        return

    try:
        overrides = load_yaml_config(yaml_path)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    apply_yaml_overrides(cfg, overrides)


def _is_feature_branch(branch: str, default_branch: str) -> bool:
    return bool(branch) and branch != default_branch.removeprefix("origin/")


def find_existing_plan(tasks_root: str, branch: str, default_branch: str) -> str:
    if not _is_feature_branch(branch, default_branch):
        return ""
    base_dir = Path(tasks_root) / sanitize_plan_name(branch)
    plan_path = base_dir / "plan"
    if plan_path.is_file():
        return to_rel_path(plan_path)
    completed = base_dir / "plan-completed"
    if completed.is_file():
        return to_rel_path(completed)
    return ""


def compute_progress_path(
    mode: Mode,
    *,
    plan_file: str = "",
    branch: str = "",
    default_branch: str = "",
    head_hash: str = "",
    tasks_root: str = "cdc-tasks",
    report_type: str = "",
) -> str:
    if mode == Mode.PLAN:
        if not plan_file:
            raise RuntimeError("cannot derive progress path: plan mode requires a plan file")
        directory = os.path.dirname(plan_file) or "."
        return os.path.join(directory, "progress-plan.txt")

    if mode == Mode.FULL:
        if not plan_file:
            raise RuntimeError("cannot derive progress path: task mode requires a plan file")
        directory = os.path.dirname(plan_file) or "."
        return os.path.join(directory, "progress-task.txt")

    if mode == Mode.REVIEW:
        if _is_feature_branch(branch, default_branch):
            segment = sanitize_plan_name(branch)
        elif head_hash:
            segment = head_hash[:12]
        else:
            raise RuntimeError("cannot derive progress path: no branch and no head hash")
        return os.path.join(tasks_root, segment, "progress-review.txt")

    if mode == Mode.SQUASH:
        if not _is_feature_branch(branch, default_branch):
            raise RuntimeError("cannot derive progress path: squash mode requires a feature branch")
        segment = sanitize_plan_name(branch)
        return os.path.join(tasks_root, segment, "progress-squash.txt")

    if mode == Mode.REPORT:
        if not report_type:
            raise RuntimeError("cannot derive progress path: report mode requires report_type")
        if not branch:
            raise RuntimeError("cannot derive progress path: report mode requires a branch")
        segment = sanitize_plan_name(branch)
        return os.path.join(tasks_root, segment, f"progress-report-{report_type}.txt")

    raise RuntimeError(f"cannot derive progress path: unsupported mode {mode}")


def compute_report_path(
    report_type: str,
    *,
    branch: str,
    tasks_root: str = "cdc-tasks",
) -> str:
    if not branch:
        raise RuntimeError("cannot derive report path: missing branch")
    if not report_type:
        raise RuntimeError("cannot derive report path: missing report_type")
    return os.path.join(tasks_root, sanitize_plan_name(branch), f"report-{report_type}.md")


def _build_logger(
    progress_path: str,
    plan_file: str,
    plan_description: str,
    mode: Mode,
    branch: str,
    colors: Colors,
    holder: PhaseHolder,
    *,
    quiet: bool = False,
    progress_jsonl: bool = False,
) -> Logger:
    logger_cfg = ProgressLoggerConfig(
        progress_path=progress_path,
        plan_file=plan_file,
        plan_description=plan_description,
        mode=mode,
        branch=branch,
        quiet=quiet,
        progress_jsonl=progress_jsonl,
    )
    try:
        return Logger(logger_cfg, colors, holder)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None


ClaudeExecutorFactory = Callable[[Logger, str], ClaudeExecutor]


class _StderrLogger:
    def print(self, fmt: str, *args: object) -> None:
        msg = fmt % args if args else fmt
        typer.echo(msg)

    def warn(self, fmt: str, *args: object) -> None:
        msg = fmt % args if args else fmt
        typer.echo(f"warn: {msg}", err=True)

    def error(self, fmt: str, *args: object) -> None:
        msg = fmt % args if args else fmt
        typer.echo(f"error: {msg}", err=True)


def _setup_runtime(
    config_arg: Path | None,
    anchor: Path | None,
    *,
    repo_path: str = ".",
    claude_cwd: str | None = None,
) -> tuple[
    Config,
    PhaseHolder,
    Colors,
    Service,
    ClaudeExecutorFactory,
    str,
    Path | None,
]:
    local_dir = detect_local_dir()
    cfg = load_config(local_dir)
    _apply_yaml_overrides(cfg, config_arg, anchor)

    check_claude_dep(cfg)

    try:
        git_svc = Service(path=repo_path, log=_StderrLogger())
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    git_svc.ensure_local_ignore(cfg.tasks_root)

    holder = PhaseHolder()
    colors = Colors(cfg.colors)
    idle_timeout = parse_duration(cfg.idle_timeout)

    def factory(log: Logger, model: str) -> ClaudeExecutor:
        def activity_handler(tool_name: str) -> None:
            log.print("claude: %s", tool_name)

        def output_handler(text: str) -> None:
            log.log_claude_output(text)

        return ClaudeExecutor(
            command=cfg.claude_command,
            args=cfg.claude_args,
            model=model,
            error_patterns=cfg.claude_error_patterns,
            limit_patterns=cfg.claude_limit_patterns,
            idle_timeout=idle_timeout,
            activity_handler=activity_handler,
            output_handler=output_handler,
            cwd=claude_cwd,
        )

    return cfg, holder, colors, git_svc, factory, cfg.default_branch, local_dir


def _build_hook_env(
    phase: str,
    *,
    branch: str,
    tasks_root: str,
    task_name: str = "",
    report_type: str = "",
) -> dict[str, str]:
    return {
        "CADENCE_PHASE": phase,
        "CADENCE_BRANCH": branch,
        "CADENCE_TASK_NAME": task_name,
        "CADENCE_TASKS_ROOT": os.path.abspath(tasks_root),
        "CADENCE_REPORT_TYPE": report_type,
    }


def _invoke_pre_hook(
    phase: str,
    *,
    cfg: Config,
    repo_root: str,
    env: dict[str, str],
    logger: Logger,
) -> None:
    hook_env = {**env, "CADENCE_HOOK": "pre"}
    outcome = run_hook(
        phase=phase,
        kind="pre",
        hooks_dir=cfg.hooks_dir,
        enabled=cfg.hooks_enabled,
        env=hook_env,
        cwd=repo_root,
        logger=logger,
        timeout=cfg.hooks_timeout_seconds,
    )
    if outcome.failed:
        raise SystemExit(outcome.exit_code)


def _invoke_post_hook(
    phase: str,
    *,
    cfg: Config,
    repo_root: str,
    env: dict[str, str],
    logger: Logger,
    success: bool,
    duration_ms: int,
) -> None:
    hook_env = {
        **env,
        "CADENCE_HOOK": "post",
        "CADENCE_PHASE_RESULT": "success" if success else "failure",
        "CADENCE_PHASE_DURATION_MS": str(duration_ms),
    }
    outcome = run_hook(
        phase=phase,
        kind="post",
        hooks_dir=cfg.hooks_dir,
        enabled=cfg.hooks_enabled,
        env=hook_env,
        cwd=repo_root,
        logger=logger,
        timeout=cfg.hooks_timeout_seconds,
    )
    if outcome.failed:
        logger.warn("post-%s hook exited %d", phase, outcome.exit_code)


def display_stats(stats: DiffStats, elapsed: str, branch: str) -> None:
    typer.echo(
        f"branch: {branch}  elapsed: {elapsed}  "
        f"files: {stats.files}  +{stats.additions}/-{stats.deletions}"
    )


def _install_sigquit(break_event: threading.Event) -> None:
    sigquit = getattr(signal, "SIGQUIT", None)
    if sigquit is None:
        return
    if threading.current_thread() is not threading.main_thread():
        return

    def handler(signum: int, frame: object) -> None:
        break_event.set()

    signal.signal(sigquit, handler)


def _make_pause_handler(log: Logger) -> Callable[[], bool]:
    def pause() -> bool:
        log.print("session interrupted. press Enter to continue, Ctrl+C to abort")
        try:
            sys.stdin.readline()
        except KeyboardInterrupt:
            return False
        except EOFError, OSError:
            return False
        return True

    return pause


def _sanitize_branch_or_die(branch: str, *, tasks_root: str) -> str:
    try:
        return sanitize_plan_name(branch)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        typer.echo(
            f"hint: branch name '{branch}' is too long for cadence's path lookup.",
            err=True,
        )
        typer.echo("  rename branch and task directory, e.g.:", err=True)
        typer.echo("    git branch -m <shorter-name>", err=True)
        typer.echo(f"    mv {tasks_root}/<old> {tasks_root}/<shorter-name>", err=True)
        raise SystemExit(2) from None


def _resolve_current_task_dir(
    config: Path | None,
    *,
    repo_path: str | None = None,
) -> tuple[Config, str, Path]:
    local_dir = detect_local_dir()
    cfg = load_config(local_dir)
    _apply_yaml_overrides(cfg, config, anchor=None)

    try:
        git_svc = Service(path=repo_path or ".", log=_StderrLogger())
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    branch = git_svc.current_branch()
    if not branch:
        typer.echo("error: cannot run from a detached HEAD", err=True)
        raise SystemExit(2)

    default_stripped = cfg.default_branch.removeprefix("origin/")
    if branch == default_stripped:
        typer.echo(f"error: cannot run on default branch {default_stripped}", err=True)
        raise SystemExit(2)

    task_dir = Path(cfg.tasks_root) / _sanitize_branch_or_die(branch, tasks_root=cfg.tasks_root)
    if not task_dir.is_dir():
        typer.echo(f"error: task directory not found: {task_dir}", err=True)
        raise SystemExit(2)

    return cfg, branch, task_dir


def _ctx_opts(ctx: typer.Context) -> GlobalOpts:
    obj = ctx.obj
    if isinstance(obj, GlobalOpts):
        return obj
    return GlobalOpts()


def _arm_sigint() -> None:
    _sigint.reset()
    _sigint.install()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cadence {resolve_version()}")
        raise typer.Exit(0)


_CONFIG_OPTION: Path | None = typer.Option(
    None,
    "--config",
    help="Path to optional config.yaml overrides (models, default_branch)",
)
_VERSION_OPTION: bool = typer.Option(
    False,
    "--version",
    callback=_version_callback,
    is_eager=True,
    help="Print version and exit",
)
_BASE_OPTION: str | None = typer.Option(
    None,
    "--base",
    help="Base branch for review diff (overrides config default_branch)",
)
_STDOUT_ONLY_OPTION: bool = typer.Option(
    False,
    "--stdout-only",
    help="Print the report to stdout only; do not write the report file",
)
_TASK_NAME_ARG: str = typer.Argument(...)
_PATH_ARG: Path = typer.Argument(...)
_IMPORT_FLAG: bool = typer.Option(
    False,
    "--import",
    help="Treat <path> as an external brief to import",
)
_IMPORT_PATH_OPTION: Path | None = typer.Option(
    None,
    "--import",
    help="Path to an external brief to fold into the plan prompt",
)
_TEMPLATE_OPTION: str | None = typer.Option(
    None,
    "--template",
    help="Pre-fill init from .cadence/templates/<name>.txt",
)
_PARALLEL_OPTION: int = typer.Option(
    1,
    "--parallel",
    help="Run up to N tasks concurrently in isolated git worktrees (default 1 = sequential)",
)
_CURRENT_OPTION: bool = typer.Option(
    False,
    "--current",
    help="Show only the current branch's task",
)
_JSON_OPTION: bool = typer.Option(
    False,
    "--json",
    help="Emit machine-readable JSON",
)
