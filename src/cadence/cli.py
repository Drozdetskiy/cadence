from __future__ import annotations

import os
import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import typer
import yaml

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
from cadence.input import TerminalCollector, ask_yes_no
from cadence.processor.prompts import build_squash_commit_prompt
from cadence.processor.runner import (
    Dependencies,
    RunContext,
    Runner,
    UserAbortedError,
)
from cadence.processor.signals import parse_squash_commit_message
from cadence.progress.colors import Colors
from cadence.progress.logger import Logger, ProgressLoggerConfig, sanitize_plan_name
from cadence.status import Mode, PhaseHolder

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

        return version("cadence-runner")
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
    return Mode.REVIEW


def _validate_flags(
    plan: Path | None,
    task: Path | None,
    review: bool,
    impl: bool,
    base: str | None,
    task_init: str | None = None,
    run: bool = False,
    squash: bool = False,
    chain: Path | None = None,
) -> None:
    if chain is not None:
        if (
            plan is not None
            or task is not None
            or review
            or run
            or task_init is not None
            or impl
            or squash
            or base is not None
        ):
            typer.echo(
                "error: --chain is mutually exclusive with --plan, --task, --review, "
                "--run, --task-init, --impl, --squash, and --base",
                err=True,
            )
            raise SystemExit(1)
        return
    if task_init is not None:
        if squash:
            typer.echo("error: --squash is incompatible with --task-init", err=True)
            raise SystemExit(1)
        if plan is not None or task is not None or review or run:
            typer.echo(
                "error: --task-init is mutually exclusive with --plan, --task, --review, and --run",
                err=True,
            )
            raise SystemExit(1)
        if impl:
            typer.echo("error: --task-init is incompatible with --impl", err=True)
            raise SystemExit(1)
        if base is not None:
            typer.echo("error: --task-init is incompatible with --base", err=True)
            raise SystemExit(1)
        return
    if run:
        if plan is not None or task is not None or review:
            typer.echo(
                "error: --run is mutually exclusive with --plan, --task, and --review",
                err=True,
            )
            raise SystemExit(1)
        if base is not None:
            typer.echo("error: --run is incompatible with --base", err=True)
            raise SystemExit(1)
        if squash and not impl:
            typer.echo("error: --squash with --plan/--run requires --impl", err=True)
            raise SystemExit(1)
        return
    if review and squash:
        typer.echo("error: --squash is incompatible with --review", err=True)
        raise SystemExit(1)
    if review and impl:
        typer.echo("error: --review is incompatible with --impl", err=True)
        raise SystemExit(1)
    if impl and plan is None:
        typer.echo("error: --impl requires --plan", err=True)
        raise SystemExit(1)
    if base is not None and not review:
        typer.echo("error: --base is only valid with --review", err=True)
        raise SystemExit(1)
    if squash and plan is not None and not impl:
        typer.echo("error: --squash with --plan/--run requires --impl", err=True)
        raise SystemExit(1)
    active = sum([plan is not None, task is not None, review])
    if active > 1:
        typer.echo(
            "error: --plan, --task, and --review are mutually exclusive",
            err=True,
        )
        raise SystemExit(1)
    if active == 0:
        if squash:
            return
        typer.echo(
            "error: one of --plan, --task, --review, --run, --task-init, "
            "--chain, or --squash is required",
            err=True,
        )
        raise SystemExit(1)


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
        raise SystemExit(1)
    text = path.read_text(encoding="utf-8")
    names: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "/" in line or "\\" in line or line.startswith((".", "-")):
            typer.echo(f"error: invalid task name in chain file: {line}", err=True)
            raise SystemExit(1)
        names.append(line)
    if not names:
        typer.echo("error: chain file is empty", err=True)
        raise SystemExit(1)
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
        raise SystemExit(1)
    content = plan_file.read_text(encoding="utf-8").strip()
    if not content:
        typer.echo("error: plan file is empty", err=True)
        raise SystemExit(1)
    return content


def _apply_yaml_overrides(
    cfg: Config,
    config_arg: Path | None,
    anchor: Path | None,
) -> None:
    if config_arg is not None:
        if not config_arg.is_file():
            typer.echo(f"error: config file not found: {config_arg}", err=True)
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

    raise RuntimeError(f"cannot derive progress path: unsupported mode {mode}")


def _build_logger(
    progress_path: str,
    plan_file: str,
    plan_description: str,
    mode: Mode,
    branch: str,
    colors: Colors,
    holder: PhaseHolder,
) -> Logger:
    logger_cfg = ProgressLoggerConfig(
        progress_path=progress_path,
        plan_file=plan_file,
        plan_description=plan_description,
        mode=mode,
        branch=branch,
    )
    try:
        return Logger(logger_cfg, colors, holder)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None


ClaudeExecutorFactory = Callable[[Logger, str], ClaudeExecutor]


def _setup_runtime(
    config_arg: Path | None,
    anchor: Path | None,
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
        git_svc = Service(path=".", log=_StderrLogger())
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

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
        )

    return cfg, holder, colors, git_svc, factory, cfg.default_branch, local_dir


def display_stats(stats: DiffStats, elapsed: str, branch: str) -> None:
    typer.echo(
        f"branch: {branch}  elapsed: {elapsed}  "
        f"files: {stats.files}  +{stats.additions}/-{stats.deletions}"
    )


