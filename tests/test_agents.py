from __future__ import annotations

from pathlib import Path

import pytest

from cadence.processor.agents import AgentDef, load_agent


class TestLoadAgentFallback:
    def test_local_overrides_embedded(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "quality.txt").write_text("LOCAL OVERRIDE BODY")

        result = load_agent("quality", local_dir=tmp_path)

        assert result is not None
        assert result.name == "quality"
        assert result.body == "LOCAL OVERRIDE BODY"
        assert result.model == ""
        assert result.agent_type == "general-purpose"

    def test_embedded_when_no_local(self, tmp_path: Path) -> None:
        result = load_agent("quality", local_dir=tmp_path)

        assert result is not None
        assert result.name == "quality"
        assert "Review code for bugs, security issues" in result.body

    def test_local_dir_none_uses_embedded(self) -> None:
        result = load_agent("implementation")

        assert result is not None
        assert "Review whether the implementation" in result.body

    def test_missing_agent_raises_diagnostic(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError) as exc_info:
            load_agent("nonexistent-agent", local_dir=tmp_path)
        message = str(exc_info.value)
        assert "nonexistent-agent" in message
        assert "agent" in message
        assert "cadence" in message
        assert "reinstall" in message
        assert "pip install" in message
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)

    def test_missing_embedded_agent_raises_diagnostic(self) -> None:
        with pytest.raises(RuntimeError) as exc_info:
            load_agent("definitely-not-a-real-agent")
        message = str(exc_info.value)
        assert "definitely-not-a-real-agent" in message
        assert "agent" in message
        assert "cadence" in message
        assert "reinstall" in message
        assert "pip install" in message
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)


class TestFrontmatter:
    def test_frontmatter_parsed(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "quality.txt").write_text(
            "---\nmodel: sonnet\nagent: code-reviewer\n---\nThe actual body.\n"
        )

        result = load_agent("quality", local_dir=tmp_path)

        assert result is not None
        assert result.model == "sonnet"
        assert result.agent_type == "code-reviewer"
        assert result.body == "The actual body.\n"

    def test_no_frontmatter_defaults(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "quality.txt").write_text("Just a body with no frontmatter.")

        result = load_agent("quality", local_dir=tmp_path)

        assert result is not None
        assert result.model == ""
        assert result.agent_type == "general-purpose"
        assert result.body == "Just a body with no frontmatter."

    def test_malformed_frontmatter_kept_as_body(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "quality.txt").write_text("---\nmodel: sonnet\nbody without closing fence\n")

        result = load_agent("quality", local_dir=tmp_path)

        assert result is not None
        assert result.model == ""
        assert result.agent_type == "general-purpose"
        assert result.body.startswith("---")


class TestModelNormalization:
    def test_long_sonnet_id_normalized(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "x.txt").write_text("---\nmodel: claude-sonnet-4-5-20250929\n---\nbody\n")

        result = load_agent("x", local_dir=tmp_path)

        assert result is not None
        assert result.model == "sonnet"

    def test_long_haiku_id_normalized(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "x.txt").write_text("---\nmodel: claude-haiku-4-5-20251001\n---\nbody\n")

        result = load_agent("x", local_dir=tmp_path)

        assert result is not None
        assert result.model == "haiku"

    def test_long_opus_id_normalized(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "x.txt").write_text("---\nmodel: claude-opus-4-7\n---\nbody\n")

        result = load_agent("x", local_dir=tmp_path)

        assert result is not None
        assert result.model == "opus"

    def test_invalid_model_warns_and_drops(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "x.txt").write_text("---\nmodel: gpt-4\n---\nthe body\n")

        warnings: list[str] = []
        result = load_agent("x", local_dir=tmp_path, warn=lambda msg: warnings.append(msg))

        assert result is not None
        assert result.model == ""
        assert result.body == "the body\n"
        assert len(warnings) == 1
        assert "gpt-4" in warnings[0]
        assert "x" in warnings[0]

    def test_short_names_accepted(self, tmp_path: Path) -> None:
        for model_name in ("haiku", "sonnet", "opus"):
            agents_dir = tmp_path / f"{model_name}-agents" / "agents"
            agents_dir.mkdir(parents=True)
            (agents_dir / "x.txt").write_text(f"---\nmodel: {model_name}\n---\nbody\n")
            result = load_agent("x", local_dir=tmp_path / f"{model_name}-agents")
            assert result is not None
            assert result.model == model_name


class TestEmbeddedDefaults:
    def test_quality_body_loads(self) -> None:
        result = load_agent("quality")
        assert result is not None
        assert result.body.startswith(
            "Review code for bugs, security issues, and quality problems."
        )
        assert result.model == ""
        assert result.agent_type == "general-purpose"

    def test_implementation_body_loads(self) -> None:
        result = load_agent("implementation")
        assert result is not None
        assert result.body.startswith("Review whether the implementation achieves the stated goal")

    def test_testing_body_loads(self) -> None:
        result = load_agent("testing")
        assert result is not None
        assert result.body.startswith("Review test coverage and quality.")

    def test_simplification_body_loads(self) -> None:
        result = load_agent("simplification")
        assert result is not None
        assert result.body.startswith("Detect over-engineered and overcomplicated code")

    @pytest.mark.parametrize(
        "name",
        [
            "quality",
            "implementation",
            "testing",
            "simplification",
        ],
    )
    def test_all_shipped_agents_load(self, name: str) -> None:
        result = load_agent(name)
        assert result is not None, f"shipped agent {name!r} returned None"
        assert result.body.strip(), f"shipped agent {name!r} loaded but body is empty"


class TestAgentDef:
    def test_is_frozen(self) -> None:
        agent = AgentDef(name="x", body="b")
        try:
            agent.body = "new"  # type: ignore[misc]
        except Exception:
            return
        raise AssertionError("AgentDef should be frozen")
