from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ColorConfig:
    task: str = "#2e8b57"
    review: str = "#1a9e9e"
    warn: str = "#d4930d"
    error: str = "#cc0000"
    signal: str = "#d25252"
    timestamp: str = "#707070"
    info: str = "#808080"


@dataclass
class Config:
    claude_command: str = "claude"
    claude_args: str = "--dangerously-skip-permissions --verbose"
    plan_model: str = "claude-opus-4-7"
    task_model: str = "claude-opus-4-7"
    review_model: str = "claude-opus-4-7"
    iteration_delay_ms: int = 2000
    task_retry_count: int = 1
    max_iterations: int = 50
    session_timeout: str = "0"
    idle_timeout: str = "5m"
    wait_on_limit: str = "0"
    tasks_root: str = "cdc-tasks"
    default_branch: str = "main"
    init_prompt_name: str = "init"
    commit_trailer: str = ""
    commit_format: str = (
        "Format: a single line `<branch-name>. <Clause>: <what>.` where `<Clause>` is "
        "`Added`, `Changed`, or `Deleted`. English. No blank line, no multi-line body — "
        "the whole commit message is one line.\n"
        "A single commit can carry any combination of `Added`, `Changed`, and `Deleted` "
        "clauses, separated by `. ` (period + space). Within one clause, list multiple "
        "items separated by `; ` (semicolon + space). Always include only the clauses "
        "that apply.\n"
        "Each item is one short clause in plain language describing the user-visible "
        "outcome — what someone reading `git log --oneline` cares about. Implementation "
        "details (method/test/file names, renames, formatter passes, doc syncs) belong "
        "in the diff, not the commit. When squashing, write a fresh summary — do not "
        "concatenate the sub-commit messages.\n"
        "Good (single clause): `0030-chain. Added: cadence chain command runs an ordered "
        "list of tasks from a file, each on its own branch, failing fast if a task fails.`\n"
        "Good (multiple clauses, multiple items): `0014-no-plan-commit-on-start. Changed: "
        "cadence no longer auto-commits the plan file when starting a task. Deleted: "
        "now-unused commit_plan_file; file_has_changes helpers.`\n"
        "Bad (verbose, name-listing, sub-commit concat): `0014-... Changed: "
        "_prepare_plan_branch returns only branch name (drops needs_commit), "
        "create_branch_for_plan no longer auto-commits, ruff format applied, "
        "test_creates_branch_and_commits renamed to test_creates_branch_no_commit, ...`\n"
        "Author as the user — no Co-Authored-By trailer (unless `commit_trailer` is configured)."
    )
    claude_error_patterns: list[str] = field(
        default_factory=lambda: [
            "You've hit your limit",
            "API Error:",
            "cannot be launched inside another Claude Code session",
            "Not logged in",
        ]
    )
    claude_limit_patterns: list[str] = field(
        default_factory=lambda: [
            "You've hit your limit",
        ]
    )
    colors: ColorConfig = field(default_factory=ColorConfig)


_DURATION_RE = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


def parse_duration(s: str) -> float:
    s = s.strip()
    if not s or s == "0":
        return 0.0
    m = _DURATION_RE.fullmatch(s)
    if m is None or not any(m.groups()):
        raise ValueError(f"invalid duration: {s!r}")
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return float(hours * 3600 + minutes * 60 + seconds)


def load_config(config_dir: Path | None) -> Config:
    cfg = Config()
    if config_dir is None:
        return cfg

    yaml_path = config_dir / "config.yaml"
    if not yaml_path.is_file():
        return cfg

    try:
        with open(yaml_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid .cadence/config.yaml: {exc}") from exc

    if raw is None:
        data: dict[str, Any] = {}
    elif isinstance(raw, dict):
        data = raw
    else:
        raise ValueError("invalid .cadence/config.yaml: top-level must be a mapping")

    _STR_FIELDS = {
        "claude_command",
        "claude_args",
        "plan_model",
        "task_model",
        "review_model",
        "session_timeout",
        "idle_timeout",
        "wait_on_limit",
        "tasks_root",
        "default_branch",
        "init_prompt_name",
        "commit_trailer",
        "commit_format",
    }
    _INT_FIELDS = {
        "iteration_delay_ms",
        "task_retry_count",
        "max_iterations",
    }
    _LIST_FIELDS = {
        "claude_error_patterns",
        "claude_limit_patterns",
    }

    for key in _STR_FIELDS:
        if key in data:
            setattr(cfg, key, str(data[key]))

    for key in _INT_FIELDS:
        if key in data:
            setattr(cfg, key, int(data[key]))

    for key in _LIST_FIELDS:
        if key in data:
            setattr(cfg, key, [str(x) for x in data[key]])

    if "colors" in data and isinstance(data["colors"], dict):
        color_data = data["colors"]
        color_cfg = cfg.colors
        for color_key in ("task", "review", "warn", "error", "signal", "timestamp", "info"):
            if color_key in color_data:
                setattr(color_cfg, color_key, str(color_data[color_key]))

    return cfg


def detect_local_dir() -> Path | None:
    cadence_dir = Path.cwd() / ".cadence"
    if cadence_dir.is_dir():
        return cadence_dir
    return None


@dataclass
class YamlOverrides:
    plan_model: str | None = None
    task_model: str | None = None
    review_model: str | None = None
    default_branch: str | None = None


def parse_yaml_overrides(text: str | None) -> YamlOverrides:
    overrides = YamlOverrides()
    if not text:
        return overrides

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid config.yaml: {exc}") from exc

    if raw is None:
        return overrides
    if not isinstance(raw, dict):
        raise ValueError("invalid config.yaml: top-level must be a mapping")

    for section in ("plan", "task", "review"):
        value = raw.get(section)
        if not isinstance(value, dict):
            continue
        model = value.get("model")
        if not isinstance(model, str) or not model:
            continue
        if section == "plan":
            overrides.plan_model = model
        elif section == "task":
            overrides.task_model = model
        else:
            overrides.review_model = model

    default_branch = raw.get("default_branch")
    if isinstance(default_branch, str) and default_branch:
        overrides.default_branch = default_branch

    return overrides


def load_yaml_config(path: Path) -> YamlOverrides:
    text = path.read_text(encoding="utf-8")
    return parse_yaml_overrides(text)


def apply_yaml_overrides(cfg: Config, overrides: YamlOverrides) -> None:
    if overrides.plan_model is not None:
        cfg.plan_model = overrides.plan_model
    if overrides.task_model is not None:
        cfg.task_model = overrides.task_model
    if overrides.review_model is not None:
        cfg.review_model = overrides.review_model
    if overrides.default_branch is not None:
        cfg.default_branch = overrides.default_branch


def find_yaml_config(start_dir: Path) -> Path | None:
    candidate = start_dir / "config.yaml"
    if candidate.is_file():
        return candidate
    return None
