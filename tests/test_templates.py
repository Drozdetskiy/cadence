from __future__ import annotations

from pathlib import Path

import pytest

from cadence.templates import TemplateNotFoundError, load_template, render_template


class TestLoadTemplate:
    def test_returns_content_for_existing_file(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "feature.txt").write_text("hello {{name}}", encoding="utf-8")

        assert load_template(templates_dir, "feature") == "hello {{name}}"

    def test_returns_unicode_content(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "feature.txt").write_text("café — déjà vu", encoding="utf-8")

        assert load_template(templates_dir, "feature") == "café — déjà vu"

    def test_raises_with_resolved_path_when_missing(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        with pytest.raises(TemplateNotFoundError) as excinfo:
            load_template(templates_dir, "missing")

        assert excinfo.value.path == templates_dir / "missing.txt"
        assert str(templates_dir / "missing.txt") in str(excinfo.value)

    def test_template_not_found_is_file_not_found_error(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        with pytest.raises(FileNotFoundError):
            load_template(templates_dir, "missing")

    def test_raises_when_directory_does_not_exist(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "no-such-dir"

        with pytest.raises(TemplateNotFoundError) as excinfo:
            load_template(templates_dir, "feature")

        assert excinfo.value.path == templates_dir / "feature.txt"


class TestRenderTemplate:
    def test_substitutes_known_keys(self) -> None:
        content = "task={{task_name}} branch={{branch}}"
        result = render_template(content, {"task_name": "feat-x", "branch": "feat-x"})
        assert result == "task=feat-x branch=feat-x"

    def test_substitutes_multiple_occurrences_of_same_key(self) -> None:
        content = "{{name}}/{{name}}/{{name}}"
        result = render_template(content, {"name": "abc"})
        assert result == "abc/abc/abc"

    def test_leaves_unknown_placeholders_unchanged(self) -> None:
        content = "known={{name}} unknown={{foo}}"
        result = render_template(content, {"name": "abc"})
        assert result == "known=abc unknown={{foo}}"

    def test_empty_content(self) -> None:
        assert render_template("", {"name": "abc"}) == ""

    def test_empty_context(self) -> None:
        content = "no substitution {{here}}"
        assert render_template(content, {}) == content

    def test_empty_content_and_empty_context(self) -> None:
        assert render_template("", {}) == ""

    def test_substitutes_with_empty_value(self) -> None:
        content = "author={{author}}!"
        assert render_template(content, {"author": ""}) == "author=!"
