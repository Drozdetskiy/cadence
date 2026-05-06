from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from cadence.executor.claude_executor import Result
from cadence.processor.reporter import run_report
from cadence.status import SignalReportDone, SignalReportFailed


def _make_logger(tmp_path: Path) -> MagicMock:
    log = MagicMock()
    log.path = str(tmp_path / "progress-report-api-changes.txt")
    return log


def _make_git_svc() -> MagicMock:
    return MagicMock()


def _wrap_body(body: str) -> str:
    return f"<<<CADENCE:REPORT_BEGIN>>>\n{body}\n<<<CADENCE:REPORT_END>>>\n{SignalReportDone}\n"


def test_happy_path_writes_file_and_echoes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "# API changes: feat-x vs main\n\n## Added\n- /users (abc1234)"
    executor = MagicMock()
    executor.run.return_value = Result(output=_wrap_body(body), signal=SignalReportDone)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-api-changes.md")

    ok = run_report(
        "api-changes",
        base="main",
        stdout_only=False,
        executor=executor,
        git_svc=_make_git_svc(),
        logger=log,
        local_dir=None,
        public_api_paths=[],
        branch="feat-x",
        default_branch="main",
        report_path=report_path,
    )

    assert ok is True
    written = Path(report_path).read_text(encoding="utf-8")
    assert written == body
    captured = capsys.readouterr()
    assert body in captured.out


def test_stdout_only_skips_file_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = "# API changes\n"
    executor = MagicMock()
    executor.run.return_value = Result(output=_wrap_body(body), signal=SignalReportDone)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-api-changes.md")

    ok = run_report(
        "api-changes",
        base="main",
        stdout_only=True,
        executor=executor,
        git_svc=_make_git_svc(),
        logger=log,
        local_dir=None,
        public_api_paths=[],
        branch="feat-x",
        default_branch="main",
        report_path=report_path,
    )

    assert ok is True
    assert not Path(report_path).exists()
    captured = capsys.readouterr()
    assert body in captured.out


def test_missing_markers_raises_and_writes_no_file(tmp_path: Path) -> None:
    executor = MagicMock()
    executor.run.return_value = Result(output="no markers at all", signal=SignalReportDone)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-api-changes.md")

    with pytest.raises(RuntimeError, match="report body not found between markers"):
        run_report(
            "api-changes",
            base="main",
            stdout_only=False,
            executor=executor,
            git_svc=_make_git_svc(),
            logger=log,
            local_dir=None,
            public_api_paths=[],
            branch="feat-x",
            default_branch="main",
            report_path=report_path,
        )

    assert not Path(report_path).exists()


def test_report_failed_signal_raises_and_writes_no_file(tmp_path: Path) -> None:
    executor = MagicMock()
    executor.run.return_value = Result(output="", signal=SignalReportFailed)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-api-changes.md")

    with pytest.raises(RuntimeError, match="claude reported failure"):
        run_report(
            "api-changes",
            base="main",
            stdout_only=False,
            executor=executor,
            git_svc=_make_git_svc(),
            logger=log,
            local_dir=None,
            public_api_paths=[],
            branch="feat-x",
            default_branch="main",
            report_path=report_path,
        )

    assert not Path(report_path).exists()


def test_idle_timeout_with_no_signal_raises(tmp_path: Path) -> None:
    executor = MagicMock()
    executor.run.return_value = Result(output="", signal="", idle_timed_out=True)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-api-changes.md")

    with pytest.raises(RuntimeError, match="idle-timed out"):
        run_report(
            "api-changes",
            base="main",
            stdout_only=False,
            executor=executor,
            git_svc=_make_git_svc(),
            logger=log,
            local_dir=None,
            public_api_paths=[],
            branch="feat-x",
            default_branch="main",
            report_path=report_path,
        )

    assert not Path(report_path).exists()


