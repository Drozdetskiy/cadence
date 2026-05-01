from __future__ import annotations

import io

import pytest

from rlx.input import (
    TerminalCollector,
    read_line_with_context,
)


class TestReadLineWithContext:
    def test_reads_line(self) -> None:
        reader = io.StringIO("hello\nworld\n")
        assert read_line_with_context(reader) == "hello"

    def test_strips_newline(self) -> None:
        reader = io.StringIO("line\n")
        assert read_line_with_context(reader) == "line"

    def test_eof_raises(self) -> None:
        reader = io.StringIO("")
        with pytest.raises(EOFError):
            read_line_with_context(reader)


class TestAskQuestion:
    def _make_collector(self, input_text: str) -> TerminalCollector:
        tc = TerminalCollector()
        tc._stdin = io.StringIO(input_text)
        tc._stdout = io.StringIO()
        return tc

    def test_select_first_option(self) -> None:
        tc = self._make_collector("1\n")
        result = tc.ask_question("Pick one:", ["Alpha", "Beta"])
        assert result == "Alpha"

    def test_select_second_option(self) -> None:
        tc = self._make_collector("2\n")
        result = tc.ask_question("Pick one:", ["Alpha", "Beta"])
        assert result == "Beta"

    def test_select_other(self) -> None:
        tc = self._make_collector("3\ncustom answer\n")
        result = tc.ask_question("Pick one:", ["Alpha", "Beta"])
        assert result == "custom answer"

    def test_filters_other_collision(self) -> None:
        tc = self._make_collector("1\n")
        result = tc.ask_question("Pick:", ["Alpha", "Other (type your own answer)"])
        assert result == "Alpha"

    def test_invalid_then_valid(self) -> None:
        tc = self._make_collector("abc\n0\n99\n1\n")
        result = tc.ask_question("Pick:", ["Only"])
        assert result == "Only"
