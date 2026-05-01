from __future__ import annotations

from pathlib import Path

import pytest

from cadence.config import (
    ColorConfig,
    Config,
    YamlOverrides,
    apply_yaml_overrides,
    detect_local_dir,
    find_yaml_config,
    load_config,
    load_yaml_config,
    parse_duration,
    parse_yaml_overrides,
)


class TestColorConfig:
    def test_defaults(self) -> None:
        c = ColorConfig()
        assert c.task == "#2e8b57"
        assert c.review == "#1a9e9e"
        assert c.warn == "#d4930d"
        assert c.error == "#cc0000"
        assert c.signal == "#d25252"
        assert c.timestamp == "#707070"
        assert c.info == "#808080"


class TestConfigDefaults:
    def test_all_defaults(self) -> None:
        cfg = Config()
        assert cfg.claude_command == "claude"
        assert cfg.claude_args == (
            "--dangerously-skip-permissions --output-format stream-json --verbose"
        )
        assert cfg.plan_model == "claude-opus-4-7"
        assert cfg.task_model == "claude-opus-4-7"
        assert cfg.review_model == "claude-opus-4-7"
        assert cfg.iteration_delay_ms == 2000
        assert cfg.task_retry_count == 1
        assert cfg.max_iterations == 50
        assert cfg.session_timeout == "0"
        assert cfg.idle_timeout == "5m"
        assert cfg.wait_on_limit == "0"
        assert cfg.finalize_enabled is False
        assert cfg.plans_dir == "docs/plans"
        assert cfg.default_branch == ""
        assert cfg.vcs_command == "git"
        assert cfg.commit_trailer == ""
        assert "You've hit your limit" in cfg.claude_error_patterns
        assert "API Error:" in cfg.claude_error_patterns
        assert "You've hit your limit" in cfg.claude_limit_patterns
        assert isinstance(cfg.colors, ColorConfig)


class TestParseDuration:
    def test_zero(self) -> None:
        assert parse_duration("0") == 0.0

    def test_empty(self) -> None:
        assert parse_duration("") == 0.0

    def test_seconds(self) -> None:
        assert parse_duration("90s") == 90.0

    def test_minutes(self) -> None:
        assert parse_duration("30m") == 1800.0

    def test_hours(self) -> None:
        assert parse_duration("1h") == 3600.0

    def test_compound_hm(self) -> None:
        assert parse_duration("1h30m") == 5400.0

    def test_compound_hms(self) -> None:
        assert parse_duration("1h30m15s") == 5415.0

    def test_compound_ms(self) -> None:
        assert parse_duration("5m30s") == 330.0

    def test_whitespace_stripped(self) -> None:
        assert parse_duration("  30m  ") == 1800.0

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid duration"):
            parse_duration("abc")

    def test_no_units_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid duration"):
            parse_duration("123")


