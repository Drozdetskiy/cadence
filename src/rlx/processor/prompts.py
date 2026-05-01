from __future__ import annotations

import importlib.resources
from pathlib import Path


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

    ref = importlib.resources.files("rlx.defaults.prompts").joinpath(
        f"{name}.txt"
    )
    content = ref.read_text(encoding="utf-8")
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


def append_commit_trailer_instruction(
    prompt: str, commit_trailer: str
) -> str:
    if not commit_trailer:
        return prompt
    instruction = (
        "\n\nWhen making git commits, add the following trailer "
        "to each commit message:\n"
        f"{commit_trailer}"
    )
    return prompt + instruction


_PLAN_DESC_PLACEHOLDER = "{{PLAN_DESCRIPTION}}"


def build_plan_prompt(
    plan_description: str,
    *,
    local_dir: Path | None = None,
    plan_file: str = "",
    progress_file: str = "",
    default_branch: str = "",
    plans_dir: str = "",
    commit_trailer: str = "",
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
    prompt = append_commit_trailer_instruction(prompt, commit_trailer)
    return prompt
