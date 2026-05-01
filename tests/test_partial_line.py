from __future__ import annotations

import io

from cadence.progress.logger import _PartialLineBuffer


class TestPartialLineBuffer:
    def test_starts_at_line_start(self) -> None:
        buf = _PartialLineBuffer()
        assert buf.at_line_start

    def test_mark_mid_line_clears_flag(self) -> None:
        buf = _PartialLineBuffer()
        buf.mark_mid_line()
        assert not buf.at_line_start

    def test_mark_line_start_restores_flag(self) -> None:
        buf = _PartialLineBuffer()
        buf.mark_mid_line()
        buf.mark_line_start()
        assert buf.at_line_start

    def test_ensure_newline_at_line_start_writes_nothing(self) -> None:
        buf = _PartialLineBuffer()
        out = io.StringIO()
        buf.ensure_newline(out)
        assert out.getvalue() == ""
        assert buf.at_line_start

    def test_ensure_newline_mid_line_writes_newline_and_resets(self) -> None:
        buf = _PartialLineBuffer()
        buf.mark_mid_line()
        out = io.StringIO()
        buf.ensure_newline(out)
        assert out.getvalue() == "\n"
        assert buf.at_line_start

    def test_ensure_newline_idempotent(self) -> None:
        buf = _PartialLineBuffer()
        buf.mark_mid_line()
        out = io.StringIO()
        buf.ensure_newline(out)
        buf.ensure_newline(out)
        assert out.getvalue() == "\n"