def test_executor_error_raises(tmp_path: Path) -> None:
    err = RuntimeError("claude failed to launch")
    executor = MagicMock()
    executor.run.return_value = Result(error=err)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-api-changes.md")

    with pytest.raises(RuntimeError, match="claude error"):
        run_report(
            "api-changes",
            base="main",
            stdout_only=False,
            executor=executor,
            git_svc=_make_git_svc(),
            logger=log,
            local_dir=None,
            public_api_paths=[],
            branch="feat-x",
            default_branch="main",
            report_path=report_path,
        )

    assert not Path(report_path).exists()


def test_unknown_report_type_raises_value_error(tmp_path: Path) -> None:
    executor = MagicMock()
    log = _make_logger(tmp_path)

    with pytest.raises(ValueError, match="unknown report_type"):
        run_report(
            "bogus",
            base="main",
            stdout_only=False,
            executor=executor,
            git_svc=_make_git_svc(),
            logger=log,
            local_dir=None,
            public_api_paths=[],
            branch="feat-x",
            default_branch="main",
            report_path=str(tmp_path / "out.md"),
        )

    executor.run.assert_not_called()


def test_prompt_includes_branch_and_paths(tmp_path: Path) -> None:
    body = "# report"
    executor = MagicMock()
    executor.run.return_value = Result(output=_wrap_body(body), signal=SignalReportDone)
    log = _make_logger(tmp_path)

    run_report(
        "api-changes",
        base="main",
        stdout_only=True,
        executor=executor,
        git_svc=_make_git_svc(),
        logger=log,
        local_dir=None,
        public_api_paths=["src/api", "proto/"],
        branch="feat-x",
        default_branch="main",
        report_path=str(tmp_path / "out.md"),
    )

    sent_prompt: Any = executor.run.call_args[0][0]
    assert "feat-x" in sent_prompt
    assert "main" in sent_prompt
    assert "src/api" in sent_prompt
    assert "proto/" in sent_prompt


def test_file_written_with_secure_permissions(tmp_path: Path) -> None:
    body = "report body"
    executor = MagicMock()
    executor.run.return_value = Result(output=_wrap_body(body), signal=SignalReportDone)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-api-changes.md")

    run_report(
        "api-changes",
        base="main",
        stdout_only=False,
        executor=executor,
        git_svc=_make_git_svc(),
        logger=log,
        local_dir=None,
        public_api_paths=[],
        branch="feat-x",
        default_branch="main",
        report_path=report_path,
    )

    mode = Path(report_path).stat().st_mode & 0o777
    assert mode == 0o600


def test_test_cases_happy_path_writes_file_and_echoes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "# Test cases: feat-x vs main\n\n### TC-1: login\n**Type:** functional"
    executor = MagicMock()
    executor.run.return_value = Result(output=_wrap_body(body), signal=SignalReportDone)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-test-cases.md")

    ok = run_report(
        "test-cases",
        base="main",
        stdout_only=False,
        executor=executor,
        git_svc=_make_git_svc(),
        logger=log,
        local_dir=None,
        public_api_paths=[],
        branch="feat-x",
        default_branch="main",
        report_path=report_path,
    )

    assert ok is True
    written = Path(report_path).read_text(encoding="utf-8")
    assert written == body
    captured = capsys.readouterr()
    assert body in captured.out


def test_test_cases_stdout_only_skips_file_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "# Test cases\n"
    executor = MagicMock()
    executor.run.return_value = Result(output=_wrap_body(body), signal=SignalReportDone)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-test-cases.md")

    ok = run_report(
        "test-cases",
        base="main",
        stdout_only=True,
        executor=executor,
        git_svc=_make_git_svc(),
        logger=log,
        local_dir=None,
        public_api_paths=[],
        branch="feat-x",
        default_branch="main",
        report_path=report_path,
    )

    assert ok is True
    assert not Path(report_path).exists()
    captured = capsys.readouterr()
    assert body in captured.out


