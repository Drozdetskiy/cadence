from __future__ import annotations

import importlib.resources
import re
from collections.abc import Callable
from pathlib import Path

from cadence.processor.agents import load_agent


def normalize_crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_comments(text: str) -> str:
    lines = text.splitlines()
    kept = [line for line in lines if not line.lstrip().startswith("#")]
    return "\n".join(kept).strip()


def strip_leading_comments(text: str) -> str:
    lines = text.splitlines(keepends=True)
    comment_count = 0
    for line in lines:
        if line.lstrip().startswith("#"):
            comment_count += 1
        else:
            break
    if comment_count >= 2:
        lines = lines[comment_count:]
    result = "".join(lines)
    return result.lstrip("\n")


def load_prompt(name: str, local_dir: Path | None = None) -> str:
    if local_dir is not None:
        local_path = local_dir / "prompts" / f"{name}.txt"
        if local_path.is_file():
            raw = normalize_crlf(local_path.read_text(encoding="utf-8"))
            if not strip_comments(raw):
                pass
            else:
                return strip_leading_comments(raw)

    ref = importlib.resources.files("cadence.defaults.prompts").joinpath(f"{name}.txt")
    try:
        content = ref.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"default prompt {name!r} not found in installed cadence "
            "package; the install may be incomplete or out of date — "
            "reinstall with 'pip install -e .'"
        ) from exc
    return normalize_crlf(content)


def replace_base_variables(
    prompt: str,
    *,
    plan_file: str = "",
    progress_file: str = "",
    goal: str = "",
    default_branch: str = "",
) -> str:
    pf = plan_file or "(no plan file - reviewing current branch)"
    pr = progress_file or "(no progress file available)"
    gl = goal or f"current branch vs {default_branch or 'main'}"
    db = default_branch or "main"

    prompt = prompt.replace("{{PLAN_FILE}}", pf)
    prompt = prompt.replace("{{PROGRESS_FILE}}", pr)
    prompt = prompt.replace("{{GOAL}}", gl)
    prompt = prompt.replace("{{DEFAULT_BRANCH}}", db)
    return prompt


def append_commit_trailer_instruction(prompt: str, commit_trailer: str) -> str:
    if not commit_trailer:
        return prompt
    instruction = (
        "\n\nWhen making git commits, add the following trailer "
        "to each commit message:\n"
        f"{commit_trailer}"
    )
    return prompt + instruction


COMMIT_FORMAT_SENTINEL = "Format every git commit message using these rules:"


def append_commit_format_instruction(prompt: str, commit_format: str) -> str:
    if not commit_format:
        return prompt
    instruction = f"\n\n{COMMIT_FORMAT_SENTINEL}\n{commit_format}"
    return prompt + instruction


_PLAN_DESC_PLACEHOLDER = "{{PLAN_DESCRIPTION}}"
_AGENT_REF_RE = re.compile(r"\{\{agent:([a-zA-Z0-9_-]+)\}\}")

_IMPORT_PRECEDENCE_NOTE = (
    "`# Task brief (init)` is the authoritative source; `# External brief` is "
    "supplementary context — prefer init when they conflict."
)


def _compose_plan_description(
    plan_description: str,
    imported_brief: str | None,
    imported_brief_source: str,
) -> str:
    if imported_brief is None:
        return plan_description
    external_heading = f"# External brief (imported from {imported_brief_source})"
    if not plan_description:
        return f"{external_heading}\n\n{imported_brief}"
    return (
        f"# Task brief (init)\n\n{plan_description}\n\n"
        f"{external_heading}\n\n{imported_brief}\n\n"
        f"{_IMPORT_PRECEDENCE_NOTE}"
    )


def format_agent_expansion(prompt_body: str, *, model: str, agent_type: str) -> str:
    model_clause = f" with model={model}" if model else ""
    return (
        f"Use the Task tool{model_clause} to launch a {agent_type} agent "
        f"with the prompt below (between the BEGIN/END markers):\n"
        f"<<<AGENT_PROMPT BEGIN>>>\n"
        f"{prompt_body}\n"
        f"<<<AGENT_PROMPT END>>>\n\n"
        f"Report findings only - no positive observations."
    )


