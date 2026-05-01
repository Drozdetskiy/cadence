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
    plans_dir: str = "",
) -> str:
    pf = plan_file or "(no plan file - reviewing current branch)"
    pr = progress_file or "(no progress file available)"
    gl = goal or f"current branch vs {default_branch or 'master'}"
    db = default_branch or "master"
    pd = plans_dir or "docs/plans"

    prompt = prompt.replace("{{PLAN_FILE}}", pf)
    prompt = prompt.replace("{{PROGRESS_FILE}}", pr)
    prompt = prompt.replace("{{GOAL}}", gl)
    prompt = prompt.replace("{{DEFAULT_BRANCH}}", db)
    prompt = prompt.replace("{{PLANS_DIR}}", pd)
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


_PLAN_DESC_PLACEHOLDER = "{{PLAN_DESCRIPTION}}"
_AGENT_REF_RE = re.compile(r"\{\{agent:([a-zA-Z0-9_-]+)\}\}")


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
        return format_agent_expansion(body, model=agent.model, agent_type=agent.agent_type)

    return _AGENT_REF_RE.sub(_sub, prompt)


def replace_prompt_variables(
    prompt: str,
    *,
    plan_file: str,
    progress_file: str,
    goal: str,
    default_branch: str,
    plans_dir: str,
    commit_trailer: str,
    local_dir: Path | None,
    warn: Callable[[str], None] | None = None,
) -> str:
    base_vars: dict[str, str] = {
        "plan_file": plan_file,
        "progress_file": progress_file,
        "goal": goal,
        "default_branch": default_branch,
        "plans_dir": plans_dir,
    }
    prompt = replace_base_variables(prompt, **base_vars)
    prompt = expand_agent_references(prompt, local_dir=local_dir, warn=warn, base_vars=base_vars)
    prompt = append_commit_trailer_instruction(prompt, commit_trailer)
    return prompt


def build_plan_prompt(
    plan_description: str,
    *,
    local_dir: Path | None = None,
    plan_file: str = "",
    progress_file: str = "",
    default_branch: str = "",
    plans_dir: str = "",
    commit_trailer: str = "",
    derived_plan_path: str = "",
) -> str:
    prompt = load_prompt("make_plan", local_dir=local_dir)
    prompt = replace_base_variables(
        prompt,
        plan_file=plan_file,
        progress_file=progress_file,
        default_branch=default_branch,
        plans_dir=plans_dir,
    )
    prompt = prompt.replace(_PLAN_DESC_PLACEHOLDER, plan_description)
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
    return prompt


def _review_goal(plan_file: str, default_branch: str) -> str:
    if plan_file:
        return f"implementation of plan at {plan_file}"
    return f"review of branch vs {default_branch or 'master'}"


def build_review_first_prompt(
    *,
    local_dir: Path | None = None,
    plan_file: str = "",
    progress_file: str = "",
    default_branch: str = "",
    plans_dir: str = "",
    commit_trailer: str = "",
    warn: Callable[[str], None] | None = None,
) -> str:
    prompt = load_prompt("review_first", local_dir=local_dir)
    return replace_prompt_variables(
        prompt,
        plan_file=plan_file,
        progress_file=progress_file,
        goal=_review_goal(plan_file, default_branch),
        default_branch=default_branch,
        plans_dir=plans_dir,
        commit_trailer=commit_trailer,
        local_dir=local_dir,
        warn=warn,
    )


def build_review_second_prompt(
    *,
    local_dir: Path | None = None,
    plan_file: str = "",
    progress_file: str = "",
    default_branch: str = "",
    plans_dir: str = "",
    commit_trailer: str = "",
    warn: Callable[[str], None] | None = None,
) -> str:
    prompt = load_prompt("review_second", local_dir=local_dir)
    return replace_prompt_variables(
        prompt,
        plan_file=plan_file,
        progress_file=progress_file,
        goal=_review_goal(plan_file, default_branch),
        default_branch=default_branch,
        plans_dir=plans_dir,
        commit_trailer=commit_trailer,
        local_dir=local_dir,
        warn=warn,
    )


def build_finalize_prompt(
    *,
    local_dir: Path | None = None,
    plan_file: str = "",
    progress_file: str = "",
    default_branch: str = "",
    plans_dir: str = "",
    commit_trailer: str = "",
    warn: Callable[[str], None] | None = None,
) -> str:
    prompt = load_prompt("finalize", local_dir=local_dir)
    return replace_prompt_variables(
        prompt,
        plan_file=plan_file,
        progress_file=progress_file,
        goal=_review_goal(plan_file, default_branch),
        default_branch=default_branch,
        plans_dir=plans_dir,
        commit_trailer=commit_trailer,
        local_dir=local_dir,
        warn=warn,
    )


__all__ = [
    "append_commit_trailer_instruction",
    "build_finalize_prompt",
    "build_plan_prompt",
    "build_review_first_prompt",
    "build_review_second_prompt",
    "build_task_prompt",
    "expand_agent_references",
    "format_agent_expansion",
    "load_prompt",
    "normalize_crlf",
    "replace_base_variables",
    "replace_prompt_variables",
    "strip_comments",
    "strip_leading_comments",
]
