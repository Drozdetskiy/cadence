from __future__ import annotations

import dataclasses
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Literal

import yaml
from rich.console import Console
from rich.text import Text

from cadence.config import Config, parse_duration
from cadence.git import Service
from cadence.processor.agents import load_agent
from cadence.processor.prompts import _AGENT_REF_RE, load_prompt

Status = Literal["ok", "warn", "fail"]

STATUS_OK: Status = "ok"
STATUS_WARN: Status = "warn"
STATUS_FAIL: Status = "fail"


@dataclass(frozen=True)
class CheckResult:
    status: Status
    category: str
    name: str
    message: str


class _QuietLogger:
    def print(self, fmt: str, *args: object) -> None:
        return None

    def warn(self, fmt: str, *args: object) -> None:
        return None

    def error(self, fmt: str, *args: object) -> None:
        return None


def _capture_version(path: str) -> str:
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError, subprocess.TimeoutExpired, OSError:
        return ""
    if result.returncode != 0:
        return ""
    for stream in (result.stdout, result.stderr):
        for line in stream.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return ""


def check_environment(claude_command: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    category = "environment"

    claude_path = shutil.which(claude_command)
    if claude_path is None:
        results.append(
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="claude",
                message=f"{claude_command!r} not found on PATH",
            )
        )
    else:
        version = _capture_version(claude_path)
        if version:
            results.append(
                CheckResult(
                    status=STATUS_OK,
                    category=category,
                    name="claude",
                    message=f"{claude_path} ({version})",
                )
            )
        else:
            results.append(
                CheckResult(
                    status=STATUS_WARN,
                    category=category,
                    name="claude",
                    message=f"{claude_path} (version unavailable)",
                )
            )

    git_path = shutil.which("git")
    if git_path is None:
        results.append(
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="git",
                message="'git' not found on PATH",
            )
        )
    else:
        version = _capture_version(git_path)
        if version:
            results.append(
                CheckResult(
                    status=STATUS_OK,
                    category=category,
                    name="git",
                    message=f"{git_path} ({version})",
                )
            )
        else:
            results.append(
                CheckResult(
                    status=STATUS_WARN,
                    category=category,
                    name="git",
                    message=f"{git_path} (version unavailable)",
                )
            )

    py_version = platform.python_version()
    if sys.version_info >= (3, 14):  # noqa: UP036  (defensive — pyproject pins >=3.14)
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="python",
                message=py_version,
            )
        )
    else:
        results.append(
            CheckResult(
                status=STATUS_WARN,
                category=category,
                name="python",
                message=f"{py_version} (cadence requires >= 3.14)",
            )
        )

    return results


def check_repository(cfg: Config) -> list[CheckResult]:
    results: list[CheckResult] = []
    category = "repository"

    cwd = Path.cwd()
    results.append(
        CheckResult(
            status=STATUS_OK,
            category=category,
            name="working directory",
            message=str(cwd),
        )
    )

    try:
        service = Service(path=".", log=_QuietLogger())
    except RuntimeError as exc:
        results.append(
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="git repository",
                message=f"not a git repository ({exc})",
            )
        )
        results.extend(_check_tasks_root(cfg.tasks_root, category))
        return results

    results.append(
        CheckResult(
            status=STATUS_OK,
            category=category,
            name="git repository",
            message=service.root(),
        )
    )

    if service.is_dirty():
        results.append(
            CheckResult(
                status=STATUS_WARN,
                category=category,
                name="worktree",
                message="dirty (uncommitted changes)",
            )
        )
    else:
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="worktree",
                message="clean",
            )
        )

    default_name = cfg.default_branch.removeprefix("origin/")
    if service.branch_exists(default_name):
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="default branch",
                message=f"{default_name} (local)",
            )
        )
    elif service.remote_branch_exists(default_name):
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="default branch",
                message=f"{default_name} (remote)",
            )
        )
    else:
        results.append(
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="default branch",
                message=f"{default_name} not found locally or as origin/{default_name}",
            )
        )

    results.extend(_check_tasks_root(cfg.tasks_root, category))
    return results