def expand_agent_references(
    prompt: str,
    *,
    local_dir: Path | None,
    warn: Callable[[str], None] | None,
    base_vars: dict[str, str],
    agent_models: dict[str, str] | None = None,
) -> str:
    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        try:
            agent = load_agent(name, local_dir=local_dir, warn=warn)
        except RuntimeError as exc:
            if warn is not None:
                warn(str(exc))
            return match.group(0)
        body = replace_base_variables(agent.body, **base_vars)
        model = agent.model
        if agent_models:
            override = agent_models.get(name)
            if override:
                model = override
        return format_agent_expansion(body, model=model, agent_type=agent.agent_type)

    return _AGENT_REF_RE.sub(_sub, prompt)


def replace_prompt_variables(
    prompt: str,
    *,
    plan_file: str,
    progress_file: str,
    goal: str,
    default_branch: str,
    commit_trailer: str,
    local_dir: Path | None,
    warn: Callable[[str], None] | None = None,
    commit_format: str = "",
    agent_models: dict[str, str] | None = None,
) -> str:
    base_vars: dict[str, str] = {
        "plan_file": plan_file,
        "progress_file": progress_file,
        "goal": goal,
        "default_branch": default_branch,
    }
    prompt = replace_base_variables(prompt, **base_vars)
    prompt = expand_agent_references(
        prompt,
        local_dir=local_dir,
        warn=warn,
        base_vars=base_vars,
        agent_models=agent_models,
    )
    prompt = append_commit_trailer_instruction(prompt, commit_trailer)
    prompt = append_commit_format_instruction(prompt, commit_format)
    return prompt


def build_plan_prompt(
    plan_description: str,
    *,
    local_dir: Path | None = None,
    plan_file: str = "",
    progress_file: str = "",
    default_branch: str = "",
    commit_trailer: str = "",
    derived_plan_path: str = "",
    imported_brief: str | None = None,
    imported_brief_source: str = "",
) -> str:
    prompt = load_prompt("make_plan", local_dir=local_dir)
    prompt = replace_base_variables(
        prompt,
        plan_file=plan_file,
        progress_file=progress_file,
        default_branch=default_branch,
    )
    composed = _compose_plan_description(plan_description, imported_brief, imported_brief_source)
    prompt = prompt.replace(_PLAN_DESC_PLACEHOLDER, composed)
    prompt = prompt.replace(
        "{{DERIVED_PLAN_PATH}}",
        derived_plan_path or "(next to the prompt file)",
    )
    prompt = append_commit_trailer_instruction(prompt, commit_trailer)
    return prompt


def build_task_prompt(
    *,
    local_dir: Path | None = None,
    plan_file: str = "",
    progress_file: str = "",
    default_branch: str = "",
    commit_trailer: str = "",
    commit_format: str = "",
) -> str:
    prompt = load_prompt("task", local_dir=local_dir)
    goal = f"implementation of plan at {plan_file}"
    prompt = replace_base_variables(
        prompt,
        plan_file=plan_file,
        progress_file=progress_file,
        goal=goal,
        default_branch=default_branch,
    )
    prompt = append_commit_trailer_instruction(prompt, commit_trailer)
    prompt = append_commit_format_instruction(prompt, commit_format)
    return prompt


def _review_goal(plan_file: str, default_branch: str) -> str:
    if plan_file:
        return f"implementation of plan at {plan_file}"
    return f"review of branch vs {default_branch or 'main'}"


_CONTEXT_ALLOWED_EXTENSIONS = frozenset({".md", ".txt", ".sql", ".yaml", ".yml", ".json", ".proto"})


