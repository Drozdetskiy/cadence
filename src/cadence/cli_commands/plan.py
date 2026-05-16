from __future__ import annotations

import time
from pathlib import Path

import typer

from cadence.cli_commands._shared import (
    _IMPORT_FLAG,
    _IMPORT_PATH_OPTION,
    _PATH_ARG,
    _arm_sigint,
    _build_hook_env,
    _build_logger,
    _ctx_opts,
    _invoke_post_hook,
    _invoke_pre_hook,
    _read_import_file,
    _read_plan_file,
    _resolve_current_task_dir,
    _setup_runtime,
    compute_progress_path,
    derive_plan_path,
    resolve_version,
    to_rel_path,
)
from cadence.input import TerminalCollector
from cadence.processor.runner import (
    Dependencies,
    InputCollector,
    RunContext,
    Runner,
    UserAbortedError,
)
from cadence.progress.logger import sanitize_plan_name
from cadence.status import Mode
from cadence.usage import UsageStats


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


def cmd_run_plan(
    ctx: typer.Context,
    import_path: Path | None = _IMPORT_PATH_OPTION,
) -> None:
    opts = _ctx_opts(ctx)
    _arm_sigint()
    _run_plan_on_current_branch(config=opts.config, import_path=import_path)