class TestLoadConfig:
    def test_no_config_dir(self) -> None:
        cfg = load_config(None)
        assert cfg.claude_command == "claude"

    def test_missing_yaml(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path)
        assert cfg.max_iterations == 50

    def test_load_string_fields(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("claude_command: my-claude\nplans_dir: my-plans\n")
        cfg = load_config(tmp_path)
        assert cfg.claude_command == "my-claude"
        assert cfg.plans_dir == "my-plans"
        assert cfg.task_model == "claude-opus-4-7"

    def test_load_int_fields(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("max_iterations: 100\niteration_delay_ms: 500\n")
        cfg = load_config(tmp_path)
        assert cfg.max_iterations == 100
        assert cfg.iteration_delay_ms == 500

    def test_load_bool_fields(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("finalize_enabled: true\n")
        cfg = load_config(tmp_path)
        assert cfg.finalize_enabled is True

    def test_load_list_fields(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text('claude_error_patterns:\n  - "custom error"\n')
        cfg = load_config(tmp_path)
        assert cfg.claude_error_patterns == ["custom error"]

    def test_load_colors(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text('colors:\n  task: "#ff0000"\n  warn: "#00ff00"\n')
        cfg = load_config(tmp_path)
        assert cfg.colors.task == "#ff0000"
        assert cfg.colors.warn == "#00ff00"
        assert cfg.colors.review == "#1a9e9e"

    def test_absent_keys_keep_defaults(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("claude_command: custom\n")
        cfg = load_config(tmp_path)
        assert cfg.claude_command == "custom"
        assert cfg.max_iterations == 50
        assert cfg.finalize_enabled is False

    def test_tilde_expansion(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text('vcs_command: "~/bin/git"\n')
        cfg = load_config(tmp_path)
        assert not cfg.vcs_command.startswith("~")
        assert cfg.vcs_command.endswith("/bin/git")

    def test_invalid_yaml_raises_value_error(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("claude_command: 'unterminated\n")
        with pytest.raises(ValueError, match=r"invalid \.cadence/config\.yaml"):
            load_config(tmp_path)

    def test_empty_yaml_keeps_defaults(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("# only a comment\n")
        cfg = load_config(tmp_path)
        assert cfg.claude_command == "claude"
        assert cfg.max_iterations == 50

    def test_top_level_not_mapping_raises(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("- 1\n- 2\n")
        with pytest.raises(
            ValueError,
            match=r"invalid \.cadence/config\.yaml: top-level must be a mapping",
        ):
            load_config(tmp_path)


class TestParseYamlOverrides:
    def test_empty_text(self) -> None:
        overrides = parse_yaml_overrides("")
        assert overrides == YamlOverrides()

    def test_blank_and_comments_only(self) -> None:
        text = "\n# top-level comment\n\n   # indented comment\n\n"
        overrides = parse_yaml_overrides(text)
        assert overrides == YamlOverrides()

    def test_all_three_modes(self) -> None:
        text = (
            "plan:\n"
            "  model: opus\n"
            "task:\n"
            "  model: sonnet\n"
            "review:\n"
            "  model: haiku\n"
        )
        overrides = parse_yaml_overrides(text)
        assert overrides.plan_model == "opus"
        assert overrides.task_model == "sonnet"
        assert overrides.review_model == "haiku"

    def test_only_one_mode(self) -> None:
        text = "task:\n  model: sonnet\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.plan_model is None
        assert overrides.task_model == "sonnet"
        assert overrides.review_model is None

    def test_double_quoted_value(self) -> None:
        text = 'task:\n  model: "sonnet4.5"\n'
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model == "sonnet4.5"

    def test_single_quoted_value(self) -> None:
        text = "review:\n  model: 'haiku'\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.review_model == "haiku"

    def test_inline_comment_after_value(self) -> None:
        text = "task:\n  model: sonnet # this is the model\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model == "sonnet"

    def test_extra_whitespace_tolerated(self) -> None:
        text = "task:   \n     model:    sonnet   \n"
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model == "sonnet"

    def test_unknown_top_level_key_ignored(self) -> None:
        text = (
            "unknown:\n"
            "  model: ignored\n"
            "task:\n"
            "  model: sonnet\n"
        )
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model == "sonnet"
        assert overrides.plan_model is None
        assert overrides.review_model is None

    def test_unknown_nested_key_ignored(self) -> None:
        text = (
            "task:\n"
            "  model: sonnet\n"
            "  temperature: 0.5\n"
        )
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model == "sonnet"

    def test_empty_value_does_not_override(self) -> None:
        text = "task:\n  model:\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model is None

    def test_value_containing_colon(self) -> None:
        text = 'task:\n  model: "org:opus"\n'
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model == "org:opus"

    def test_malformed_no_colon_raises(self) -> None:
        with pytest.raises(ValueError, match=r"invalid cadence-config\.yaml"):
            parse_yaml_overrides("task:\n  model: 'unterminated\n")

    def test_malformed_top_level_scalar_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"invalid cadence-config\.yaml: top-level must be a mapping",
        ):
            parse_yaml_overrides("- 1\n- 2\n")

    def test_section_with_scalar_value_silently_ignored(self) -> None:
        overrides = parse_yaml_overrides("task: sonnet\n")
        assert overrides == YamlOverrides()


class TestLoadYamlConfig:
    def test_loads_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "cadence-config.yaml"
        path.write_text("plan:\n  model: opus\n")
        overrides = load_yaml_config(path)
        assert overrides.plan_model == "opus"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_yaml_config(tmp_path / "nope.yaml")

    def test_malformed_file_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "cadence-config.yaml"
        path.write_text("not valid yaml without colon\n")
        with pytest.raises(ValueError, match=r"invalid cadence-config\.yaml"):
            load_yaml_config(path)


class TestApplyYamlOverrides:
    def test_only_specified_fields_change(self) -> None:
        cfg = Config(plan_model="P", task_model="T", review_model="R")
        apply_yaml_overrides(cfg, YamlOverrides(task_model="newT"))
        assert cfg.plan_model == "P"
        assert cfg.task_model == "newT"
        assert cfg.review_model == "R"

    def test_all_fields_override(self) -> None:
        cfg = Config(plan_model="P", task_model="T", review_model="R")
        apply_yaml_overrides(
            cfg,
            YamlOverrides(plan_model="newP", task_model="newT", review_model="newR"),
        )
        assert cfg.plan_model == "newP"
        assert cfg.task_model == "newT"
        assert cfg.review_model == "newR"

    def test_empty_overrides_is_no_op(self) -> None:
        cfg = Config(plan_model="P", task_model="T", review_model="R")
        apply_yaml_overrides(cfg, YamlOverrides())
        assert cfg.plan_model == "P"
        assert cfg.task_model == "T"
        assert cfg.review_model == "R"


class TestFindYamlConfig:
    def test_returns_path_when_present(self, tmp_path: Path) -> None:
        yaml = tmp_path / "cadence-config.yaml"
        yaml.write_text("plan:\n  model: opus\n")
        assert find_yaml_config(tmp_path) == yaml

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert find_yaml_config(tmp_path) is None

    def test_returns_none_when_not_a_file(self, tmp_path: Path) -> None:
        (tmp_path / "cadence-config.yaml").mkdir()
        assert find_yaml_config(tmp_path) is None


class TestDetectLocalDir:
    def test_returns_none_when_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert detect_local_dir() is None

    def test_returns_path_when_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cadence_dir = tmp_path / ".cadence"
        cadence_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        result = detect_local_dir()
        assert result is not None
        assert result.name == ".cadence"