def load_context_files(
    local_dir: Path | None,
    *,
    max_bytes: int = 200_000,
    warn: Callable[[str], None] | None = None,
) -> str:
    if local_dir is None:
        return ""
    ctx_dir = local_dir / "context"
    if not ctx_dir.is_dir():
        return ""

    entries: list[Path] = []
    for entry in sorted(ctx_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        if entry.suffix not in _CONTEXT_ALLOWED_EXTENSIONS:
            continue
        entries.append(entry)

    if not entries:
        return ""

    parts: list[str] = []
    total = 0
    included = 0
    for entry in entries:
        content = entry.read_text(encoding="utf-8", errors="replace")
        encoded_size = len(content.encode("utf-8"))
        if total + encoded_size > max_bytes:
            break
        total += encoded_size
        parts.append(f"## {entry.name}\n{content}\n\n")
        included += 1

    skipped = len(entries) - included
    if skipped > 0 and warn is not None:
        warn(f"context: dropped {skipped} file(s) over {max_bytes}-byte limit")

    if not parts:
        return ""
    return "# Project context\n\n" + "".join(parts)


def _format_public_api_paths(paths: list[str]) -> str:
    if not paths:
        return "(infer from project structure)"
    return "- " + "\n- ".join(paths)


def build_report_api_changes_prompt(
    *,
    local_dir: Path | None,
    branch: str,
    default_branch: str,
    public_api_paths: list[str],
    progress_file: str = "",
    commit_format: str = "",
    warn: Callable[[str], None] | None = None,
) -> str:
    prompt = load_prompt("report_api_changes", local_dir=local_dir)
    prompt = prompt.replace("{{BRANCH}}", branch)
    prompt = prompt.replace("{{DEFAULT_BRANCH}}", default_branch or "main")
    prompt = prompt.replace("{{PROGRESS_FILE}}", progress_file)
    prompt = prompt.replace("{{PUBLIC_API_PATHS}}", _format_public_api_paths(public_api_paths))
    prompt = prompt.replace("{{PROJECT_CONTEXT}}", load_context_files(local_dir, warn=warn))
    prompt = append_commit_format_instruction(prompt, commit_format)
    return prompt


def build_report_test_cases_prompt(
    *,
    local_dir: Path | None,
    branch: str,
    default_branch: str,
    progress_file: str = "",
    commit_format: str = "",
    warn: Callable[[str], None] | None = None,
) -> str:
    prompt = load_prompt("report_test_cases", local_dir=local_dir)
    prompt = prompt.replace("{{BRANCH}}", branch)
    prompt = prompt.replace("{{DEFAULT_BRANCH}}", default_branch or "main")
    prompt = prompt.replace("{{PROGRESS_FILE}}", progress_file)
    prompt = prompt.replace("{{PROJECT_CONTEXT}}", load_context_files(local_dir, warn=warn))
    prompt = append_commit_format_instruction(prompt, commit_format)
    return prompt


def build_squash_commit_prompt(
    *,
    local_dir: Path | None = None,
    default_branch: str = "",
    commit_format: str = "",
) -> str:
    prompt = load_prompt("squash_commit", local_dir=local_dir)
    prompt = replace_base_variables(prompt, default_branch=default_branch)
    prompt = append_commit_format_instruction(prompt, commit_format)
    return prompt


def build_review_first_prompt(
    *,
    local_dir: Path | None = None,
    plan_file: str = "",
    progress_file: str = "",
    default_branch: str = "",
    commit_trailer: str = "",
    warn: Callable[[str], None] | None = None,
    commit_format: str = "",
    agent_models: dict[str, str] | None = None,
) -> str:
    prompt = load_prompt("review_first", local_dir=local_dir)
    return replace_prompt_variables(
        prompt,
        plan_file=plan_file,
        progress_file=progress_file,
        goal=_review_goal(plan_file, default_branch),
        default_branch=default_branch,
        commit_trailer=commit_trailer,
        local_dir=local_dir,
        warn=warn,
        commit_format=commit_format,
        agent_models=agent_models,
    )


def build_review_second_prompt(
    *,
    local_dir: Path | None = None,
    plan_file: str = "",
    progress_file: str = "",
    default_branch: str = "",
    commit_trailer: str = "",
    warn: Callable[[str], None] | None = None,
    commit_format: str = "",
    agent_models: dict[str, str] | None = None,
) -> str:
    prompt = load_prompt("review_second", local_dir=local_dir)
    return replace_prompt_variables(
        prompt,
        plan_file=plan_file,
        progress_file=progress_file,
        goal=_review_goal(plan_file, default_branch),
        default_branch=default_branch,
        commit_trailer=commit_trailer,
        local_dir=local_dir,
        warn=warn,
        commit_format=commit_format,
        agent_models=agent_models,
    )


__all__ = [
    "COMMIT_FORMAT_SENTINEL",
    "append_commit_format_instruction",
    "append_commit_trailer_instruction",
    "build_plan_prompt",
    "build_report_api_changes_prompt",
    "build_report_test_cases_prompt",
    "build_review_first_prompt",
    "build_review_second_prompt",
    "build_squash_commit_prompt",
    "build_task_prompt",
    "expand_agent_references",
    "format_agent_expansion",
    "load_context_files",
    "load_prompt",
    "normalize_crlf",
    "replace_base_variables",
    "replace_prompt_variables",
    "strip_comments",
    "strip_leading_comments",
]