def _check_tasks_root(tasks_root: str, category: str) -> list[CheckResult]:
    path = Path(tasks_root)
    if not path.is_dir():
        return [
            CheckResult(
                status=STATUS_WARN,
                category=category,
                name="tasks_root",
                message=f"{tasks_root}/ does not exist",
            )
        ]
    try:
        task_dirs = sum(1 for p in path.iterdir() if p.is_dir())
    except OSError as exc:
        return [
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="tasks_root",
                message=f"{tasks_root}/ unreadable ({exc})",
            )
        ]
    if not os.access(path, os.W_OK):
        return [
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="tasks_root",
                message=f"{tasks_root}/ not writable ({task_dirs} task dirs)",
            )
        ]
    return [
        CheckResult(
            status=STATUS_OK,
            category=category,
            name="tasks_root",
            message=f"{tasks_root}/ writable, {task_dirs} task dir{'s' if task_dirs != 1 else ''}",
        )
    ]


KNOWN_MODELS: tuple[str, ...] = (
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
)

MODEL_FIELDS: tuple[str, ...] = (
    "plan_model",
    "task_model",
    "review_model",
    "report_api_changes_model",
    "report_test_cases_model",
)

KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {f.name for f in dataclasses.fields(Config)} | {"colors"}
)

EMBEDDED_PROMPT_NAMES: tuple[str, ...] = (
    "make_plan",
    "task",
    "review_first",
    "review_second",
    "squash_commit",
    "report_api_changes",
    "report_test_cases",
)

EMBEDDED_AGENT_NAMES: tuple[str, ...] = (
    "quality",
    "implementation",
    "testing",
    "simplification",
)

_NON_NEGATIVE_INT_FIELDS: tuple[str, ...] = (
    "iteration_delay_ms",
    "task_retry_count",
    "min_plan_iterations",
    "min_review_iterations",
)

_POSITIVE_INT_FIELDS: tuple[str, ...] = (
    "max_iterations",
    "limit_retry_max",
    "hooks_timeout_seconds",
    "running_threshold_minutes",
    "import_max_bytes",
)

_DURATION_FIELDS: tuple[str, ...] = (
    "session_timeout",
    "idle_timeout",
    "wait_on_limit",
)


def check_config(local_dir: Path | None) -> list[CheckResult]:
    category = "config"
    if local_dir is None:
        return [
            CheckResult(
                status=STATUS_OK,
                category=category,
                name=".cadence",
                message="(no .cadence/ directory)",
            )
        ]

    yaml_path = local_dir / "config.yaml"
    if not yaml_path.is_file():
        return [
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="config.yaml",
                message="(none — defaults in use)",
            )
        ]

    try:
        text = yaml_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="config.yaml",
                message=f"unreadable ({exc})",
            )
        ]

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="config.yaml",
                message=f"invalid YAML ({exc})",
            )
        ]

    if raw is None:
        data: dict[str, Any] = {}
    elif isinstance(raw, dict):
        data = raw
    else:
        return [
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="config.yaml",
                message="top-level must be a mapping",
            )
        ]

    results: list[CheckResult] = [
        CheckResult(
            status=STATUS_OK,
            category=category,
            name="config.yaml",
            message=f"{yaml_path} parses",
        )
    ]

    for key in data:
        if key not in KNOWN_CONFIG_KEYS:
            results.append(
                CheckResult(
                    status=STATUS_WARN,
                    category=category,
                    name=f"key {key!r}",
                    message="unknown config key",
                )
            )

    for field_name in MODEL_FIELDS:
        if field_name in data:
            value = data[field_name]
            if isinstance(value, str) and value and value not in KNOWN_MODELS:
                results.append(
                    CheckResult(
                        status=STATUS_WARN,
                        category=category,
                        name=field_name,
                        message=f'"{value}" not in known model list',
                    )
                )

    for field_name in _NON_NEGATIVE_INT_FIELDS:
        if field_name in data:
            value = data[field_name]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                results.append(
                    CheckResult(
                        status=STATUS_FAIL,
                        category=category,
                        name=field_name,
                        message=f"{field_name} must be >= 0 (got {value!r})",
                    )
                )

    for field_name in _POSITIVE_INT_FIELDS:
        if field_name in data:
            value = data[field_name]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                results.append(
                    CheckResult(
                        status=STATUS_FAIL,
                        category=category,
                        name=field_name,
                        message=f"{field_name} must be > 0 (got {value!r})",
                    )
                )

    for field_name in _DURATION_FIELDS:
        if field_name in data:
            value = data[field_name]
            if not isinstance(value, str):
                results.append(
                    CheckResult(
                        status=STATUS_FAIL,
                        category=category,
                        name=field_name,
                        message=f"{field_name} must be a duration string (got {value!r})",
                    )
                )
                continue
            try:
                parse_duration(value)
            except ValueError as exc:
                results.append(
                    CheckResult(
                        status=STATUS_FAIL,
                        category=category,
                        name=field_name,
                        message=str(exc),
                    )
                )

    return results


