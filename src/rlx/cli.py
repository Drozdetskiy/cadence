from __future__ import annotations

import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import typer

from rlx.config import (
    Config,
    apply_yaml_overrides,
    detect_local_dir,
    find_yaml_config,
    load_config,
    load_yaml_config,
    parse_duration,
)
from rlx.executor.claude_executor import ClaudeExecutor
from rlx.git import DiffStats, Service, get_default_branch, is_git_repo
from rlx.input import TerminalCollector, ask_yes_no
from rlx.processor.runner import (
    Dependencies,
    RunContext,
    Runner,
    UserAbortedError,
)
from rlx.progress.colors import Colors
from rlx.progress.logger import Logger, ProgressLoggerConfig
from rlx.status import Mode, PhaseHolder

app = typer.Typer(add_completion=False)


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

        return version("rlx")
    except Exception:
        return "unknown"


def determine_mode(
    plan: Path | None,
    task: Path | None,
    review: bool,
) -> Mode:
    if plan is not None:
        return Mode.PLAN
    if task is not None:
        return Mode.FULL
    if review:
        return Mode.REVIEW
    raise typer.BadParameter(
        "one of --plan, --task, or --review is required"
    )


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


def derive_plan_path(prompt_file: Path) -> str:
    name = prompt_file.name
    if "preprompt" in name:
        plan_name = name.replace("preprompt", "plan", 1)
    else:
        idx = name.rfind("prompt")
        if idx != -1:
            plan_name = name[:idx] + "plan" + name[idx + len("prompt"):]
        else:
            stem = prompt_file.stem
            plan_name = f"{stem}-plan{prompt_file.suffix}"
    return str(prompt_file.parent / plan_name)


def _read_plan_file(plan_file: Path) -> str:
    if not plan_file.is_file():
        typer.echo(f"error: file not found: {plan_file}", err=True)
        raise SystemExit(1)
    content = plan_file.read_text(encoding="utf-8").strip()
    if not content:
        typer.echo("error: plan file is empty", err=True)
        raise SystemExit(1)
    return content


def _ensure_git_repo(vcs_command: str) -> None:
    if not is_git_repo(vcs_command=vcs_command):
        typer.echo(
            "error: not a git repository (or not at repo root)",
            err=True,
        )
        raise SystemExit(1)


def _apply_yaml_overrides(
    cfg: Config,
    config_arg: Path | None,
    anchor: Path | None,
) -> None:
    if config_arg is not None:
        if not config_arg.is_file():
            typer.echo(
                f"error: config file not found: {config_arg}", err=True
            )
            raise SystemExit(1)
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


def _build_plan_logger(
    plan_file: Path, content: str, colors: Colors, holder: PhaseHolder
) -> Logger:
    logger_cfg = ProgressLoggerConfig(
        plan_file=to_rel_path(plan_file),
        plan_description=content,
        mode=Mode.PLAN,
        branch="",
    )
    try:
        return Logger(logger_cfg, colors, holder)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None


def _build_task_logger(
    plan_file: Path, colors: Colors, holder: PhaseHolder, branch: str
) -> Logger:
    logger_cfg = ProgressLoggerConfig(
        plan_file=to_rel_path(plan_file),
        plan_description="",
        mode=Mode.FULL,
        branch=branch,
    )
    try:
        return Logger(logger_cfg, colors, holder)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None


def _build_review_logger(
    colors: Colors, holder: PhaseHolder, branch: str
) -> Logger:
    logger_cfg = ProgressLoggerConfig(
        plan_file="",
        plan_description="",
        mode=Mode.REVIEW,
        branch=branch,
    )
    try:
        return Logger(logger_cfg, colors, holder)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None


def _build_review_executor(
    cfg: Config,
    activity_handler: Callable[[str], None],
    output_handler: Callable[[str], None],
    idle_timeout: float,
) -> ClaudeExecutor | None:
    if cfg.review_model == cfg.task_model:
        return None
    return ClaudeExecutor(
        command=cfg.claude_command,
        args=cfg.claude_args,
        model=cfg.review_model,
        error_patterns=cfg.claude_error_patterns,
        limit_patterns=cfg.claude_limit_patterns,
        idle_timeout=idle_timeout,
        activity_handler=activity_handler,
        output_handler=output_handler,
    )


