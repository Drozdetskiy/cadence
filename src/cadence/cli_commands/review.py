from __future__ import annotations

import threading
import time
from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _BASE_OPTION,
    _arm_sigint,
    _build_hook_env,
    _build_logger,
    _ctx_opts,
    _install_sigquit,
    _invoke_post_hook,
    _invoke_pre_hook,
    _make_pause_handler,
    _sanitize_branch_or_die,
    _setup_runtime,
    compute_progress_path,
    display_stats,
    find_existing_plan,
    resolve_version,
)
from cadence.input import TerminalCollector
from cadence.processor.runner import (
    Dependencies,
    RunContext,
    Runner,
    UserAbortedError,
)
from cadence.progress.logger import sanitize_plan_name
from cadence.status import Mode


def run_review_mode(base: str | None = None, *, config: Path | None = None) -> None:
    cfg, holder, colors, git_svc, factory, default_branch, local_dir = _setup_runtime(config, None)

    git_svc.set_commit_trailer(cfg.commit_trailer)

    if base is not None:
        default_branch = base

    branch = git_svc.current_branch()
    if branch:
        _sanitize_branch_or_die(branch, tasks_root=cfg.tasks_root)
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

    review_second_claude = (
        factory(log, cfg.review_second_model)
        if cfg.review_second_model and cfg.review_second_model != cfg.review_model
        else None
    )
    deps = Dependencies(
        executor=factory(log, cfg.review_model),
        input_collector=TerminalCollector(),
        logger=log,
        holder=holder,
        review_second_executor=review_second_claude,
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


def cmd_review(ctx: typer.Context, base: str | None = _BASE_OPTION) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_review_mode(base, config=opts.config)
