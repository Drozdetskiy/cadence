from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cadence.config import Config
from cadence.diagnostics.doctor import (
    STATUS_FAIL,
    STATUS_OK,
    STATUS_WARN,
    CheckResult,
    check_agents,
    check_config,
    check_context,
    check_environment,
    check_hooks,
    check_prompts,
    check_repository,
    render,
    run_doctor,
)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "doctor@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Doctor"], cwd=path, check=True)


def _git_initial_commit(path: Path, branch: str = "main") -> None:
    subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=path, check=True)
    (path / "README").write_text("hi\n")
    subprocess.run(["git", "add", "README"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=path,
        check=True,
    )


def _by_name(results: list[CheckResult], name: str) -> CheckResult:
    for r in results:
        if r.name == name:
            return r
    raise AssertionError(f"no result named {name!r} in {[r.name for r in results]}")


class TestCheckEnvironment:
    def test_claude_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_which(cmd: str) -> str | None:
            if cmd == "claude":
                return "/usr/local/bin/claude"
            if cmd == "git":
                return "/usr/bin/git"
            return None

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            argv = args[0]
            assert isinstance(argv, list)
            if argv[0] == "/usr/local/bin/claude":
                return subprocess.CompletedProcess(argv, 0, stdout="claude 1.2.3\n", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="git version 2.42.0\n", stderr="")

        monkeypatch.setattr("cadence.diagnostics.doctor.shutil.which", fake_which)
        monkeypatch.setattr("cadence.diagnostics.doctor.subprocess.run", fake_run)
        results = check_environment("claude")
        claude = _by_name(results, "claude")
        assert claude.status == STATUS_OK
        assert "claude 1.2.3" in claude.message
        git = _by_name(results, "git")
        assert git.status == STATUS_OK
        assert "git version 2.42.0" in git.message

    def test_claude_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_which(cmd: str) -> str | None:
            if cmd == "git":
                return "/usr/bin/git"
            return None

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args[0], 0, stdout="git version 2.42.0\n", stderr="")

        monkeypatch.setattr("cadence.diagnostics.doctor.shutil.which", fake_which)
        monkeypatch.setattr("cadence.diagnostics.doctor.subprocess.run", fake_run)
        results = check_environment("claude")
        claude = _by_name(results, "claude")
        assert claude.status == STATUS_FAIL
        assert "not found" in claude.message

    def test_claude_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_which(cmd: str) -> str | None:
            if cmd == "claude":
                return "/x/claude"
            if cmd == "git":
                return "/usr/bin/git"
            return None

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            argv = args[0]
            assert isinstance(argv, list)
            if argv[0] == "/x/claude":
                return subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom\n")
            return subprocess.CompletedProcess(argv, 0, stdout="git version 2\n", stderr="")

        monkeypatch.setattr("cadence.diagnostics.doctor.shutil.which", fake_which)
        monkeypatch.setattr("cadence.diagnostics.doctor.subprocess.run", fake_run)
        results = check_environment("claude")
        claude = _by_name(results, "claude")
        assert claude.status == STATUS_WARN
        assert "/x/claude" in claude.message

    def test_claude_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_which(cmd: str) -> str | None:
            if cmd == "claude":
                return "/x/claude"
            if cmd == "git":
                return "/usr/bin/git"
            return None

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            argv = args[0]
            assert isinstance(argv, list)
            if argv[0] == "/x/claude":
                raise subprocess.TimeoutExpired(cmd=argv, timeout=5)
            return subprocess.CompletedProcess(argv, 0, stdout="git version 2\n", stderr="")

        monkeypatch.setattr("cadence.diagnostics.doctor.shutil.which", fake_which)
        monkeypatch.setattr("cadence.diagnostics.doctor.subprocess.run", fake_run)
        results = check_environment("claude")
        claude = _by_name(results, "claude")
        assert claude.status == STATUS_WARN

    def test_git_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_which(cmd: str) -> str | None:
            if cmd == "claude":
                return "/x/claude"
            return None

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args[0], 0, stdout="claude 1.0\n", stderr="")

        monkeypatch.setattr("cadence.diagnostics.doctor.shutil.which", fake_which)
        monkeypatch.setattr("cadence.diagnostics.doctor.subprocess.run", fake_run)
        results = check_environment("claude")
        git = _by_name(results, "git")
        assert git.status == STATUS_FAIL

    def test_python_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("cadence.diagnostics.doctor.shutil.which", lambda cmd: None)
        results = check_environment("claude")
        py = _by_name(results, "python")
        # We are running on >=3.14 per the project's interpreter, so this is ok.
        assert py.status == STATUS_OK