def display_stats(stats: DiffStats, elapsed: str, branch: str) -> None:
    typer.echo(
        f"branch: {branch}  elapsed: {elapsed}  "
        f"files: {stats.files}  +{stats.additions}/-{stats.deletions}"
    )


def run_plan_mode(
    plan_file: Path, *, impl: bool = False, config: Path | None = None
) -> None:
    content = _read_plan_file(plan_file)

    local_dir = detect_local_dir()
    cfg = load_config(local_dir)
    _apply_yaml_overrides(cfg, config, plan_file)

    check_claude_dep(cfg)

    vcs = cfg.vcs_command or "git"
    _ensure_git_repo(vcs)

    default_branch = cfg.default_branch or get_default_branch(vcs_command=vcs)

    holder = PhaseHolder()
    colors = Colors(cfg.colors)
    log = _build_plan_logger(plan_file, content, colors, holder)

    log.print("rlx %s", resolve_version())
    log.print("mode: plan")
    log.print("plan file: %s", to_rel_path(plan_file))
    log.print("progress: %s", log.path)

    plan_path = derive_plan_path(plan_file)
    ctx = RunContext(
        mode=Mode.PLAN,
        plan_file=to_rel_path(plan_file),
        plan_description=content,
        progress_path=log.path,
        default_branch=default_branch,
        local_dir=local_dir,
        derived_plan_path=plan_path,
    )

    idle_timeout = parse_duration(cfg.idle_timeout)

    def activity_handler(tool_name: str) -> None:
        log.print("claude: %s", tool_name)

    def output_handler(text: str) -> None:
        log.log_claude_output(text)

    claude = ClaudeExecutor(
        command=cfg.claude_command,
        args=cfg.claude_args,
        model=cfg.plan_model,
        error_patterns=cfg.claude_error_patterns,
        limit_patterns=cfg.claude_limit_patterns,
        idle_timeout=idle_timeout,
        activity_handler=activity_handler,
        output_handler=output_handler,
    )

    deps = Dependencies(
        executor=claude,
        input_collector=TerminalCollector(),
        logger=log,
        holder=holder,
    )

    run_success = False
    try:
        runner = Runner(ctx, cfg, deps)
        run_success = runner.run()
        if run_success:
            typer.echo(f"run: rlx --task {plan_path}")
    except KeyboardInterrupt:
        log.print("interrupted by user")
        return
    except UserAbortedError:
        log.print("aborted by user")
        return
    except Exception as exc:
        log.error("execution failed: %s", exc)
        raise SystemExit(1) from exc
    finally:
        log.close(success=run_success)

    if impl and run_success:
        run_task_mode(Path(plan_path), config=config)


def _install_sigquit(break_event: threading.Event) -> None:
    sigquit = getattr(signal, "SIGQUIT", None)
    if sigquit is None:
        return

    def handler(signum: int, frame: object) -> None:
        break_event.set()

    signal.signal(sigquit, handler)


def _make_pause_handler(log: Logger) -> Callable[[], bool]:
    def pause() -> bool:
        log.print(
            "session interrupted. press Enter to continue, Ctrl+C to abort"
        )
        try:
            sys.stdin.readline()
        except KeyboardInterrupt:
            return False
        except (EOFError, OSError):
            return False
        return True

    return pause


