from __future__ import annotations

import threading
import time
from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _PATH_ARG,
    _arm_sigint,
    _build_hook_env,
    _build_logger,
    _ctx_opts,
    _install_sigquit,
    _invoke_post_hook,
    _invoke_pre_hook,
    _make_pause_handler,
    _resolve_current_task_dir,
    _sanitize_branch_or_die,
    _setup_runtime,
    compute_progress_path,
    display_stats,
    resolve_version,
    to_rel_path,
)
from cadence.input import TerminalCollector, ask_yes_no
from cadence.processor.runner import (
    Dependencies,
    RunContext,
    Runner,
    UserAbortedError,
)
from cadence.progress.logger import sanitize_plan_name
from cadence.status import Mode
from cadence.usage import UsageStats


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
    _sanitize_branch_or_die(branch, tasks_root=cfg.tasks_root)

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
    review_second_claude = (
        factory(log, cfg.review_second_model)
        if cfg.review_second_model and cfg.review_second_model != cfg.review_model
        else None
    )

    deps = Dependencies(
        executor=claude,
        input_collector=TerminalCollector(),
        logger=log,
        holder=holder,
        review_executor=review_claude,
        review_second_executor=review_second_claude,
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


def cmd_task(ctx: typer.Context, path: Path = _PATH_ARG) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_task_mode(path, config=opts.config)


def cmd_run_task(ctx: typer.Context) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    _run_task_on_current_branch(config=opts.config)