class TestCheckRepository:
    def test_not_a_git_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = Config()
        results = check_repository(cfg)
        repo = _by_name(results, "git repository")
        assert repo.status == STATUS_FAIL
        # tasks_root should still be reported (warn, since missing)
        tr = _by_name(results, "tasks_root")
        assert tr.status == STATUS_WARN

    def test_clean_repo_with_main(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _git_init(tmp_path)
        _git_initial_commit(tmp_path, branch="main")
        monkeypatch.chdir(tmp_path)
        cfg = Config(default_branch="main")
        results = check_repository(cfg)
        worktree = _by_name(results, "worktree")
        assert worktree.status == STATUS_OK
        default = _by_name(results, "default branch")
        assert default.status == STATUS_OK
        assert "main" in default.message
        assert "(local)" in default.message

    def test_dirty_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _git_init(tmp_path)
        _git_initial_commit(tmp_path, branch="main")
        (tmp_path / "README").write_text("changed\n")
        monkeypatch.chdir(tmp_path)
        cfg = Config(default_branch="main")
        results = check_repository(cfg)
        worktree = _by_name(results, "worktree")
        assert worktree.status == STATUS_WARN
        assert "dirty" in worktree.message

    def test_default_branch_via_origin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Build an "upstream" repo to use as a remote.
        upstream = tmp_path / "upstream"
        upstream.mkdir()
        _git_init(upstream)
        _git_initial_commit(upstream, branch="main")

        clone = tmp_path / "clone"
        subprocess.run(
            ["git", "clone", "-q", str(upstream), str(clone)],
            check=True,
        )
        subprocess.run(["git", "config", "user.email", "doctor@test"], cwd=clone, check=True)
        subprocess.run(["git", "config", "user.name", "Doctor"], cwd=clone, check=True)
        # Rename local main to something else so only origin/main remains.
        subprocess.run(["git", "branch", "-m", "feature"], cwd=clone, check=True)

        monkeypatch.chdir(clone)
        cfg = Config(default_branch="main")
        results = check_repository(cfg)
        default = _by_name(results, "default branch")
        assert default.status == STATUS_OK
        assert "(remote)" in default.message

    def test_default_branch_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _git_init(tmp_path)
        _git_initial_commit(tmp_path, branch="feature")
        monkeypatch.chdir(tmp_path)
        cfg = Config(default_branch="main")
        results = check_repository(cfg)
        default = _by_name(results, "default branch")
        assert default.status == STATUS_FAIL

    def test_tasks_root_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _git_init(tmp_path)
        _git_initial_commit(tmp_path, branch="main")
        monkeypatch.chdir(tmp_path)
        cfg = Config(default_branch="main", tasks_root="cdc-tasks")
        results = check_repository(cfg)
        tr = _by_name(results, "tasks_root")
        assert tr.status == STATUS_WARN
        assert "does not exist" in tr.message

    def test_tasks_root_with_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _git_init(tmp_path)
        _git_initial_commit(tmp_path, branch="main")
        tasks = tmp_path / "cdc-tasks"
        tasks.mkdir()
        (tasks / "0001-foo").mkdir()
        (tasks / "0002-bar").mkdir()
        monkeypatch.chdir(tmp_path)
        cfg = Config(default_branch="main", tasks_root="cdc-tasks")
        results = check_repository(cfg)
        tr = _by_name(results, "tasks_root")
        assert tr.status == STATUS_OK
        assert "2 task dirs" in tr.message

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses W_OK")
    def test_tasks_root_not_writable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _git_init(tmp_path)
        _git_initial_commit(tmp_path, branch="main")
        tasks = tmp_path / "cdc-tasks"
        tasks.mkdir()
        os.chmod(tasks, 0o500)
        try:
            monkeypatch.chdir(tmp_path)
            cfg = Config(default_branch="main", tasks_root="cdc-tasks")
            results = check_repository(cfg)
            tr = _by_name(results, "tasks_root")
            assert tr.status == STATUS_FAIL
            assert "not writable" in tr.message
        finally:
            os.chmod(tasks, 0o755)


class TestCheckConfig:
    def test_no_local_dir(self) -> None:
        results = check_config(None)
        assert len(results) == 1
        assert results[0].status == STATUS_OK
        assert "(no .cadence/" in results[0].message

    def test_no_yaml_file(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        results = check_config(local)
        assert len(results) == 1
        assert results[0].status == STATUS_OK
        assert "defaults in use" in results[0].message

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("foo: [unclosed\n")
        results = check_config(local)
        assert len(results) == 1
        assert results[0].status == STATUS_FAIL
        assert "invalid YAML" in results[0].message

    def test_top_level_not_mapping(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("- one\n- two\n")
        results = check_config(local)
        assert len(results) == 1
        assert results[0].status == STATUS_FAIL
        assert "must be a mapping" in results[0].message

    def test_empty_yaml(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("")
        results = check_config(local)
        assert all(r.status == STATUS_OK for r in results)
        assert any(r.name == "config.yaml" for r in results)

    def test_unknown_key(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("plan_model: claude-opus-4-7\nunknown_thing: 1\n")
        results = check_config(local)
        unknown = [r for r in results if r.status == STATUS_WARN and "unknown_thing" in r.name]
        assert len(unknown) == 1

    def test_unknown_model(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("plan_model: claude-future-9-9\n")
        results = check_config(local)
        warns = [r for r in results if r.name == "plan_model" and r.status == STATUS_WARN]
        assert len(warns) == 1
        assert "claude-future-9-9" in warns[0].message

    def test_known_model_no_warn(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("plan_model: claude-opus-4-7\n")
        results = check_config(local)
        assert not any(r.name == "plan_model" for r in results)

    def test_negative_int_fails(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("iteration_delay_ms: -5\n")
        results = check_config(local)
        fails = [r for r in results if r.name == "iteration_delay_ms" and r.status == STATUS_FAIL]
        assert len(fails) == 1

    def test_zero_max_iterations_fails(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("max_iterations: 0\n")
        results = check_config(local)
        fails = [r for r in results if r.name == "max_iterations" and r.status == STATUS_FAIL]
        assert len(fails) == 1

    def test_invalid_duration(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("idle_timeout: forever\n")
        results = check_config(local)
        fails = [r for r in results if r.name == "idle_timeout" and r.status == STATUS_FAIL]
        assert len(fails) == 1

    def test_runner_policy_out_of_range_fails(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text("limit_retry_max: 0\nmin_plan_iterations: -1\n")
        results = check_config(local)
        retry_fails = [
            r for r in results if r.name == "limit_retry_max" and r.status == STATUS_FAIL
        ]
        plan_fails = [
            r for r in results if r.name == "min_plan_iterations" and r.status == STATUS_FAIL
        ]
        assert len(retry_fails) == 1
        assert len(plan_fails) == 1

    def test_valid_duration(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        (local / "config.yaml").write_text(
            'idle_timeout: 5m\nsession_timeout: 1h\nwait_on_limit: "0"\n'
        )
        results = check_config(local)
        assert not any(r.status == STATUS_FAIL for r in results)


class TestCheckPrompts:
    def test_embedded_defaults_load(self) -> None:
        results = check_prompts(None)
        defaults = [r for r in results if r.name.startswith("default ")]
        assert len(defaults) == 7
        assert all(r.status == STATUS_OK for r in defaults)
        overrides = [r for r in results if r.name == "overrides"]
        assert len(overrides) == 1
        assert overrides[0].status == STATUS_OK
        assert "(no overrides)" in overrides[0].message

    def test_no_overrides_dir(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        results = check_prompts(local)
        overrides = [r for r in results if r.name == "overrides"]
        assert len(overrides) == 1
        assert overrides[0].status == STATUS_OK

    def test_override_with_valid_agent_ref(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        prompts_dir = local / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "review_first.txt").write_text("body {{agent:quality}}\n")
        results = check_prompts(local)
        override = _by_name(results, "override review_first.txt")
        assert override.status == STATUS_OK

    def test_override_with_missing_agent_ref(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        prompts_dir = local / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "review_first.txt").write_text("body {{agent:nope_agent}}\n")
        results = check_prompts(local)
        override = _by_name(results, "override review_first.txt")
        assert override.status == STATUS_WARN
        assert "nope_agent" in override.message

    def test_override_no_agent_refs(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        prompts_dir = local / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "task.txt").write_text("plain prompt body\n")
        results = check_prompts(local)
        override = _by_name(results, "override task.txt")
        assert override.status == STATUS_OK

    def test_override_local_agent_satisfies_ref(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        prompts_dir = local / "prompts"
        agents_dir = local / "agents"
        prompts_dir.mkdir(parents=True)
        agents_dir.mkdir(parents=True)
        (prompts_dir / "review_first.txt").write_text("body {{agent:custom}}\n")
        (agents_dir / "custom.txt").write_text("custom body\n")
        results = check_prompts(local)
        override = _by_name(results, "override review_first.txt")
        assert override.status == STATUS_OK


class TestCheckAgents:
    def test_embedded_defaults_load(self) -> None:
        results = check_agents(None)
        defaults = [r for r in results if r.name.startswith("default ")]
        assert len(defaults) == 4
        assert all(r.status == STATUS_OK for r in defaults)

    def test_no_overrides_dir(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        results = check_agents(local)
        overrides = [r for r in results if r.name == "overrides"]
        assert len(overrides) == 1
        assert overrides[0].status == STATUS_OK
        assert "(no overrides)" in overrides[0].message

    def test_valid_override(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        agents_dir = local / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "custom.txt").write_text("---\nmodel: opus\n---\nbody\n")
        results = check_agents(local)
        override = _by_name(results, "override custom.txt")
        assert override.status == STATUS_OK
        assert "model=opus" in override.message

    def test_override_bad_model(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        agents_dir = local / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "custom.txt").write_text("---\nmodel: foobar\n---\nbody\n")
        results = check_agents(local)
        override = _by_name(results, "override custom.txt")
        assert override.status == STATUS_WARN
        assert "foobar" in override.message

    def test_override_no_frontmatter(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        agents_dir = local / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "custom.txt").write_text("just a body, no frontmatter\n")
        results = check_agents(local)
        override = _by_name(results, "override custom.txt")
        assert override.status == STATUS_OK


class TestCheckHooks:
    def test_disabled(self, tmp_path: Path) -> None:
        results = check_hooks(str(tmp_path / ".cadence" / "hooks"), hooks_enabled=False)
        assert len(results) == 1
        assert results[0].status == STATUS_OK
        assert "disabled" in results[0].message

    def test_missing_dir(self, tmp_path: Path) -> None:
        results = check_hooks(str(tmp_path / "nope"), hooks_enabled=True)
        assert len(results) == 1
        assert results[0].status == STATUS_OK
        assert "does not exist" in results[0].message

    def test_empty_dir(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        results = check_hooks(str(hooks_dir), hooks_enabled=True)
        assert len(results) == 1
        assert results[0].status == STATUS_OK
        assert "no *.sh" in results[0].message

    def test_executable_hook(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook = hooks_dir / "pre-task.sh"
        hook.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(hook, 0o755)
        results = check_hooks(str(hooks_dir), hooks_enabled=True)
        r = _by_name(results, "pre-task.sh")
        assert r.status == STATUS_OK
        assert "executable" in r.message

    def test_non_executable_hook(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook = hooks_dir / "post-review.sh"
        hook.write_text("#!/bin/sh\n")
        os.chmod(hook, 0o644)
        results = check_hooks(str(hooks_dir), hooks_enabled=True)
        r = _by_name(results, "post-review.sh")
        assert r.status == STATUS_FAIL
        assert "chmod" in r.message

    def test_unknown_hook_name(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook = hooks_dir / "weird-name.sh"
        hook.write_text("#!/bin/sh\n")
        os.chmod(hook, 0o755)
        results = check_hooks(str(hooks_dir), hooks_enabled=True)
        r = _by_name(results, "weird-name.sh")
        assert r.status == STATUS_WARN
        assert "unknown" in r.message


class TestCheckContext:
    def test_no_local_dir(self) -> None:
        results = check_context(None)
        assert len(results) == 1
        assert results[0].status == STATUS_OK
        assert "(no context" in results[0].message

    def test_missing_dir(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        local.mkdir()
        results = check_context(local)
        assert len(results) == 1
        assert results[0].status == STATUS_OK

    def test_files_within_limit(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        ctx = local / "context"
        ctx.mkdir(parents=True)
        (ctx / "intro.md").write_text("hello\n")
        (ctx / "schema.sql").write_text("CREATE TABLE t (id INT);\n")
        results = check_context(local)
        intro = _by_name(results, "intro.md")
        assert intro.status == STATUS_OK
        total = _by_name(results, "total")
        assert total.status == STATUS_OK
        assert "limit" in total.message

    def test_files_exceeding_limit(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        ctx = local / "context"
        ctx.mkdir(parents=True)
        # 200_001 bytes — just over the limit
        (ctx / "big.md").write_text("a" * 200_001)
        results = check_context(local)
        total = _by_name(results, "total")
        assert total.status == STATUS_WARN
        assert "exceeds limit" in total.message

    def test_disallowed_extension(self, tmp_path: Path) -> None:
        local = tmp_path / ".cadence"
        ctx = local / "context"
        ctx.mkdir(parents=True)
        (ctx / "image.png").write_bytes(b"\x89PNG fake")
        (ctx / "ok.md").write_text("hello\n")
        results = check_context(local)
        png = _by_name(results, "image.png")
        assert png.status == STATUS_WARN
        assert ".png" in png.message
        # disallowed file should not count toward the total
        total = _by_name(results, "total")
        assert total.status == STATUS_OK

    def test_uppercase_extension_matches_runtime_skip(self, tmp_path: Path) -> None:
        # Runtime (processor.prompts.load_context_files) compares
        # entry.suffix case-sensitively, so files like notes.MD are skipped at
        # runtime; doctor must report the same to avoid misleading the user.
        local = tmp_path / ".cadence"
        ctx = local / "context"
        ctx.mkdir(parents=True)
        (ctx / "notes.MD").write_text("hello\n")
        results = check_context(local)
        notes = _by_name(results, "notes.MD")
        assert notes.status == STATUS_WARN
        assert ".MD" in notes.message


class TestRunDoctor:
    def test_happy_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _git_init(tmp_path)
        _git_initial_commit(tmp_path, branch="main")
        # Build .cadence and cdc-tasks
        cadence_dir = tmp_path / ".cadence"
        cadence_dir.mkdir()
        (cadence_dir / "config.yaml").write_text("plan_model: claude-opus-4-7\n")
        tasks = tmp_path / "cdc-tasks"
        tasks.mkdir()
        (tasks / "0001-foo").mkdir()

        monkeypatch.chdir(tmp_path)

        def fake_which(cmd: str) -> str | None:
            if cmd == "claude":
                return "/usr/local/bin/claude"
            if cmd == "git":
                return "/usr/bin/git"
            return None

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            argv = args[0]
            assert isinstance(argv, list)
            return subprocess.CompletedProcess(argv, 0, stdout="x 1.0\n", stderr="")

        monkeypatch.setattr("cadence.diagnostics.doctor.shutil.which", fake_which)
        monkeypatch.setattr("cadence.diagnostics.doctor.subprocess.run", fake_run)

        cfg = Config(default_branch="main", tasks_root="cdc-tasks")
        results, exit_code = run_doctor(cfg=cfg, local_dir=cadence_dir)
        assert exit_code == 0
        categories = {r.category for r in results}
        assert {
            "environment",
            "repository",
            "config",
            "prompts",
            "agents",
            "hooks",
            "context",
        } <= categories

    def test_failure_when_claude_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_init(tmp_path)
        _git_initial_commit(tmp_path, branch="main")
        monkeypatch.chdir(tmp_path)

        monkeypatch.setattr(
            "cadence.diagnostics.doctor.shutil.which",
            lambda cmd: "/usr/bin/git" if cmd == "git" else None,
        )

        cfg = Config(default_branch="main", tasks_root="cdc-tasks")
        results, exit_code = run_doctor(cfg=cfg, local_dir=None)
        assert exit_code == 1
        assert any(r.status == STATUS_FAIL and r.name == "claude" for r in results)


class TestRender:
    def _sample_results(self) -> list[CheckResult]:
        return [
            CheckResult(STATUS_OK, "environment", "claude", "/x/claude (1.0)"),
            CheckResult(STATUS_WARN, "config", "iteration_delay_ms", "warn message"),
            CheckResult(STATUS_FAIL, "repository", "default branch", "missing"),
        ]

    def test_render_groups_by_category(self) -> None:
        text = render(self._sample_results(), no_color=True)
        # categories should appear as headers
        assert "environment" in text
        assert "repository" in text
        assert "config" in text
        # glyphs (no_color preserves text)
        assert "✓" in text
        assert "⚠" in text
        assert "✗" in text

    def test_render_footer_counts(self) -> None:
        text = render(self._sample_results(), no_color=True)
        assert "1 warning" in text
        assert "1 error" in text
        assert "result:" in text

    def test_render_all_passed(self) -> None:
        results = [CheckResult(STATUS_OK, "environment", "git", "/usr/bin/git (2.0)")]
        text = render(results, no_color=True)
        assert "all checks passed" in text

    def test_render_canonical_category_order(self) -> None:
        # Provide categories out of canonical order; renderer should reorder.
        results = [
            CheckResult(STATUS_OK, "context", "context", "(no context directory)"),
            CheckResult(STATUS_OK, "environment", "git", "ok"),
            CheckResult(STATUS_OK, "config", "config.yaml", "ok"),
        ]
        text = render(results, no_color=True)
        env_pos = text.index("environment")
        config_pos = text.index("config")
        context_pos = text.index("context")
        assert env_pos < config_pos < context_pos