def check_prompts(local_dir: Path | None) -> list[CheckResult]:
    category = "prompts"
    results: list[CheckResult] = []

    for name in EMBEDDED_PROMPT_NAMES:
        try:
            load_prompt(name)
        except RuntimeError as exc:
            results.append(
                CheckResult(
                    status=STATUS_FAIL,
                    category=category,
                    name=f"default {name}",
                    message=str(exc),
                )
            )
        else:
            results.append(
                CheckResult(
                    status=STATUS_OK,
                    category=category,
                    name=f"default {name}",
                    message="loads",
                )
            )

    overrides_dir = (local_dir / "prompts") if local_dir is not None else None
    if overrides_dir is None or not overrides_dir.is_dir():
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="overrides",
                message="(no overrides)",
            )
        )
        return results

    override_files = sorted(overrides_dir.glob("*.txt"))
    if not override_files:
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="overrides",
                message="(no overrides)",
            )
        )
        return results

    for path in override_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            results.append(
                CheckResult(
                    status=STATUS_FAIL,
                    category=category,
                    name=f"override {path.name}",
                    message=f"unreadable ({exc})",
                )
            )
            continue

        agent_refs = _AGENT_REF_RE.findall(text)
        missing: list[str] = []
        for agent_name in agent_refs:
            try:
                load_agent(agent_name, local_dir=local_dir)
            except RuntimeError:
                missing.append(agent_name)

        if missing:
            joined = ", ".join(f"{{{{agent:{n}}}}}" for n in missing)
            results.append(
                CheckResult(
                    status=STATUS_WARN,
                    category=category,
                    name=f"override {path.name}",
                    message=f"references undefined agent(s): {joined}",
                )
            )
        else:
            results.append(
                CheckResult(
                    status=STATUS_OK,
                    category=category,
                    name=f"override {path.name}",
                    message="loads",
                )
            )

    return results


def check_agents(local_dir: Path | None) -> list[CheckResult]:
    category = "agents"
    results: list[CheckResult] = []

    for name in EMBEDDED_AGENT_NAMES:
        try:
            agent = load_agent(name)
        except RuntimeError as exc:
            results.append(
                CheckResult(
                    status=STATUS_FAIL,
                    category=category,
                    name=f"default {name}",
                    message=str(exc),
                )
            )
        else:
            model_suffix = f" (model={agent.model})" if agent.model else ""
            results.append(
                CheckResult(
                    status=STATUS_OK,
                    category=category,
                    name=f"default {name}",
                    message=f"loads{model_suffix}",
                )
            )

    overrides_dir = (local_dir / "agents") if local_dir is not None else None
    if overrides_dir is None or not overrides_dir.is_dir():
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="overrides",
                message="(no overrides)",
            )
        )
        return results

    override_files = sorted(overrides_dir.glob("*.txt"))
    if not override_files:
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="overrides",
                message="(no overrides)",
            )
        )
        return results

    for path in override_files:
        warnings: list[str] = []
        try:
            agent = load_agent(path.stem, local_dir=local_dir, warn=warnings.append)
        except RuntimeError as exc:
            results.append(
                CheckResult(
                    status=STATUS_FAIL,
                    category=category,
                    name=f"override {path.name}",
                    message=str(exc),
                )
            )
            continue

        if warnings:
            results.append(
                CheckResult(
                    status=STATUS_WARN,
                    category=category,
                    name=f"override {path.name}",
                    message="; ".join(warnings),
                )
            )
        else:
            model_suffix = f" (model={agent.model})" if agent.model else ""
            results.append(
                CheckResult(
                    status=STATUS_OK,
                    category=category,
                    name=f"override {path.name}",
                    message=f"loads{model_suffix}",
                )
            )

    return results


KNOWN_HOOK_PHASES: tuple[str, ...] = ("plan", "task", "review", "squash", "report")
KNOWN_HOOK_KINDS: tuple[str, ...] = ("pre", "post")

_HOOK_NAME_RE = re.compile(
    r"^(?P<kind>"
    + "|".join(KNOWN_HOOK_KINDS)
    + r")-(?P<phase>"
    + "|".join(KNOWN_HOOK_PHASES)
    + r")$"
)

# Mirror of cadence.processor.prompts._CONTEXT_ALLOWED_EXTENSIONS / load_context_files's
# 200_000-byte budget. Keep these in sync with that source of truth.
_CONTEXT_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".txt", ".yaml", ".yml", ".json", ".sql", ".proto"}
)
_CONTEXT_MAX_BYTES: int = 200_000


def check_hooks(hooks_dir: str, hooks_enabled: bool) -> list[CheckResult]:
    category = "hooks"

    if not hooks_enabled:
        return [
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="hooks",
                message="(disabled in config.yaml)",
            )
        ]

    path = Path(hooks_dir)
    if not path.is_dir():
        return [
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="hooks",
                message=f"({hooks_dir}/ does not exist)",
            )
        ]

    results: list[CheckResult] = []
    files = sorted(path.glob("*.sh"))
    if not files:
        return [
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="hooks",
                message=f"({hooks_dir}/ contains no *.sh files)",
            )
        ]

    for hook_path in files:
        stem = hook_path.stem
        if _HOOK_NAME_RE.match(stem) is None:
            results.append(
                CheckResult(
                    status=STATUS_WARN,
                    category=category,
                    name=hook_path.name,
                    message=(
                        "unknown hook name (expected "
                        "{pre,post}-{plan,task,review,squash,report}.sh)"
                    ),
                )
            )
            continue

        if os.access(hook_path, os.X_OK):
            results.append(
                CheckResult(
                    status=STATUS_OK,
                    category=category,
                    name=hook_path.name,
                    message="executable",
                )
            )
        else:
            results.append(
                CheckResult(
                    status=STATUS_FAIL,
                    category=category,
                    name=hook_path.name,
                    message="not executable (chmod +x to enable)",
                )
            )

    return results


def _format_kb(num_bytes: int) -> str:
    return f"{num_bytes / 1024:.1f} KB"


