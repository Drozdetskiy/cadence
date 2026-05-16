from __future__ import annotations

import time
from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _arm_sigint,
    _build_hook_env,
    _build_logger,
    _ctx_opts,
    _echo_dirty_status,
    _invoke_post_hook,
    _invoke_pre_hook,
    _sanitize_branch_or_die,
    _setup_runtime,
    compute_progress_path,
    display_stats,
    resolve_version,
)
from cadence.config import (
    apply_yaml_overrides,
    find_yaml_config,
    load_yaml_config,
)
from cadence.processor.prompts import build_squash_commit_prompt
from cadence.processor.signals import parse_squash_commit_message
from cadence.progress.logger import sanitize_plan_name
from cadence.status import Mode
from cadence.usage import (
    UsageStats,
    estimate_cost,
    format_phase_summary,
)


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
    _sanitize_branch_or_die(branch, tasks_root=cfg.tasks_root)

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
        _echo_dirty_status(git_svc.dirty_status_lines())
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

    claude = factory(log, cfg.squash_model)

    repo_root = git_svc.root()
    hook_env = _build_hook_env(
        "squash",
        branch=branch,
        tasks_root=cfg.tasks_root,
        task_name=sanitize_plan_name(branch),
    )

    run_success = False
    phase_stats = UsageStats()
    phase_model = cfg.squash_model
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


def cmd_squash(ctx: typer.Context) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_squash_mode(config=opts.config)
