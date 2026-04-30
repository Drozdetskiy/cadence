from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


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
    claude_args: str = "--dangerously-skip-permissions --output-format stream-json --verbose"
    plan_model: str = "opus4.7"
    task_model: str = "opus4.7"
    review_model: str = "opus4.7"
    iteration_delay_ms: int = 2000
    task_retry_count: int = 1
    max_iterations: int = 50
    session_timeout: str = "0"
    idle_timeout: str = "5m"
    wait_on_limit: str = "0"
    finalize_enabled: bool = False
    plans_dir: str = "docs/plans"
    default_branch: str = ""
    vcs_command: str = "git"
    commit_trailer: str = ""
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

    toml_path = config_dir / "config.toml"
    if not toml_path.is_file():
        return cfg

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    _STR_FIELDS = {
        "claude_command",
        "claude_args",
        "plan_model",
        "task_model",
        "review_model",
        "session_timeout",
        "idle_timeout",
        "wait_on_limit",
        "plans_dir",
        "default_branch",
        "vcs_command",
        "commit_trailer",
    }
    _INT_FIELDS = {
        "iteration_delay_ms",
        "task_retry_count",
        "max_iterations",
    }
    _BOOL_FIELDS = {
        "finalize_enabled",
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

    for key in _BOOL_FIELDS:
        if key in data:
            setattr(cfg, key, bool(data[key]))

    for key in _LIST_FIELDS:
        if key in data:
            setattr(cfg, key, [str(x) for x in data[key]])

    if "colors" in data and isinstance(data["colors"], dict):
        color_data = data["colors"]
        color_cfg = cfg.colors
        for color_key in ("task", "review", "warn", "error", "signal", "timestamp", "info"):
            if color_key in color_data:
                setattr(color_cfg, color_key, str(color_data[color_key]))

    if cfg.vcs_command.startswith("~"):
        cfg.vcs_command = os.path.expanduser(cfg.vcs_command)

    return cfg


def detect_local_dir() -> Path | None:
    rlx_dir = Path.cwd() / ".rlx"
    if rlx_dir.is_dir():
        return rlx_dir
    return None