def run_plan_mode(
    plan_file: Path,
    *,
    impl: bool = False,
    squash: bool = False,
    config: Path | None = None,
) -> None:
    content = _read_plan_file(plan_file)

    cfg, holder, colors, _git_svc, factory, default_branch, local_dir = _setup_runtime(
        config, plan_file
    )

    plan_file_rel = to_rel_path(plan_file)
    try:
        progress_path = compute_progress_path(
            Mode.PLAN,
            plan_file=plan_file_rel,
            tasks_root=cfg.tasks_root,
            default_branch=default_branch,
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None
    log = _build_logger(progress_path, plan_file_rel, content, Mode.PLAN, "", colors, holder)

    log.print("cadence %s", resolve_version())
    log.print("mode: plan")
    log.print("plan file: %s", plan_file_rel)
    log.print("progress: %s", log.path)

    plan_path = derive_plan_path(plan_file, cfg.init_prompt_name)
    ctx = RunContext(
        mode=Mode.PLAN,
        plan_file=plan_file_rel,
        plan_description=content,
        progress_path=log.path,
        default_branch=default_branch,
        local_dir=local_dir,
        derived_plan_path=plan_path,
    )

    deps = Dependencies(
        executor=factory(log, cfg.plan_model),
        input_collector=TerminalCollector(),
        logger=log,
        holder=holder,
    )

    run_success = False
    try:
        runner = Runner(ctx, cfg, deps)
        run_success = runner.run()
        if run_success:
            typer.echo(f"run: cadence --task {plan_path}")
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
        run_task_mode(Path(plan_path), squash=squash, config=config)


def _install_sigquit(break_event: threading.Event) -> None:
    sigquit = getattr(signal, "SIGQUIT", None)
    if sigquit is None:
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


def run_task_mode(
    task_file: Path,
    *,
    squash: bool = False,
    config: Path | None = None,
) -> None:
    if not task_file.is_file():
        typer.echo(f"error: file not found: {task_file}", err=True)
        raise SystemExit(1)

    cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(
        config, task_file
    )

    git_svc.set_commit_trailer(cfg.commit_trailer)

    try:
        git_svc.ensure_has_commits(
            lambda: ask_yes_no("repository has no commits. create an initial commit?")
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    plan_path_str = to_rel_path(task_file)
    try:
        git_svc.create_branch_for_plan(plan_path_str, default_branch)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    branch = git_svc.current_branch()

    try:
        progress_path = compute_progress_path(
            Mode.FULL,
            plan_file=plan_path_str,
            branch=branch,
            tasks_root=cfg.tasks_root,
            default_branch=default_branch,
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None
    log = _build_logger(progress_path, plan_path_str, "", Mode.FULL, branch, colors, holder)

    log.print("cadence %s", resolve_version())
    log.print("mode: full")
    log.print("plan file: %s", plan_path_str)
    log.print("branch: %s", branch)
    log.print("progress: %s", log.path)

    git_svc.set_log(log)

    claude = factory(log, cfg.task_model)
    review_claude = factory(log, cfg.review_model) if cfg.review_model != cfg.task_model else None

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
        runner.set_git_checker(git_svc)

        run_success = runner.run()
        if run_success:
            stats = git_svc.diff_stats(default_branch)
            try:
                git_svc.mark_plan_completed(plan_path_str)
            except (RuntimeError, OSError) as exc:
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

    if squash and run_success:
        run_squash_mode(config=config)


def run_review_mode(base: str | None = None, *, config: Path | None = None) -> None:
    cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(config, None)

    git_svc.set_commit_trailer(cfg.commit_trailer)

    if base is not None:
        default_branch = base

    branch = git_svc.current_branch()
    plan_file = find_existing_plan(cfg.tasks_root, branch, default_branch)
    try:
        head_hash = git_svc.head_hash()
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    try:
        progress_path = compute_progress_path(
            Mode.REVIEW,
            branch=branch,
            tasks_root=cfg.tasks_root,
            default_branch=default_branch,
            head_hash=head_hash,
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None
    log = _build_logger(progress_path, plan_file, "", Mode.REVIEW, branch, colors, holder)

    log.print("cadence %s", resolve_version())
    log.print("mode: review")
    log.print("branch: %s", branch)
    log.print("base: %s", default_branch)
    log.print("plan: %s", plan_file if plan_file else "(none)")
    log.print("progress: %s", log.path)

    git_svc.set_log(log)

    deps = Dependencies(
        executor=factory(log, cfg.review_model),
        input_collector=TerminalCollector(),
        logger=log,
        holder=holder,
    )

    ctx = RunContext(
        mode=Mode.REVIEW,
        plan_file=plan_file,
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
        runner.set_git_checker(git_svc)

        run_success = runner.run()
        if run_success:
            stats = git_svc.diff_stats(default_branch)
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


def run_squash_mode(*, config: Path | None = None) -> None:
    cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(
        config, anchor=None
    )

    git_svc.set_commit_trailer(cfg.commit_trailer)

    branch = git_svc.current_branch()
    if not branch:
        typer.echo("error: cannot --squash from a detached HEAD", err=True)
        raise SystemExit(1)

    task_dir = Path(cfg.tasks_root) / sanitize_plan_name(branch)

    if config is None:
        per_task_yaml = find_yaml_config(task_dir)
        if per_task_yaml is not None:
            try:
                apply_yaml_overrides(cfg, load_yaml_config(per_task_yaml))
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise SystemExit(1) from None
            default_branch = cfg.default_branch

    if git_svc.is_default_branch(default_branch):
        typer.echo(f"error: cannot --squash on default branch {default_branch}", err=True)
        raise SystemExit(1)

    if not task_dir.is_dir():
        typer.echo(f"error: task directory not found: {task_dir}", err=True)
        raise SystemExit(1)

    completed = task_dir / "plan-completed"
    if not completed.is_file():
        typer.echo("error: plan not completed; squash refused", err=True)
        raise SystemExit(1)

    if git_svc.is_dirty():
        typer.echo("error: uncommitted changes present", err=True)
        raise SystemExit(1)

    ahead = git_svc.commits_ahead(default_branch)
    if ahead == 0:
        typer.echo(f"error: no commits ahead of {default_branch}", err=True)
        raise SystemExit(1)
    if ahead == 1:
        typer.echo("single commit already; nothing to squash")
        return

    try:
        progress_path = compute_progress_path(
            Mode.SQUASH,
            branch=branch,
            tasks_root=cfg.tasks_root,
            default_branch=default_branch,
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    log = _build_logger(progress_path, "", "", Mode.SQUASH, branch, colors, holder)

    log.print("cadence %s", resolve_version())
    log.print("mode: squash")
    log.print("branch: %s", branch)
    log.print("base: %s", default_branch)
    log.print("progress: %s", log.path)

    git_svc.set_log(log)

    prompt = build_squash_commit_prompt(
        local_dir=local_dir,
        default_branch=default_branch,
        commit_format=cfg.commit_format,
    )

    claude = factory(log, cfg.task_model)

    run_success = False
    try:
        result = claude.run(prompt)
        if result.idle_timed_out:
            log.error("claude idle-timed out before producing a commit message")
            raise SystemExit(1)
        if result.error is not None:
            log.error("claude error: %s", result.error)
            raise SystemExit(1)
        message = parse_squash_commit_message(result.output or "")
        if not message:
            log.error("claude did not return a commit message")
            raise SystemExit(1)
        if git_svc.is_dirty():
            log.error("working tree was modified during squash; aborting")
            raise SystemExit(1)
        try:
            git_svc.squash_commits(default_branch, message)
        except RuntimeError as exc:
            log.error("squash failed: %s", exc)
            raise SystemExit(1) from None
        stats = git_svc.diff_stats(default_branch)
        display_stats(stats, log.elapsed(), branch)
        run_success = True
    except KeyboardInterrupt:
        log.print("interrupted by user")
        return
    except Exception as exc:
        log.error("execution failed: %s", exc)
        raise SystemExit(1) from exc
    finally:
        log.close(success=run_success)


def run_task_init_mode(task_name: str, *, config: Path | None = None) -> None:
    if not task_name or "/" in task_name or "\\" in task_name or task_name.startswith((".", "-")):
        typer.echo(f"error: invalid task name: {task_name!r}", err=True)
        raise SystemExit(1)

    local_dir = detect_local_dir()
    cfg = load_config(local_dir)
    _apply_yaml_overrides(cfg, config, anchor=None)

    try:
        git_svc = Service(path=".", log=_StderrLogger())
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    try:
        git_svc.ensure_has_commits(
            lambda: ask_yes_no("repository has no commits. create an initial commit?")
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    parent_branch = git_svc.current_branch()
    if not parent_branch:
        typer.echo("error: cannot --task-init from a detached HEAD", err=True)
        raise SystemExit(1)

    task_dir = Path(cfg.tasks_root) / task_name
    if task_dir.exists():
        typer.echo(f"error: task directory already exists: {task_dir}", err=True)
        raise SystemExit(1)

    if git_svc.branch_exists(task_name):
        typer.echo(f"error: branch already exists: {task_name}", err=True)
        raise SystemExit(1)

    try:
        git_svc.create_branch(task_name)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    task_dir.mkdir(parents=True, exist_ok=False)
    init_file = task_dir / "init"
    init_file.touch()

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

    typer.echo(f"next: cadence --plan {init_file}")


def run_run_mode(
    *,
    impl: bool = False,
    squash: bool = False,
    config: Path | None = None,
) -> None:
    local_dir = detect_local_dir()
    cfg = load_config(local_dir)
    _apply_yaml_overrides(cfg, config, anchor=None)

    try:
        git_svc = Service(path=".", log=_StderrLogger())
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    branch = git_svc.current_branch()
    if not branch:
        typer.echo("error: cannot --run from a detached HEAD", err=True)
        raise SystemExit(1)

    task_dir = Path(cfg.tasks_root) / sanitize_plan_name(branch)
    if not task_dir.is_dir():
        typer.echo(f"error: task directory not found: {task_dir}", err=True)
        raise SystemExit(1)

    completed = task_dir / "plan-completed"
    if completed.is_file():
        if squash and impl:
            run_squash_mode(config=config)
            return
        typer.echo(f"plan already completed: {to_rel_path(completed)}")
        return

    plan_file = task_dir / "plan"
    if plan_file.is_file():
        if impl:
            run_task_mode(plan_file, squash=squash, config=config)
            return
        typer.echo(f"plan ready: {to_rel_path(plan_file)}")
        typer.echo(f"run: cadence --task {to_rel_path(plan_file)}")
        return

    init_file = task_dir / cfg.init_prompt_name
    if not init_file.is_file():
        typer.echo(f"error: init file not found: {init_file}", err=True)
        raise SystemExit(1)

    if not init_file.read_text(encoding="utf-8").strip():
        typer.echo(f"error: init file is empty: {init_file}", err=True)
        raise SystemExit(1)

    run_plan_mode(init_file, impl=impl, squash=squash, config=config)


def run_chain_mode(chain_file: Path, *, config: Path | None = None) -> None:
    cfg, _holder, _colors, git_svc, _factory, default_branch, _local_dir = _setup_runtime(
        config, anchor=None
    )

    git_svc.set_commit_trailer(cfg.commit_trailer)

    try:
        git_svc.ensure_has_commits(
            lambda: ask_yes_no("repository has no commits. create an initial commit?")
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    if git_svc.is_dirty():
        typer.echo("error: uncommitted changes present", err=True)
        raise SystemExit(1)
    if git_svc.current_branch() == "":
        typer.echo("error: cannot --chain from a detached HEAD", err=True)
        raise SystemExit(1)

    names = _parse_chain_file(chain_file)

    warnings = _validate_chain_tasks(cfg.tasks_root, names)
    if warnings:
        for w in warnings:
            typer.echo(f"warn: {w}", err=True)
        raise SystemExit(1)

    total = len(names)
    for i, name in enumerate(names, start=1):
        typer.echo(f"[chain {i}/{total}] {name}")
        try:
            task_default = _resolve_chain_default_branch(cfg.tasks_root, name, default_branch)
            if git_svc.branch_exists(name):
                git_svc.checkout_branch(name)
            else:
                git_svc.create_branch_from(name, task_default)
            run_run_mode(impl=True, squash=True, config=config)
        except SystemExit as exc:
            if exc.code != 0:
                typer.echo(
                    f"chain failed at task {i}/{total}: {name}",
                    err=True,
                )
            raise
        except RuntimeError as exc:
            typer.echo(f"error: {exc}", err=True)
            typer.echo(
                f"chain failed at task {i}/{total}: {name}",
                err=True,
            )
            raise SystemExit(1) from None

        if _sigint.shutdown_event.is_set():
            typer.echo(
                f"chain interrupted at task {i}/{total}: {name}",
                err=True,
            )
            raise SystemExit(1)

    typer.echo(f"chain complete: {total} task(s)")


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
    help="Path to optional config.yaml overrides (models, default_branch)",
)
_TASK_INIT_OPT: str | None = typer.Option(
    None,
    "--task-init",
    help="Scaffold a new task: branch + tasks_root/<name>/init [+ config.yaml]",
)
_RUN_OPT: bool = typer.Option(
    False,
    "--run",
    help="Run the task for the current branch (auto-detect init/plan)",
)
_SQUASH_OPT: bool = typer.Option(
    False,
    "--squash",
    help=(
        "Squash all commits on the branch into one"
        " (also a flag for --task / --plan --impl / --run --impl)"
    ),
)
_CHAIN_OPT: Path | None = typer.Option(
    None,
    "--chain",
    help="Run a sequence of tasks listed in a file (one task name per line)",
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
    task_init: str | None = _TASK_INIT_OPT,
    run: bool = _RUN_OPT,
    squash: bool = _SQUASH_OPT,
    chain: Path | None = _CHAIN_OPT,
    version: bool = _VERSION_OPT,
) -> None:
    if version:
        typer.echo(f"cadence {resolve_version()}")
        raise SystemExit(0)

    _validate_flags(plan, task, review, impl, base, task_init, run=run, squash=squash, chain=chain)

    if task_init is not None:
        run_task_init_mode(task_init, config=config)
        return

    _sigint.reset()
    _sigint.install()

    if chain is not None:
        run_chain_mode(chain, config=config)
        return

    if run:
        run_run_mode(impl=impl, squash=squash, config=config)
        return

    if squash and plan is None and task is None and not review:
        run_squash_mode(config=config)
        return

    mode = determine_mode(plan, task, review)

    if mode == Mode.PLAN:
        assert plan is not None
        run_plan_mode(plan, impl=impl, squash=squash, config=config)
    elif mode == Mode.FULL:
        assert task is not None
        run_task_mode(task, squash=squash, config=config)
    elif mode == Mode.REVIEW:
        run_review_mode(base, config=config)