def run_task_mode(task_file: Path, *, config: Path | None = None) -> None:
    if not task_file.is_file():
        typer.echo(f"error: file not found: {task_file}", err=True)
        raise SystemExit(1)

    local_dir = detect_local_dir()
    cfg = load_config(local_dir)
    _apply_yaml_overrides(cfg, config, task_file)

    check_claude_dep(cfg)

    vcs = cfg.vcs_command or "git"
    _ensure_git_repo(vcs)

    holder = PhaseHolder()
    colors = Colors(cfg.colors)

    try:
        git_svc = Service(path=".", log=_StderrLogger(), command=vcs)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    git_svc.set_commit_trailer(cfg.commit_trailer)

    try:
        git_svc.ensure_has_commits(
            lambda: ask_yes_no(
                "repository has no commits. create an initial commit?"
            )
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    default_branch = cfg.default_branch or git_svc.get_default_branch()

    plan_path_str = to_rel_path(task_file)
    try:
        git_svc.create_branch_for_plan(plan_path_str, default_branch)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    branch = git_svc.current_branch()

    log = _build_task_logger(task_file, colors, holder, branch)

    log.print("rlx %s", resolve_version())
    log.print("mode: full")
    log.print("plan file: %s", plan_path_str)
    log.print("branch: %s", branch)
    log.print("progress: %s", log.path)

    git_svc_for_log = Service(path=".", log=log, command=vcs)
    git_svc_for_log.set_commit_trailer(cfg.commit_trailer)

    idle_timeout = parse_duration(cfg.idle_timeout)

    def activity_handler(tool_name: str) -> None:
        log.print("claude: %s", tool_name)

    def output_handler(text: str) -> None:
        log.log_claude_output(text)

    claude = ClaudeExecutor(
        command=cfg.claude_command,
        args=cfg.claude_args,
        model=cfg.task_model,
        error_patterns=cfg.claude_error_patterns,
        limit_patterns=cfg.claude_limit_patterns,
        idle_timeout=idle_timeout,
        activity_handler=activity_handler,
        output_handler=output_handler,
    )
    review_claude = _build_review_executor(
        cfg, activity_handler, output_handler, idle_timeout
    )

    deps = Dependencies(
        executor=claude,
        input_collector=TerminalCollector(),
        logger=log,
        holder=holder,
        review_executor=review_claude,
    )

    ctx = RunContext(
        mode=Mode.FULL,
        plan_file=plan_path_str,
        plan_description="",
        progress_path=log.path,
        default_branch=default_branch,
        local_dir=local_dir,
    )

    break_event = threading.Event()
    _install_sigquit(break_event)

    run_success = False
    try:
        runner = Runner(ctx, cfg, deps)
        runner.set_break_event(break_event)
        runner.set_pause_handler(_make_pause_handler(log))
        runner.set_git_checker(git_svc_for_log)

        run_success = runner.run()
        if run_success:
            stats = git_svc_for_log.diff_stats(default_branch)
            try:
                git_svc_for_log.mark_plan_completed(plan_path_str)
            except (RuntimeError, FileNotFoundError) as exc:
                log.warn("could not mark plan completed: %s", exc)
            display_stats(stats, log.elapsed(), branch)
    except KeyboardInterrupt:
        log.print("interrupted by user")
        return
    except UserAbortedError:
        log.print("aborted by user")
        return
    except Exception as exc:
        log.error("execution failed: %s", exc)
        raise SystemExit(1) from exc
    finally:
        log.close(success=run_success)


def run_review_mode(
    base: str | None = None, *, config: Path | None = None
) -> None:
    local_dir = detect_local_dir()
    cfg = load_config(local_dir)
    _apply_yaml_overrides(cfg, config, None)

    check_claude_dep(cfg)

    vcs = cfg.vcs_command or "git"
    _ensure_git_repo(vcs)

    holder = PhaseHolder()
    colors = Colors(cfg.colors)

    try:
        git_svc = Service(path=".", log=_StderrLogger(), command=vcs)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    git_svc.set_commit_trailer(cfg.commit_trailer)

    default_branch = base or cfg.default_branch or git_svc.get_default_branch()
    branch = git_svc.current_branch()

    log = _build_review_logger(colors, holder, branch)

    log.print("rlx %s", resolve_version())
    log.print("mode: review")
    log.print("branch: %s", branch)
    log.print("base: %s", default_branch)
    log.print("progress: %s", log.path)

    git_svc_for_log = Service(path=".", log=log, command=vcs)
    git_svc_for_log.set_commit_trailer(cfg.commit_trailer)

    idle_timeout = parse_duration(cfg.idle_timeout)

    def activity_handler(tool_name: str) -> None:
        log.print("claude: %s", tool_name)

    def output_handler(text: str) -> None:
        log.log_claude_output(text)

    claude = ClaudeExecutor(
        command=cfg.claude_command,
        args=cfg.claude_args,
        model=cfg.review_model,
        error_patterns=cfg.claude_error_patterns,
        limit_patterns=cfg.claude_limit_patterns,
        idle_timeout=idle_timeout,
        activity_handler=activity_handler,
        output_handler=output_handler,
    )

    deps = Dependencies(
        executor=claude,
        input_collector=TerminalCollector(),
        logger=log,
        holder=holder,
        review_executor=None,
    )

    ctx = RunContext(
        mode=Mode.REVIEW,
        plan_file="",
        plan_description="",
        progress_path=log.path,
        default_branch=default_branch,
        local_dir=local_dir,
    )

    break_event = threading.Event()
    _install_sigquit(break_event)

    run_success = False
    try:
        runner = Runner(ctx, cfg, deps)
        runner.set_break_event(break_event)
        runner.set_pause_handler(_make_pause_handler(log))
        runner.set_git_checker(git_svc_for_log)

        run_success = runner.run()
        if run_success:
            stats = git_svc_for_log.diff_stats(default_branch)
            display_stats(stats, log.elapsed(), branch)
    except KeyboardInterrupt:
        log.print("interrupted by user")
        return
    except UserAbortedError:
        log.print("aborted by user")
        return
    except Exception as exc:
        log.error("execution failed: %s", exc)
        raise SystemExit(1) from exc
    finally:
        log.close(success=run_success)


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


_PLAN_OPT: Path | None = typer.Option(None, "--plan", help="Path to plan description file")
_TASK_OPT: Path | None = typer.Option(None, "--task", help="Path to plan file for task execution")
_REVIEW_OPT: bool = typer.Option(False, "--review", help="Review current branch only")
_IMPL_OPT: bool = typer.Option(False, "--impl", help="Auto-implement after plan creation")
_BASE_OPT: str | None = typer.Option(
    None,
    "--base",
    help="Base branch for review diff (overrides config default_branch)",
)
_CONFIG_OPT: Path | None = typer.Option(
    None,
    "--config",
    help="Path to optional rlx-config.yaml model overrides",
)
_VERSION_OPT: bool = typer.Option(False, "--version", help="Print version and exit")


@app.command()
def main(
    plan: Path | None = _PLAN_OPT,
    task: Path | None = _TASK_OPT,
    review: bool = _REVIEW_OPT,
    impl: bool = _IMPL_OPT,
    base: str | None = _BASE_OPT,
    config: Path | None = _CONFIG_OPT,
    version: bool = _VERSION_OPT,
) -> None:
    if version:
        typer.echo(f"rlx {resolve_version()}")
        raise SystemExit(0)

    if review and impl:
        typer.echo(
            "error: --review is incompatible with --impl",
            err=True,
        )
        raise SystemExit(1)

    if impl and plan is None:
        typer.echo(
            "error: --impl requires --plan",
            err=True,
        )
        raise SystemExit(1)

    if base is not None and not review:
        typer.echo(
            "error: --base is only valid with --review",
            err=True,
        )
        raise SystemExit(1)

    _sigint.reset()
    _sigint.install()

    active = sum([plan is not None, task is not None, review])
    if active > 1:
        typer.echo(
            "error: --plan, --task, and --review are mutually exclusive",
            err=True,
        )
        raise SystemExit(1)

    mode = determine_mode(plan, task, review)

    if mode == Mode.PLAN:
        assert plan is not None
        run_plan_mode(plan, impl=impl, config=config)
    elif mode == Mode.FULL:
        assert task is not None
        run_task_mode(task, config=config)
    elif mode == Mode.REVIEW:
        run_review_mode(base, config=config)
