from __future__ import annotations

import importlib.resources
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentDef:
    name: str
    body: str
    model: str = ""
    agent_type: str = "general-purpose"


_ALLOWED_MODELS: tuple[str, ...] = ("opus", "sonnet", "haiku")
_DEFAULT_AGENT_TYPE = "general-purpose"


def _normalize_model(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    lowered = stripped.lower()
    if lowered in _ALLOWED_MODELS:
        return lowered
    for candidate in _ALLOWED_MODELS:
        if candidate in lowered:
            return candidate
    return ""


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {}, text
    end_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\r\n") == "---":
            end_idx = idx
            break
    if end_idx == -1:
        return {}, text
    fields: dict[str, str] = {}
    for raw_line in lines[1:end_idx]:
        line = raw_line.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("#"):
            value = ""
        else:
            hash_idx = value.find(" #")
            if hash_idx != -1:
                value = value[:hash_idx].strip()
            value = value.strip().strip("'\"")
        if key:
            fields[key] = value
    body = "".join(lines[end_idx + 1 :])
    return fields, body


def load_agent(
    name: str,
    *,
    local_dir: Path | None = None,
    warn: Callable[[str], None] | None = None,
) -> AgentDef | None:
    raw: str | None = None
    if local_dir is not None:
        local_path = local_dir / "agents" / f"{name}.txt"
        if local_path.is_file():
            raw = local_path.read_text(encoding="utf-8")
    if raw is None:
        ref = importlib.resources.files("rlx.defaults.agents").joinpath(f"{name}.txt")
        if not ref.is_file():
            return None
        raw = ref.read_text(encoding="utf-8")

    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    fields, body = _parse_frontmatter(raw)

    model_raw = fields.get("model", "")
    model = ""
    if model_raw:
        normalized = _normalize_model(model_raw)
        if normalized:
            model = normalized
        else:
            if warn is not None:
                warn(
                    f"invalid model {model_raw!r} for agent {name}, ignoring"
                )

    agent_type = fields.get("agent", "").strip() or _DEFAULT_AGENT_TYPE

    return AgentDef(name=name, body=body, model=model, agent_type=agent_type)


__all__ = ["AgentDef", "load_agent"]
