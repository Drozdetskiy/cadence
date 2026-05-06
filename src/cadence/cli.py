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
from cadence.diagnostics.doctor import render as render_doctor
from cadence.diagnostics.doctor import run_doctor
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
from cadence.executor.claude_executor import ClaudeExecutor
from cadence.git import DiffStats, Service
from cadence.hooks import run_hook
from cadence.input import ParallelAbortCollector, TerminalCollector, ask_yes_no
from cadence.processor.prompts import build_squash_commit_prompt
from cadence.processor.reporter import run_report
from cadence.processor.runner import (
    Dependencies,
    InputCollector,
    RunContext,
    Runner,
    UserAbortedError,
)
from cadence.processor.signals import parse_squash_commit_message
from cadence.progress.colors import Colors
from cadence.progress.logger import Logger, ProgressLoggerConfig, sanitize_plan_name
from cadence.status import Mode, PhaseHolder
from cadence.usage import (
    UsageStats,
    estimate_cost,
    format_chain_summary,
    format_phase_summary,
)


@dataclass(frozen=True)
class GlobalOpts:
    config: Path | None = None


app = typer.Typer(add_completion=True, no_args_is_help=True)
run_app = typer.Typer(no_args_is_help=False)
app.add_typer(run_app, name="run", help="Run plan/task on the current branch (auto-detect)")
report_app = typer.Typer(no_args_is_help=True)
app.add_typer(
    report_app,
    name="report",
    help="Generate analysis reports about the current branch",
)


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


