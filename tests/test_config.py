from __future__ import annotations

from pathlib import Path

import pytest

from rlx.config import ColorConfig, Config, detect_local_dir, load_config, parse_duration


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
        assert cfg.plan_model == "opus4.7"
        assert cfg.task_model == "opus4.7"
        assert cfg.review_model == "opus4.7"
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

    def test_missing_toml(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path)
        assert cfg.max_iterations == 50

    def test_load_string_fields(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "config.toml"
        toml_path.write_text('claude_command = "my-claude"\nplans_dir = "my-plans"\n')
        cfg = load_config(tmp_path)
        assert cfg.claude_command == "my-claude"
        assert cfg.plans_dir == "my-plans"
        assert cfg.task_model == "opus4.7"

    def test_load_int_fields(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "config.toml"
        toml_path.write_text("max_iterations = 100\niteration_delay_ms = 500\n")
        cfg = load_config(tmp_path)
        assert cfg.max_iterations == 100
        assert cfg.iteration_delay_ms == 500

    def test_load_bool_fields(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "config.toml"
        toml_path.write_text("finalize_enabled = true\n")
        cfg = load_config(tmp_path)
        assert cfg.finalize_enabled is True

    def test_load_list_fields(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "config.toml"
        toml_path.write_text('claude_error_patterns = ["custom error"]\n')
        cfg = load_config(tmp_path)
        assert cfg.claude_error_patterns == ["custom error"]

    def test_load_colors(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "config.toml"
        toml_path.write_text('[colors]\ntask = "#ff0000"\nwarn = "#00ff00"\n')
        cfg = load_config(tmp_path)
        assert cfg.colors.task == "#ff0000"
        assert cfg.colors.warn == "#00ff00"
        assert cfg.colors.review == "#1a9e9e"

    def test_absent_keys_keep_defaults(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "config.toml"
        toml_path.write_text('claude_command = "custom"\n')
        cfg = load_config(tmp_path)
        assert cfg.claude_command == "custom"
        assert cfg.max_iterations == 50
        assert cfg.finalize_enabled is False

    def test_tilde_expansion(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "config.toml"
        toml_path.write_text('vcs_command = "~/bin/git"\n')
        cfg = load_config(tmp_path)
        assert not cfg.vcs_command.startswith("~")
        assert cfg.vcs_command.endswith("/bin/git")


class TestDetectLocalDir:
    def test_returns_none_when_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert detect_local_dir() is None

    def test_returns_path_when_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rlx_dir = tmp_path / ".rlx"
        rlx_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        result = detect_local_dir()
        assert result is not None
        assert result.name == ".rlx"