def check_context(local_dir: Path | None) -> list[CheckResult]:
    category = "context"

    context_dir = (local_dir / "context") if local_dir is not None else None
    if context_dir is None or not context_dir.is_dir():
        return [
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="context",
                message="(no context directory)",
            )
        ]

    try:
        entries = sorted(p for p in context_dir.iterdir() if p.is_file())
    except OSError as exc:
        return [
            CheckResult(
                status=STATUS_FAIL,
                category=category,
                name="context",
                message=f"unreadable ({exc})",
            )
        ]

    results: list[CheckResult] = []
    total_bytes = 0
    for entry in entries:
        ext = entry.suffix
        try:
            size = entry.stat().st_size
        except OSError as exc:
            results.append(
                CheckResult(
                    status=STATUS_FAIL,
                    category=category,
                    name=entry.name,
                    message=f"unreadable ({exc})",
                )
            )
            continue

        if ext not in _CONTEXT_ALLOWED_EXTENSIONS:
            results.append(
                CheckResult(
                    status=STATUS_WARN,
                    category=category,
                    name=entry.name,
                    message=f"ignored (extension {ext or '<none>'} not in allowed set)",
                )
            )
            continue

        total_bytes += size
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name=entry.name,
                message=_format_kb(size),
            )
        )

    total_msg = f"total: {_format_kb(total_bytes)} / {_format_kb(_CONTEXT_MAX_BYTES)} limit"
    if total_bytes > _CONTEXT_MAX_BYTES:
        results.append(
            CheckResult(
                status=STATUS_WARN,
                category=category,
                name="total",
                message=total_msg + " (exceeds limit; will be truncated)",
            )
        )
    else:
        results.append(
            CheckResult(
                status=STATUS_OK,
                category=category,
                name="total",
                message=total_msg,
            )
        )

    return results


_CATEGORY_ORDER: tuple[str, ...] = (
    "environment",
    "repository",
    "config",
    "prompts",
    "agents",
    "hooks",
    "context",
)


def run_doctor(
    *,
    cfg: Config,
    local_dir: Path | None,
) -> tuple[list[CheckResult], int]:
    results: list[CheckResult] = []
    results.extend(check_environment(cfg.claude_command))
    results.extend(check_repository(cfg))
    results.extend(check_config(local_dir))
    results.extend(check_prompts(local_dir))
    results.extend(check_agents(local_dir))
    results.extend(check_hooks(cfg.hooks_dir, cfg.hooks_enabled))
    results.extend(check_context(local_dir))

    exit_code = 1 if any(r.status == STATUS_FAIL for r in results) else 0
    return results, exit_code


_STATUS_GLYPH: dict[str, str] = {
    STATUS_OK: "✓",
    STATUS_WARN: "⚠",
    STATUS_FAIL: "✗",
}

_STATUS_STYLE: dict[str, str] = {
    STATUS_OK: "green",
    STATUS_WARN: "yellow",
    STATUS_FAIL: "red",
}


def _make_console(no_color: bool) -> Console:
    return Console(
        file=StringIO(),
        force_terminal=not no_color,
        no_color=no_color,
        width=120,
        highlight=False,
        soft_wrap=True,
        markup=False,
    )


def render(results: list[CheckResult], *, no_color: bool) -> str:
    console = _make_console(no_color)

    grouped: dict[str, list[CheckResult]] = {}
    for r in results:
        grouped.setdefault(r.category, []).append(r)

    categories: list[str] = [c for c in _CATEGORY_ORDER if c in grouped]
    for c in grouped:
        if c not in categories:
            categories.append(c)

    first = True
    for category in categories:
        section = grouped[category]
        if not first:
            console.print("")
        first = False
        console.print(category)
        name_width = max(len(r.name) for r in section)
        for r in section:
            line = Text("  ")
            line.append(_STATUS_GLYPH[r.status], style=_STATUS_STYLE[r.status])
            line.append("  ")
            line.append(r.name.ljust(name_width))
            line.append("  ")
            line.append(r.message)
            console.print(line)

    warnings = sum(1 for r in results if r.status == STATUS_WARN)
    errors = sum(1 for r in results if r.status == STATUS_FAIL)
    console.print("")
    if warnings == 0 and errors == 0:
        console.print("result: all checks passed")
    else:
        clauses: list[str] = []
        if warnings:
            clauses.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        if errors:
            clauses.append(f"{errors} error{'s' if errors != 1 else ''}")
        console.print(f"result: {', '.join(clauses)}")

    file = console.file
    assert isinstance(file, StringIO)
    return file.getvalue()
