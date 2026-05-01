from __future__ import annotations

from rich.style import Style

from cadence.config import ColorConfig
from cadence.status import Phase, PhaseReview


def _hex_to_style(hex_color: str) -> Style:
    return Style(color=hex_color)


class Colors:
    def __init__(self, cfg: ColorConfig) -> None:
        self._task = _hex_to_style(cfg.task)
        self._review = _hex_to_style(cfg.review)
        self._warn = _hex_to_style(cfg.warn)
        self._err = _hex_to_style(cfg.error)
        self._signal = _hex_to_style(cfg.signal)
        self._timestamp = _hex_to_style(cfg.timestamp)
        self._info = _hex_to_style(cfg.info)
        self._phases: dict[Phase, Style] = {PhaseReview: self._review}

    def for_phase(self, phase: Phase) -> Style:
        return self._phases.get(phase, self._task)

    def timestamp(self) -> Style:
        return self._timestamp

    def warn(self) -> Style:
        return self._warn

    def error(self) -> Style:
        return self._err

    def signal(self) -> Style:
        return self._signal

    def info(self) -> Style:
        return self._info