def test_test_cases_missing_markers_raises_and_writes_no_file(tmp_path: Path) -> None:
    executor = MagicMock()
    executor.run.return_value = Result(output="no markers at all", signal=SignalReportDone)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-test-cases.md")

    with pytest.raises(RuntimeError, match="report body not found between markers"):
        run_report(
            "test-cases",
            base="main",
            stdout_only=False,
            executor=executor,
            git_svc=_make_git_svc(),
            logger=log,
            local_dir=None,
            public_api_paths=[],
            branch="feat-x",
            default_branch="main",
            report_path=report_path,
        )

    assert not Path(report_path).exists()


def test_test_cases_report_failed_signal_raises_and_writes_no_file(tmp_path: Path) -> None:
    executor = MagicMock()
    executor.run.return_value = Result(output="", signal=SignalReportFailed)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-test-cases.md")

    with pytest.raises(RuntimeError, match="claude reported failure"):
        run_report(
            "test-cases",
            base="main",
            stdout_only=False,
            executor=executor,
            git_svc=_make_git_svc(),
            logger=log,
            local_dir=None,
            public_api_paths=[],
            branch="feat-x",
            default_branch="main",
            report_path=report_path,
        )

    assert not Path(report_path).exists()


def test_test_cases_idle_timeout_with_no_signal_raises(tmp_path: Path) -> None:
    executor = MagicMock()
    executor.run.return_value = Result(output="", signal="", idle_timed_out=True)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-test-cases.md")

    with pytest.raises(RuntimeError, match="idle-timed out"):
        run_report(
            "test-cases",
            base="main",
            stdout_only=False,
            executor=executor,
            git_svc=_make_git_svc(),
            logger=log,
            local_dir=None,
            public_api_paths=[],
            branch="feat-x",
            default_branch="main",
            report_path=report_path,
        )

    assert not Path(report_path).exists()


def test_test_cases_executor_error_raises(tmp_path: Path) -> None:
    err = RuntimeError("claude failed to launch")
    executor = MagicMock()
    executor.run.return_value = Result(error=err)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-test-cases.md")

    with pytest.raises(RuntimeError, match="claude error"):
        run_report(
            "test-cases",
            base="main",
            stdout_only=False,
            executor=executor,
            git_svc=_make_git_svc(),
            logger=log,
            local_dir=None,
            public_api_paths=[],
            branch="feat-x",
            default_branch="main",
            report_path=report_path,
        )

    assert not Path(report_path).exists()


def test_test_cases_prompt_includes_branch_and_default_branch_no_api_paths(
    tmp_path: Path,
) -> None:
    body = "# report"
    executor = MagicMock()
    executor.run.return_value = Result(output=_wrap_body(body), signal=SignalReportDone)
    log = _make_logger(tmp_path)

    run_report(
        "test-cases",
        base="develop",
        stdout_only=True,
        executor=executor,
        git_svc=_make_git_svc(),
        logger=log,
        local_dir=None,
        public_api_paths=["src/api", "proto/"],
        branch="feat-x",
        default_branch="develop",
        report_path=str(tmp_path / "out.md"),
    )

    sent_prompt: Any = executor.run.call_args[0][0]
    assert "feat-x" in sent_prompt
    assert "develop" in sent_prompt
    assert "{{PUBLIC_API_PATHS}}" not in sent_prompt
    assert "src/api" not in sent_prompt
    assert "proto/" not in sent_prompt


def test_test_cases_file_written_with_secure_permissions(tmp_path: Path) -> None:
    body = "report body"
    executor = MagicMock()
    executor.run.return_value = Result(output=_wrap_body(body), signal=SignalReportDone)
    log = _make_logger(tmp_path)
    report_path = str(tmp_path / "cdc-tasks" / "feat-x" / "report-test-cases.md")

    run_report(
        "test-cases",
        base="main",
        stdout_only=False,
        executor=executor,
        git_svc=_make_git_svc(),
        logger=log,
        local_dir=None,
        public_api_paths=[],
        branch="feat-x",
        default_branch="main",
        report_path=report_path,
    )

    mode = Path(report_path).stat().st_mode & 0o777
    assert mode == 0o600
