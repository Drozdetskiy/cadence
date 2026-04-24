from __future__ import annotations

import sys
from typing import IO

_OTHER_SENTINEL = "Other (type your own answer)"


def read_line_with_context(reader: IO[str]) -> str:
    line = reader.readline()
    if not line:
        raise EOFError("stdin closed")
    return line.rstrip("\n")


def ask_yes_no(
    prompt: str,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> bool:
    out = stdout if stdout is not None else sys.stdout
    inp = stdin if stdin is not None else sys.stdin
    out.write(f"{prompt} [y/N]: ")
    out.flush()
    try:
        raw = read_line_with_context(inp)
    except (EOFError, OSError):
        return False
    return raw.strip().lower() in ("y", "yes")


class TerminalCollector:
    def __init__(self) -> None:
        self._stdin: IO[str] = sys.stdin
        self._stdout: IO[str] = sys.stdout

    def ask_question(self, question: str, options: list[str]) -> str:
        filtered = [o for o in options if o != _OTHER_SENTINEL]
        choices = [*filtered, _OTHER_SENTINEL]
        self._stdout.write(f"\n{question}\n")
        try:
            return self._select_with_numbers(choices)
        except (EOFError, OSError):
            return options[0] if options else ""

    def _select_with_numbers(self, choices: list[str]) -> str:
        for i, choice in enumerate(choices, 1):
            self._stdout.write(f"  {i}. {choice}\n")
        self._stdout.flush()

        while True:
            self._stdout.write(f"Enter number (1-{len(choices)}): ")
            self._stdout.flush()
            raw = read_line_with_context(self._stdin)
            try:
                num = int(raw)
            except ValueError:
                continue
            if 1 <= num <= len(choices):
                selected = choices[num - 1]
                if selected == _OTHER_SENTINEL:
                    return self._read_custom_answer()
                return selected

    def _read_custom_answer(self) -> str:
        self._stdout.write("Type your answer: ")
        self._stdout.flush()
        return read_line_with_context(self._stdin)
