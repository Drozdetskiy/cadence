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
        assert cfg.claude_args == "--dangerously-skip-permissions --verbose"
        assert cfg.plan_model == "claude-opus-4-7"
        assert cfg.task_model == "claude-opus-4-7"
        assert cfg.review_model == "claude-opus-4-7"
        assert cfg.iteration_delay_ms == 2000
        assert cfg.task_retry_count == 1
        assert cfg.max_iterations == 50
        assert cfg.session_timeout == "0"
        assert cfg.idle_timeout == "5m"
        assert cfg.wait_on_limit == "0"
        assert cfg.tasks_root == "cdc-tasks"
        assert cfg.default_branch == "main"
        assert cfg.init_prompt_name == "init"
        assert cfg.commit_trailer == ""
        assert cfg.report_api_changes_model == ""
        assert cfg.report_test_cases_model == ""
        assert cfg.public_api_paths == []
        assert cfg.hooks_dir == ".cadence/hooks"
        assert cfg.hooks_timeout_seconds == 60
        assert cfg.hooks_enabled is True
        assert cfg.print_usage is True
        assert cfg.cost_estimates is True
        assert cfg.progress_jsonl is False
        assert cfg.running_threshold_minutes == 10
        assert cfg.import_max_bytes == 262144
        assert cfg.commit_format != ""
        assert "a single line `<branch-name>. <Clause>: <what>.`" in cfg.commit_format
        assert "separated by `. ` (period + space)" in cfg.commit_format
        assert "no Co-Authored-By trailer" in cfg.commit_format
        assert "\n\nChanged:" not in cfg.commit_format
        assert "You've hit your limit" in cfg.claude_error_patterns
        assert "API Error:" in cfg.claude_error_patterns
        assert "You've hit your limit" in cfg.claude_limit_patterns
        assert isinstance(cfg.colors, ColorConfig)

    def test_commit_format_canonical_examples(self) -> None:
        cfg = Config()
        assert "0030-chain. Added:" in cfg.commit_format
        assert "0014-no-plan-commit-on-start. Changed:" in cfg.commit_format


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
        yaml_path.write_text("claude_command: my-claude\ncommit_trailer: my-trailer\n")
        cfg = load_config(tmp_path)
        assert cfg.claude_command == "my-claude"
        assert cfg.commit_trailer == "my-trailer"
        assert cfg.task_model == "claude-opus-4-7"

    def test_load_commit_format_override(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text('commit_format: "custom"\n')
        cfg = load_config(tmp_path)
        assert cfg.commit_format == "custom"

    def test_load_tasks_root_override(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("tasks_root: my-tasks\n")
        cfg = load_config(tmp_path)
        assert cfg.tasks_root == "my-tasks"

    def test_load_init_prompt_name_override(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("init_prompt_name: preprompt\n")
        cfg = load_config(tmp_path)
        assert cfg.init_prompt_name == "preprompt"

    def test_load_int_fields(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("max_iterations: 100\niteration_delay_ms: 500\n")
        cfg = load_config(tmp_path)
        assert cfg.max_iterations == 100
        assert cfg.iteration_delay_ms == 500

    def test_load_list_fields(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text('claude_error_patterns:\n  - "custom error"\n')
        cfg = load_config(tmp_path)
        assert cfg.claude_error_patterns == ["custom error"]

    def test_load_public_api_paths(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text('public_api_paths:\n  - "src/api"\n  - "proto"\n')
        cfg = load_config(tmp_path)
        assert cfg.public_api_paths == ["src/api", "proto"]

    def test_load_report_api_changes_model(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("report_api_changes_model: claude-sonnet-4-6\n")
        cfg = load_config(tmp_path)
        assert cfg.report_api_changes_model == "claude-sonnet-4-6"

    def test_default_report_api_changes_model_is_empty(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("claude_command: x\n")
        cfg = load_config(tmp_path)
        assert cfg.report_api_changes_model == ""

    def test_load_report_test_cases_model(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("report_test_cases_model: claude-sonnet-4-6\n")
        cfg = load_config(tmp_path)
        assert cfg.report_test_cases_model == "claude-sonnet-4-6"

    def test_default_report_test_cases_model_is_empty(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("claude_command: x\n")
        cfg = load_config(tmp_path)
        assert cfg.report_test_cases_model == ""

    def test_load_hooks_fields(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "hooks_dir: custom/path\nhooks_timeout_seconds: 30\nhooks_enabled: false\n"
        )
        cfg = load_config(tmp_path)
        assert cfg.hooks_dir == "custom/path"
        assert cfg.hooks_timeout_seconds == 30
        assert cfg.hooks_enabled is False

    def test_load_running_threshold_minutes(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("running_threshold_minutes: 30\n")
        cfg = load_config(tmp_path)
        assert cfg.running_threshold_minutes == 30

    def test_default_running_threshold_minutes(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("claude_command: x\n")
        cfg = load_config(tmp_path)
        assert cfg.running_threshold_minutes == 10

    def test_load_import_max_bytes(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("import_max_bytes: 1024\n")
        cfg = load_config(tmp_path)
        assert cfg.import_max_bytes == 1024

    def test_default_import_max_bytes(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("claude_command: x\n")
        cfg = load_config(tmp_path)
        assert cfg.import_max_bytes == 262144

    def test_load_usage_flags_false(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("print_usage: false\ncost_estimates: false\n")
        cfg = load_config(tmp_path)
        assert cfg.print_usage is False
        assert cfg.cost_estimates is False

    def test_default_usage_flags_when_yaml_missing_keys(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("claude_command: x\n")
        cfg = load_config(tmp_path)
        assert cfg.print_usage is True
        assert cfg.cost_estimates is True

    def test_non_bool_yaml_value_coerced_to_true(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text('print_usage: "no"\ncost_estimates: "no"\n')
        cfg = load_config(tmp_path)
        assert cfg.print_usage is True
        assert cfg.cost_estimates is True

    def test_load_progress_jsonl_true(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("progress_jsonl: true\n")
        cfg = load_config(tmp_path)
        assert cfg.progress_jsonl is True

    def test_default_progress_jsonl_when_yaml_missing_key(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("claude_command: x\n")
        cfg = load_config(tmp_path)
        assert cfg.progress_jsonl is False

    def test_progress_jsonl_non_bool_value_coerced(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text('progress_jsonl: "yes"\n')
        cfg = load_config(tmp_path)
        assert cfg.progress_jsonl is True

    def test_progress_jsonl_empty_string_coerced_false(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text('progress_jsonl: ""\n')
        cfg = load_config(tmp_path)
        assert cfg.progress_jsonl is False

    def test_invalid_hooks_timeout_raises(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("hooks_timeout_seconds: not-a-number\n")
        with pytest.raises(ValueError):
            load_config(tmp_path)

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
        assert cfg.tasks_root == "cdc-tasks"

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
        text = "plan:\n  model: opus\ntask:\n  model: sonnet\nreview:\n  model: haiku\n"
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
        text = "unknown:\n  model: ignored\ntask:\n  model: sonnet\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model == "sonnet"
        assert overrides.plan_model is None
        assert overrides.review_model is None

    def test_unknown_nested_key_ignored(self) -> None:
        text = "task:\n  model: sonnet\n  temperature: 0.5\n"
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
        with pytest.raises(ValueError, match=r"invalid config\.yaml"):
            parse_yaml_overrides("task:\n  model: 'unterminated\n")

    def test_malformed_top_level_scalar_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"invalid config\.yaml: top-level must be a mapping",
        ):
            parse_yaml_overrides("- 1\n- 2\n")

    def test_section_with_scalar_value_silently_ignored(self) -> None:
        overrides = parse_yaml_overrides("task: sonnet\n")
        assert overrides == YamlOverrides()

    def test_default_branch_top_level(self) -> None:
        overrides = parse_yaml_overrides("default_branch: 0015-refactoring\n")
        assert overrides.default_branch == "0015-refactoring"

    def test_default_branch_alongside_models(self) -> None:
        text = "default_branch: develop\ntask:\n  model: sonnet\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.default_branch == "develop"
        assert overrides.task_model == "sonnet"

    def test_default_branch_empty_string_ignored(self) -> None:
        overrides = parse_yaml_overrides("default_branch: ''\n")
        assert overrides.default_branch is None

    def test_default_branch_non_string_ignored(self) -> None:
        overrides = parse_yaml_overrides("default_branch: 42\n")
        assert overrides.default_branch is None

    def test_report_api_changes_model_override(self) -> None:
        text = "report_api_changes:\n  model: opus-report\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.report_api_changes_model == "opus-report"
        assert overrides.plan_model is None
        assert overrides.task_model is None
        assert overrides.review_model is None

    def test_report_api_changes_model_alongside_others(self) -> None:
        text = "task:\n  model: sonnet\nreport_api_changes:\n  model: opus-report\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model == "sonnet"
        assert overrides.report_api_changes_model == "opus-report"

    def test_report_api_changes_model_empty_value_ignored(self) -> None:
        text = "report_api_changes:\n  model:\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.report_api_changes_model is None

    def test_report_test_cases_model_override(self) -> None:
        text = "report_test_cases:\n  model: opus-tc\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.report_test_cases_model == "opus-tc"
        assert overrides.plan_model is None
        assert overrides.task_model is None
        assert overrides.review_model is None
        assert overrides.report_api_changes_model is None

    def test_report_test_cases_model_alongside_others(self) -> None:
        text = "task:\n  model: sonnet\nreport_test_cases:\n  model: opus-tc\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.task_model == "sonnet"
        assert overrides.report_test_cases_model == "opus-tc"

    def test_report_test_cases_model_empty_value_ignored(self) -> None:
        text = "report_test_cases:\n  model:\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.report_test_cases_model is None

    def test_report_api_and_test_cases_models_independent(self) -> None:
        text = "report_api_changes:\n  model: opus-api\nreport_test_cases:\n  model: opus-tc\n"
        overrides = parse_yaml_overrides(text)
        assert overrides.report_api_changes_model == "opus-api"
        assert overrides.report_test_cases_model == "opus-tc"


class TestLoadYamlConfig:
    def test_loads_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("plan:\n  model: opus\n")
        overrides = load_yaml_config(path)
        assert overrides.plan_model == "opus"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_yaml_config(tmp_path / "nope.yaml")

    def test_malformed_file_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("not valid yaml without colon\n")
        with pytest.raises(ValueError, match=r"invalid config\.yaml"):
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

    def test_default_branch_overrides(self) -> None:
        cfg = Config(default_branch="main")
        apply_yaml_overrides(cfg, YamlOverrides(default_branch="0015-refactoring"))
        assert cfg.default_branch == "0015-refactoring"

    def test_default_branch_none_is_no_op(self) -> None:
        cfg = Config(default_branch="main")
        apply_yaml_overrides(cfg, YamlOverrides())
        assert cfg.default_branch == "main"

    def test_report_api_changes_model_overrides(self) -> None:
        cfg = Config()
        apply_yaml_overrides(cfg, YamlOverrides(report_api_changes_model="opus-rep"))
        assert cfg.report_api_changes_model == "opus-rep"

    def test_report_api_changes_model_none_is_no_op(self) -> None:
        cfg = Config(report_api_changes_model="preset")
        apply_yaml_overrides(cfg, YamlOverrides())
        assert cfg.report_api_changes_model == "preset"

    def test_report_test_cases_model_overrides(self) -> None:
        cfg = Config()
        apply_yaml_overrides(cfg, YamlOverrides(report_test_cases_model="opus-tc"))
        assert cfg.report_test_cases_model == "opus-tc"

    def test_report_test_cases_model_none_is_no_op(self) -> None:
        cfg = Config(report_test_cases_model="preset")
        apply_yaml_overrides(cfg, YamlOverrides())
        assert cfg.report_test_cases_model == "preset"


class TestFindYamlConfig:
    def test_returns_path_when_present(self, tmp_path: Path) -> None:
        yaml = tmp_path / "config.yaml"
        yaml.write_text("plan:\n  model: opus\n")
        assert find_yaml_config(tmp_path) == yaml

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert find_yaml_config(tmp_path) is None

    def test_returns_none_when_not_a_file(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").mkdir()
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