def run_plan_mode(
    plan_file: Path,
    *,
    config: Path | None = None,
    repo_path: str | None = None,
    input_collector: InputCollector | None = None,
    chain_collector: UsageStats | None = None,
    import_path: Path | None = None,
    init_content_override: str | None = None,
) -> None:
    if init_content_override is not None:
        content = init_content_override
    else:
        content = _read_plan_file(plan_file)

    cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(
        config,
        plan_file,
        repo_path=repo_path if repo_path is not None else ".",
        claude_cwd=repo_path,
    )

    imported_brief: str | None
    if import_path is not None:
        imported_brief, imported_brief_source = _read_import_file(import_path, cfg.import_max_bytes)
    else:
        imported_brief = None
        imported_brief_source = ""

    plan_file_str = str(plan_file.resolve()) if repo_path is not None else to_rel_path(plan_file)
    try:
        progress_path = compute_progress_path(
            Mode.PLAN,
            plan_file=plan_file_str,
            tasks_root=cfg.tasks_root,
            default_branch=default_branch,
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None
    log = _build_logger(
        progress_path,
        plan_file_str,
        content,
        Mode.PLAN,
        "",
        colors,
        holder,
        quiet=repo_path is not None,
        progress_jsonl=cfg.progress_jsonl,
    )

    log.print("cadence %s", resolve_version())
    log.print("mode: plan")
    log.print("plan file: %s", plan_file_str)
    log.print("progress: %s", log.path)

    if repo_path is not None:
        plan_path = derive_plan_path(plan_file.resolve(), cfg.init_prompt_name)
    else:
        plan_path = derive_plan_path(plan_file, cfg.init_prompt_name)
    ctx = RunContext(
        mode=Mode.PLAN,
        plan_file=plan_file_str,
        plan_description=content,
        progress_path=log.path,
        default_branch=default_branch,
        local_dir=local_dir,
        derived_plan_path=plan_path,
        imported_brief=imported_brief,
        imported_brief_source=imported_brief_source,
    )

    deps = Dependencies(
        executor=factory(log, cfg.plan_model),
        input_collector=input_collector if input_collector is not None else TerminalCollector(),
        logger=log,
        holder=holder,
        plan_model=cfg.plan_model,
        task_model=cfg.task_model,
        review_model=cfg.review_model,
    )

    repo_root = git_svc.root()
    hook_env = _build_hook_env(
        "plan",
        branch="",
        tasks_root=cfg.tasks_root,
        task_name=sanitize_plan_name(plan_file.parent.name),
    )

    run_success = False
    try:
        _invoke_pre_hook("plan", cfg=cfg, repo_root=repo_root, env=hook_env, logger=log)
        start = time.monotonic()
        try:
            runner = Runner(ctx, cfg, deps)
            if chain_collector is not None:
                runner.set_chain_collector(chain_collector)
            run_success = runner.run()
            if run_success and repo_path is None:
                typer.echo(f"run: cadence task {plan_path}")
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
            duration_ms = int((time.monotonic() - start) * 1000)
            _invoke_post_hook(
                "plan",
                cfg=cfg,
                repo_root=repo_root,
                env=hook_env,
                logger=log,
                success=run_success,
                duration_ms=duration_ms,
            )
    finally:
        log.close(success=run_success)


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


def run_task_mode(
    task_file: Path,
    *,
    config: Path | None = None,
    repo_path: str | None = None,
    chain_collector: UsageStats | None = None,
) -> None:
    if not task_file.is_file():
        typer.echo(f"error: file not found: {task_file}", err=True)
        raise SystemExit(2)

    cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(
        config,
        task_file,
        repo_path=repo_path if repo_path is not None else ".",
        claude_cwd=repo_path,
    )

    git_svc.set_commit_trailer(cfg.commit_trailer)

    try:
        git_svc.ensure_has_commits(
            lambda: ask_yes_no("repository has no commits. create an initial commit?")
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    plan_path_str = str(task_file.resolve()) if repo_path is not None else to_rel_path(task_file)
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
    log = _build_logger(
        progress_path,
        plan_path_str,
        "",
        Mode.FULL,
        branch,
        colors,
        holder,
        quiet=repo_path is not None,
        progress_jsonl=cfg.progress_jsonl,
    )

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
        plan_model=cfg.plan_model,
        task_model=cfg.task_model,
        review_model=cfg.review_model,
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

    repo_root = git_svc.root()
    hook_env = _build_hook_env(
        "task",
        branch=branch,
        tasks_root=cfg.tasks_root,
        task_name=sanitize_plan_name(branch),
    )

    run_success = False
    try:
        _invoke_pre_hook("task", cfg=cfg, repo_root=repo_root, env=hook_env, logger=log)
        start = time.monotonic()
        try:
            runner = Runner(ctx, cfg, deps)
            runner.set_break_event(break_event)
            runner.set_pause_handler(_make_pause_handler(log))
            runner.set_git_checker(git_svc)
            if chain_collector is not None:
                runner.set_chain_collector(chain_collector)

            run_success = runner.run()
            if run_success:
                stats = git_svc.diff_stats(default_branch)
                try:
                    git_svc.mark_plan_completed(plan_path_str)
                except (RuntimeError, OSError) as exc:
                    log.warn("could not mark plan completed: %s", exc)
                if repo_path is None:
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
            duration_ms = int((time.monotonic() - start) * 1000)
            _invoke_post_hook(
                "task",
                cfg=cfg,
                repo_root=repo_root,
                env=hook_env,
                logger=log,
                success=run_success,
                duration_ms=duration_ms,
            )
    finally:
        log.close(success=run_success)


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
    log = _build_logger(
        progress_path,
        plan_file,
        "",
        Mode.REVIEW,
        branch,
        colors,
        holder,
        progress_jsonl=cfg.progress_jsonl,
    )

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
        plan_model=cfg.plan_model,
        task_model=cfg.task_model,
        review_model=cfg.review_model,
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

    repo_root = git_svc.root()
    hook_env = _build_hook_env(
        "review",
        branch=branch,
        tasks_root=cfg.tasks_root,
        task_name=sanitize_plan_name(branch) if branch else "",
    )

    run_success = False
    try:
        _invoke_pre_hook("review", cfg=cfg, repo_root=repo_root, env=hook_env, logger=log)
        start = time.monotonic()
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
            duration_ms = int((time.monotonic() - start) * 1000)
            _invoke_post_hook(
                "review",
                cfg=cfg,
                repo_root=repo_root,
                env=hook_env,
                logger=log,
                success=run_success,
                duration_ms=duration_ms,
            )
    finally:
        log.close(success=run_success)


def run_squash_mode(
    *,
    config: Path | None = None,
    repo_path: str | None = None,
    chain_collector: UsageStats | None = None,
) -> None:
    cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(
        config,
        anchor=None,
        repo_path=repo_path if repo_path is not None else ".",
        claude_cwd=repo_path,
    )

    git_svc.set_commit_trailer(cfg.commit_trailer)

    branch = git_svc.current_branch()
    if not branch:
        typer.echo("error: cannot squash from a detached HEAD", err=True)
        raise SystemExit(2)

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
        typer.echo(f"error: cannot squash on default branch {default_branch}", err=True)
        raise SystemExit(2)

    if not task_dir.is_dir():
        typer.echo(f"error: task directory not found: {task_dir}", err=True)
        raise SystemExit(2)

    completed = task_dir / "plan-completed"
    if not completed.is_file():
        typer.echo("error: plan not completed; squash refused", err=True)
        raise SystemExit(2)

    if git_svc.is_dirty():
        typer.echo("error: uncommitted changes present", err=True)
        raise SystemExit(2)

    ahead = git_svc.commits_ahead(default_branch)
    if ahead == 0:
        typer.echo(f"error: no commits ahead of {default_branch}", err=True)
        raise SystemExit(2)
    if ahead == 1:
        if repo_path is None:
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

    log = _build_logger(
        progress_path,
        "",
        "",
        Mode.SQUASH,
        branch,
        colors,
        holder,
        quiet=repo_path is not None,
        progress_jsonl=cfg.progress_jsonl,
    )

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

    repo_root = git_svc.root()
    hook_env = _build_hook_env(
        "squash",
        branch=branch,
        tasks_root=cfg.tasks_root,
        task_name=sanitize_plan_name(branch),
    )

    run_success = False
    phase_stats = UsageStats()
    phase_model = cfg.task_model
    try:
        _invoke_pre_hook("squash", cfg=cfg, repo_root=repo_root, env=hook_env, logger=log)
        start = time.monotonic()
        try:
            iter_start = time.monotonic()
            result = claude.run(prompt)
            iter_ms = int((time.monotonic() - iter_start) * 1000)
            phase_stats.add(result.usage, duration_ms=iter_ms)
            if result.model:
                phase_model = result.model
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
            if repo_path is None:
                display_stats(stats, log.elapsed(), branch)
            run_success = True
        except KeyboardInterrupt:
            log.print("interrupted by user")
            return
        except Exception as exc:
            log.error("execution failed: %s", exc)
            raise SystemExit(1) from exc
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            if cfg.print_usage:
                cost = estimate_cost(phase_stats, phase_model)
                phase_stats.set_cost(cost)
                log.print(
                    "%s",
                    format_phase_summary(
                        phase_stats,
                        phase_model,
                        "squash",
                        cost_estimates=cfg.cost_estimates,
                    ),
                )
                if chain_collector is not None:
                    chain_collector.merge(phase_stats)
            _invoke_post_hook(
                "squash",
                cfg=cfg,
                repo_root=repo_root,
                env=hook_env,
                logger=log,
                success=run_success,
                duration_ms=duration_ms,
            )
    finally:
        log.close(success=run_success)


class _ReportPhaseTracker:
    def __init__(self, inner: object, phase_stats: UsageStats) -> None:
        self._inner = inner
        self._phase_stats = phase_stats
        self.last_model: str = ""

    def run(self, prompt: str) -> object:
        iter_start = time.monotonic()
        result = self._inner.run(prompt)  # type: ignore[attr-defined]
        iter_ms = int((time.monotonic() - iter_start) * 1000)
        self._phase_stats.add(result.usage, duration_ms=iter_ms)
        if result.model:
            self.last_model = result.model
        return result


def _emit_report_phase_summary(
    *,
    cfg: Config,
    log: Logger,
    phase_stats: UsageStats,
    phase_model: str,
    phase_label: str,
) -> None:
    if not cfg.print_usage:
        return
    cost = estimate_cost(phase_stats, phase_model)
    phase_stats.set_cost(cost)
    log.print(
        "%s",
        format_phase_summary(
            phase_stats,
            phase_model,
            phase_label,
            cost_estimates=cfg.cost_estimates,
        ),
    )


def run_report_api_changes_mode(
    *,
    base: str | None = None,
    stdout_only: bool = False,
    config: Path | None = None,
) -> None:
    cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(
        config, anchor=None
    )

    git_svc.set_commit_trailer(cfg.commit_trailer)

    if base is not None:
        default_branch = base

    branch = git_svc.current_branch()
    if not branch:
        typer.echo("error: cannot report from a detached HEAD", err=True)
        raise SystemExit(2)

    task_dir = Path(cfg.tasks_root) / sanitize_plan_name(branch)

    if config is None:
        per_task_yaml = find_yaml_config(task_dir)
        if per_task_yaml is not None:
            try:
                apply_yaml_overrides(cfg, load_yaml_config(per_task_yaml))
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise SystemExit(1) from None
            if base is None:
                default_branch = cfg.default_branch

    if git_svc.is_default_branch(default_branch):
        typer.echo(f"error: cannot report on default branch {default_branch}", err=True)
        raise SystemExit(2)

    try:
        progress_path = compute_progress_path(
            Mode.REPORT,
            branch=branch,
            tasks_root=cfg.tasks_root,
            default_branch=default_branch,
            report_type="api-changes",
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    report_path = compute_report_path("api-changes", branch=branch, tasks_root=cfg.tasks_root)

    log = _build_logger(
        progress_path,
        "",
        "",
        Mode.REPORT,
        branch,
        colors,
        holder,
        progress_jsonl=cfg.progress_jsonl,
    )

    log.print("cadence %s", resolve_version())
    log.print("mode: report")
    log.print("report: api-changes")
    log.print("branch: %s", branch)
    log.print("base: %s", default_branch)
    log.print("progress: %s", log.path)

    git_svc.set_log(log)

    model = cfg.report_api_changes_model or cfg.review_model
    claude = factory(log, model)
    phase_stats = UsageStats()
    tracker = _ReportPhaseTracker(claude, phase_stats)

    repo_root = git_svc.root()
    hook_env = _build_hook_env(
        "report",
        branch=branch,
        tasks_root=cfg.tasks_root,
        task_name=sanitize_plan_name(branch),
        report_type="api-changes",
    )

    run_success = False
    try:
        _invoke_pre_hook("report", cfg=cfg, repo_root=repo_root, env=hook_env, logger=log)
        start = time.monotonic()
        try:
            run_success = run_report(
                "api-changes",
                base=default_branch,
                stdout_only=stdout_only,
                executor=tracker,  # type: ignore[arg-type]
                git_svc=git_svc,
                logger=log,
                local_dir=local_dir,
                public_api_paths=cfg.public_api_paths,
                branch=branch,
                default_branch=default_branch,
                report_path=report_path,
            )
            if run_success and not stdout_only:
                typer.echo(f"wrote: {report_path}")
        except KeyboardInterrupt:
            log.print("interrupted by user")
            return
        except RuntimeError as exc:
            log.error("execution failed: %s", exc)
            raise SystemExit(1) from None
        except Exception as exc:
            log.error("execution failed: %s", exc)
            raise SystemExit(1) from exc
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            _emit_report_phase_summary(
                cfg=cfg,
                log=log,
                phase_stats=phase_stats,
                phase_model=tracker.last_model or model,
                phase_label="report-api-changes",
            )
            _invoke_post_hook(
                "report",
                cfg=cfg,
                repo_root=repo_root,
                env=hook_env,
                logger=log,
                success=run_success,
                duration_ms=duration_ms,
            )
    finally:
        log.close(success=run_success)


def run_report_test_cases_mode(
    *,
    base: str | None = None,
    stdout_only: bool = False,
    config: Path | None = None,
) -> None:
    cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(
        config, anchor=None
    )

    git_svc.set_commit_trailer(cfg.commit_trailer)

    if base is not None:
        default_branch = base

    branch = git_svc.current_branch()
    if not branch:
        typer.echo("error: cannot report from a detached HEAD", err=True)
        raise SystemExit(2)

    task_dir = Path(cfg.tasks_root) / sanitize_plan_name(branch)

    if config is None:
        per_task_yaml = find_yaml_config(task_dir)
        if per_task_yaml is not None:
            try:
                apply_yaml_overrides(cfg, load_yaml_config(per_task_yaml))
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise SystemExit(1) from None
            if base is None:
                default_branch = cfg.default_branch

    if git_svc.is_default_branch(default_branch):
        typer.echo(f"error: cannot report on default branch {default_branch}", err=True)
        raise SystemExit(2)

    try:
        progress_path = compute_progress_path(
            Mode.REPORT,
            branch=branch,
            tasks_root=cfg.tasks_root,
            default_branch=default_branch,
            report_type="test-cases",
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    report_path = compute_report_path("test-cases", branch=branch, tasks_root=cfg.tasks_root)

    log = _build_logger(
        progress_path,
        "",
        "",
        Mode.REPORT,
        branch,
        colors,
        holder,
        progress_jsonl=cfg.progress_jsonl,
    )

    log.print("cadence %s", resolve_version())
    log.print("mode: report")
    log.print("report: test-cases")
    log.print("branch: %s", branch)
    log.print("base: %s", default_branch)
    log.print("progress: %s", log.path)

    git_svc.set_log(log)

    model = cfg.report_test_cases_model or cfg.review_model
    claude = factory(log, model)
    phase_stats = UsageStats()
    tracker = _ReportPhaseTracker(claude, phase_stats)

    repo_root = git_svc.root()
    hook_env = _build_hook_env(
        "report",
        branch=branch,
        tasks_root=cfg.tasks_root,
        task_name=sanitize_plan_name(branch),
        report_type="test-cases",
    )

    run_success = False
    try:
        _invoke_pre_hook("report", cfg=cfg, repo_root=repo_root, env=hook_env, logger=log)
        start = time.monotonic()
        try:
            run_success = run_report(
                "test-cases",
                base=default_branch,
                stdout_only=stdout_only,
                executor=tracker,  # type: ignore[arg-type]
                git_svc=git_svc,
                logger=log,
                local_dir=local_dir,
                public_api_paths=cfg.public_api_paths,
                branch=branch,
                default_branch=default_branch,
                report_path=report_path,
            )
            if run_success and not stdout_only:
                typer.echo(f"wrote: {report_path}")
        except KeyboardInterrupt:
            log.print("interrupted by user")
            return
        except RuntimeError as exc:
            log.error("execution failed: %s", exc)
            raise SystemExit(1) from None
        except Exception as exc:
            log.error("execution failed: %s", exc)
            raise SystemExit(1) from exc
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            _emit_report_phase_summary(
                cfg=cfg,
                log=log,
                phase_stats=phase_stats,
                phase_model=tracker.last_model or model,
                phase_label="report-test-cases",
            )
            _invoke_post_hook(
                "report",
                cfg=cfg,
                repo_root=repo_root,
                env=hook_env,
                logger=log,
                success=run_success,
                duration_ms=duration_ms,
            )
    finally:
        log.close(success=run_success)


def run_task_init_mode(task_name: str, *, config: Path | None = None) -> None:
    if not task_name or "/" in task_name or "\\" in task_name or task_name.startswith((".", "-")):
        typer.echo(f"error: invalid task name: {task_name!r}", err=True)
        raise SystemExit(2)

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
        typer.echo("error: cannot init from a detached HEAD", err=True)
        raise SystemExit(2)

    task_dir = Path(cfg.tasks_root) / task_name
    if task_dir.exists():
        typer.echo(f"error: task directory already exists: {task_dir}", err=True)
        raise SystemExit(2)

    if git_svc.branch_exists(task_name):
        typer.echo(f"error: branch already exists: {task_name}", err=True)
        raise SystemExit(2)

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

    typer.echo("next: cadence run")


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

    task_dir = Path(cfg.tasks_root) / sanitize_plan_name(branch)
    if not task_dir.is_dir():
        typer.echo(f"error: task directory not found: {task_dir}", err=True)
        raise SystemExit(2)

    return cfg, branch, task_dir


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


def _run_plan_on_current_branch(
    *,
    config: Path | None,
    repo_path: str | None = None,
    input_collector: InputCollector | None = None,
    chain_collector: UsageStats | None = None,
    import_path: Path | None = None,
) -> None:
    cfg, _branch, task_dir = _resolve_current_task_dir(config, repo_path=repo_path)

    init_file = task_dir / cfg.init_prompt_name
    if not init_file.is_file():
        typer.echo(f"error: init file not found: {init_file}", err=True)
        raise SystemExit(2)

    if not init_file.read_text(encoding="utf-8").strip():
        typer.echo(f"error: init file is empty: {init_file}", err=True)
        raise SystemExit(2)

    run_plan_mode(
        init_file,
        config=config,
        repo_path=repo_path,
        input_collector=input_collector,
        chain_collector=chain_collector,
        import_path=import_path,
    )


def _run_task_on_current_branch(
    *,
    config: Path | None,
    repo_path: str | None = None,
    chain_collector: UsageStats | None = None,
) -> None:
    _cfg, _branch, task_dir = _resolve_current_task_dir(config, repo_path=repo_path)

    plan_file = task_dir / "plan"
    if not plan_file.is_file():
        typer.echo(f"error: plan file not found: {plan_file}", err=True)
        raise SystemExit(2)

    run_task_mode(plan_file, config=config, repo_path=repo_path, chain_collector=chain_collector)


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
        raise SystemExit(2)
    if git_svc.current_branch() == "":
        typer.echo("error: cannot chain from a detached HEAD", err=True)
        raise SystemExit(2)

    names = _parse_chain_file(chain_file)

    warnings = _validate_chain_tasks(cfg.tasks_root, names)
    if warnings:
        for w in warnings:
            typer.echo(f"warn: {w}", err=True)
        raise SystemExit(2)

    total = len(names)
    chain_stats = UsageStats()

    def _emit_chain_summary() -> None:
        if cfg.print_usage:
            typer.echo(
                format_chain_summary(
                    chain_stats,
                    cost_estimates=cfg.cost_estimates,
                    tasks=total,
                )
            )

    def _bail_if_interrupted(i: int, name: str) -> None:
        if _sigint.shutdown_event.is_set():
            typer.echo(f"chain interrupted at task {i}/{total}: {name}", err=True)
            raise SystemExit(1)

    for i, name in enumerate(names, start=1):
        typer.echo(f"[chain {i}/{total}] {name}")
        try:
            task_default = _resolve_chain_default_branch(cfg.tasks_root, name, default_branch)
            if git_svc.branch_exists(name):
                git_svc.checkout_branch(name)
            else:
                git_svc.create_branch_from(name, task_default)
            _run_plan_on_current_branch(config=config, chain_collector=chain_stats)
            _bail_if_interrupted(i, name)
            _run_task_on_current_branch(config=config, chain_collector=chain_stats)
            _bail_if_interrupted(i, name)
            run_squash_mode(config=config, chain_collector=chain_stats)
            _bail_if_interrupted(i, name)
        except SystemExit as exc:
            if exc.code != 0 and not _sigint.shutdown_event.is_set():
                typer.echo(
                    f"chain failed at task {i}/{total}: {name}",
                    err=True,
                )
            _emit_chain_summary()
            raise
        except RuntimeError as exc:
            typer.echo(f"error: {exc}", err=True)
            typer.echo(
                f"chain failed at task {i}/{total}: {name}",
                err=True,
            )
            _emit_chain_summary()
            raise SystemExit(1) from None

    typer.echo(f"chain complete: {total} task(s)")
    _emit_chain_summary()


@dataclass(frozen=True)
class _ParallelTaskResult:
    name: str
    status: str
    phase: str = ""
    elapsed: str = ""
    error: str = ""
    usage: UsageStats | None = None


def _format_elapsed(seconds: float) -> str:
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h}h {m}m {sec}s"


_chain_print_lock = threading.Lock()


def _chain_echo(msg: str, *, err: bool = False) -> None:
    with _chain_print_lock:
        typer.echo(msg, err=err)


def _run_parallel_worker(
    name: str,
    *,
    worktree_path: str,
    task_default: str,
    config: Path | None,
    git_svc: Service,
    tasks_root: str,
    stop_event: threading.Event,
) -> _ParallelTaskResult:
    worker_stats = UsageStats()

    if stop_event.is_set():
        return _ParallelTaskResult(name=name, status="cancelled", usage=worker_stats)

    start = time.monotonic()
    _chain_echo(f"[chain] {name}: started")

    def progress_for(phase: str) -> str:
        suffix = {
            "plan": "progress-plan.txt",
            "task": "progress-task.txt",
            "squash": "progress-squash.txt",
        }[phase]
        return str(Path(tasks_root) / sanitize_plan_name(name) / suffix)

    def fail(phase: str, exc: BaseException) -> _ParallelTaskResult:
        stop_event.set()
        elapsed = _format_elapsed(time.monotonic() - start)
        if isinstance(exc, SystemExit):
            cause = exc.__cause__
            detail = str(cause) if cause is not None else ""
        else:
            detail = str(exc)
        msg = f"[chain] {name}: failed at {phase} ({elapsed}) — see {progress_for(phase)}"
        if detail:
            msg += f": {detail}"
        _chain_echo(msg)
        return _ParallelTaskResult(
            name=name,
            status="failed",
            phase=phase,
            elapsed=elapsed,
            error=detail or str(exc),
            usage=worker_stats,
        )

    try:
        git_svc.worktree_add(worktree_path, name, task_default)
    except RuntimeError as exc:
        stop_event.set()
        elapsed = _format_elapsed(time.monotonic() - start)
        _chain_echo(f"[chain] {name}: failed at worktree_add ({elapsed}): {exc}")
        return _ParallelTaskResult(
            name=name,
            status="failed",
            phase="worktree_add",
            elapsed=elapsed,
            error=str(exc),
            usage=worker_stats,
        )

    try:
        _run_plan_on_current_branch(
            config=config,
            repo_path=worktree_path,
            input_collector=ParallelAbortCollector(),
            chain_collector=worker_stats,
        )
    except BaseException as exc:
        return fail("plan", exc)

    try:
        _run_task_on_current_branch(
            config=config,
            repo_path=worktree_path,
            chain_collector=worker_stats,
        )
    except BaseException as exc:
        return fail("task", exc)

    try:
        run_squash_mode(
            config=config,
            repo_path=worktree_path,
            chain_collector=worker_stats,
        )
    except BaseException as exc:
        return fail("squash", exc)

    elapsed = _format_elapsed(time.monotonic() - start)
    try:
        git_svc.worktree_remove(worktree_path)
    except RuntimeError as rm_exc:
        _chain_echo(f"[chain] {name}: warn: worktree remove failed: {rm_exc}", err=True)
    _chain_echo(f"[chain] {name}: completed ({elapsed})")
    return _ParallelTaskResult(name=name, status="ok", elapsed=elapsed, usage=worker_stats)


def run_chain_parallel(
    chain_file: Path,
    *,
    parallel: int,
    config: Path | None = None,
) -> None:
    from concurrent.futures import Future, ThreadPoolExecutor, as_completed

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
        raise SystemExit(2)
    if git_svc.current_branch() == "":
        typer.echo("error: cannot chain from a detached HEAD", err=True)
        raise SystemExit(2)

    names = _parse_chain_file(chain_file)

    warnings = _validate_chain_tasks(cfg.tasks_root, names)
    if warnings:
        for w in warnings:
            typer.echo(f"warn: {w}", err=True)
        raise SystemExit(2)

    repo_root = Path(git_svc.root())
    worktree_root = repo_root / ".cadence" / "worktrees"
    worktree_paths = {name: str(worktree_root / name) for name in names}

    collisions: list[str] = []
    for name in names:
        wt = Path(worktree_paths[name])
        if wt.exists() or git_svc.worktree_exists(str(wt)):
            collisions.append(f"worktree path already exists: {wt}")
        if git_svc.branch_exists(name):
            collisions.append(f"branch already exists: {name}")
    if collisions:
        for c in collisions:
            typer.echo(f"error: {c}", err=True)
        raise SystemExit(2)

    task_defaults = {
        name: _resolve_chain_default_branch(cfg.tasks_root, name, default_branch) for name in names
    }

    total = len(names)
    _chain_echo(f"[chain] starting {total} parallel tasks: {', '.join(names)}")

    stop_event = threading.Event()
    results: dict[str, _ParallelTaskResult] = {}

    pool = ThreadPoolExecutor(max_workers=parallel)
    futures: dict[Future[_ParallelTaskResult], str] = {}
    for name in names:
        fut = pool.submit(
            _run_parallel_worker,
            name,
            worktree_path=worktree_paths[name],
            task_default=task_defaults[name],
            config=config,
            git_svc=git_svc,
            tasks_root=cfg.tasks_root,
            stop_event=stop_event,
        )
        futures[fut] = name

    try:
        for fut in as_completed(list(futures)):
            name = futures[fut]
            if fut.cancelled():
                results[name] = _ParallelTaskResult(name=name, status="cancelled")
                continue
            try:
                result = fut.result()
            except BaseException as exc:
                stop_event.set()
                _chain_echo(f"[chain] {name}: failed: {exc}", err=True)
                result = _ParallelTaskResult(name=name, status="failed", error=str(exc))
            results[name] = result
            if result.status == "failed":
                for f in list(futures):
                    if not f.done() and not f.running():
                        f.cancel()
    finally:
        pool.shutdown(wait=True)

    for fut, name in futures.items():
        if name not in results:
            if fut.cancelled():
                results[name] = _ParallelTaskResult(name=name, status="cancelled")
            elif fut.done():
                try:
                    results[name] = fut.result()
                except BaseException as exc:
                    results[name] = _ParallelTaskResult(name=name, status="failed", error=str(exc))

    succeeded = sum(1 for r in results.values() if r.status == "ok")
    _chain_echo(f"[chain] complete: {succeeded}/{total} succeeded")

    if cfg.print_usage:
        chain_stats = UsageStats()
        for r in results.values():
            if r.usage is not None:
                chain_stats.merge(r.usage)
        _chain_echo(
            format_chain_summary(
                chain_stats,
                cost_estimates=cfg.cost_estimates,
                tasks=total,
            )
        )

    if any(r.status == "failed" for r in results.values()):
        raise SystemExit(1)


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


@app.callback()
def app_callback(
    ctx: typer.Context,
    config: Path | None = _CONFIG_OPTION,
    version: bool = _VERSION_OPTION,
) -> None:
    _ = version
    ctx.obj = GlobalOpts(config=config)


@app.command("init", help="Scaffold a new task: branch + tasks_root/<name>/init [+ config.yaml]")
def cmd_init(ctx: typer.Context, task_name: str = _TASK_NAME_ARG) -> None:
    opts = _ctx_opts(ctx)
    run_task_init_mode(task_name, config=opts.config)


@run_app.callback(invoke_without_command=True)
def cmd_run(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    opts = _ctx_opts(ctx)
    _arm_sigint()
    _auto_detect_and_run(config=opts.config)


@run_app.command("plan", help="Run plan creation on the current branch's init file")
def cmd_run_plan(
    ctx: typer.Context,
    import_path: Path | None = _IMPORT_PATH_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    _run_plan_on_current_branch(config=opts.config, import_path=import_path)


@run_app.command("task", help="Run task execution on the current branch's plan file")
def cmd_run_task(ctx: typer.Context) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    _run_task_on_current_branch(config=opts.config)


@app.command("plan", help="Create a plan from a prompt file at <path>")
def cmd_plan(
    ctx: typer.Context,
    path: Path = _PATH_ARG,
    import_: bool = _IMPORT_FLAG,
) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    if import_:
        run_plan_mode(
            path,
            config=opts.config,
            import_path=path,
            init_content_override="",
        )
    else:
        run_plan_mode(path, config=opts.config)


@app.command("task", help="Execute tasks from a plan file at <path>")
def cmd_task(ctx: typer.Context, path: Path = _PATH_ARG) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_task_mode(path, config=opts.config)


@app.command("review", help="Review the current branch")
def cmd_review(ctx: typer.Context, base: str | None = _BASE_OPTION) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_review_mode(base, config=opts.config)


@app.command("squash", help="Squash all commits on the current branch into one")
def cmd_squash(ctx: typer.Context) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_squash_mode(config=opts.config)


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


@app.command("status", help="Show the status of cadence tasks under tasks_root")
def cmd_status(
    ctx: typer.Context,
    current: bool = _CURRENT_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    run_status_mode(current_only=current, json_output=json_output, config=opts.config)


@app.command("doctor", help="Run pre-flight environment & config checks (no Claude calls)")
def cmd_doctor(ctx: typer.Context) -> None:
    opts = _ctx_opts(ctx)
    run_doctor_mode(config=opts.config)


@app.command("chain", help="Run a sequence of tasks listed in a file (one task name per line)")
def cmd_chain(
    ctx: typer.Context,
    path: Path = _PATH_ARG,
    parallel: int = _PARALLEL_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    if parallel < 1:
        typer.echo("error: --parallel must be >= 1", err=True)
        raise SystemExit(2)
    if parallel == 1:
        run_chain_mode(path, config=opts.config)
        return
    run_chain_parallel(path, parallel=parallel, config=opts.config)


@report_app.command(
    "api-changes",
    help="Generate an API-changes report for the current branch",
)
def cmd_report_api_changes(
    ctx: typer.Context,
    base: str | None = _BASE_OPTION,
    stdout_only: bool = _STDOUT_ONLY_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_report_api_changes_mode(base=base, stdout_only=stdout_only, config=opts.config)


@report_app.command(
    "test-cases",
    help="Generate a manual-QA test-case report for the current branch",
)
def cmd_report_test_cases(
    ctx: typer.Context,
    base: str | None = _BASE_OPTION,
    stdout_only: bool = _STDOUT_ONLY_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_report_test_cases_mode(base=base, stdout_only=stdout_only, config=opts.config)
