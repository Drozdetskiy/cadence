from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _PARALLEL_OPTION,
    _PATH_ARG,
    _arm_sigint,
    _ctx_opts,
    _echo_dirty_status,
    _parse_chain_file,
    _resolve_chain_default_branch,
    _setup_runtime,
    _sigint,
    _validate_chain_tasks,
)
from cadence.cli_commands.plan import _run_plan_on_current_branch
from cadence.cli_commands.squash import run_squash_mode
from cadence.cli_commands.task import _run_task_on_current_branch
from cadence.git import Service
from cadence.input import ParallelAbortCollector, ask_yes_no
from cadence.progress.logger import sanitize_plan_name
from cadence.usage import (
    UsageStats,
    format_chain_summary,
)


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
        _echo_dirty_status(git_svc.dirty_status_lines())
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
            if git_svc.is_dirty():
                _echo_dirty_status(
                    git_svc.dirty_status_lines(),
                    header=(
                        f"error: uncommitted changes present at start of task {i}/{total} ({name}):"
                    ),
                )
                raise SystemExit(2)
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
        _echo_dirty_status(git_svc.dirty_status_lines())
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
