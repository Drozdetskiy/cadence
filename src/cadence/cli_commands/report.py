from __future__ import annotations

import time
from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _BASE_OPTION,
    _STDOUT_ONLY_OPTION,
    _arm_sigint,
    _build_hook_env,
    _build_logger,
    _ctx_opts,
    _invoke_post_hook,
    _invoke_pre_hook,
    _sanitize_branch_or_die,
    _setup_runtime,
    compute_progress_path,
    compute_report_path,
    resolve_version,
)
from cadence.config import (
    Config,
    apply_yaml_overrides,
    find_yaml_config,
    load_yaml_config,
)
from cadence.processor.reporter import run_report
from cadence.progress.logger import Logger, sanitize_plan_name
from cadence.status import Mode
from cadence.usage import (
    UsageStats,
    estimate_cost,
    format_phase_summary,
)


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


def cmd_report_api_changes(
    ctx: typer.Context,
    base: str | None = _BASE_OPTION,
    stdout_only: bool = _STDOUT_ONLY_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_report_api_changes_mode(base=base, stdout_only=stdout_only, config=opts.config)


def cmd_report_test_cases(
    ctx: typer.Context,
    base: str | None = _BASE_OPTION,
    stdout_only: bool = _STDOUT_ONLY_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    run_report_test_cases_mode(base=base, stdout_only=stdout_only, config=opts.config)
