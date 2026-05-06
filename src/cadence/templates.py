from __future__ import annotations

from pathlib import Path


class TemplateNotFoundError(FileNotFoundError):
    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path

    def __str__(self) -> str:
        return f"template not found at {self.path}"


def load_template(templates_dir: Path, name: str) -> str:
    path = templates_dir / f"{name}.txt"
    if not path.is_file():
        raise TemplateNotFoundError(path)
    return path.read_text(encoding="utf-8")


def render_template(content: str, context: dict[str, str]) -> str:
    rendered = content
    for key, value in context.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered
