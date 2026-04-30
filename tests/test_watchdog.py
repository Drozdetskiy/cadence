from __future__ import annotations

import threading
import time

from cadence.executor.claude_executor import _IdleWatchdog


class TestIdleWatchdog:
    def test_inactive_when_timeout_zero(self) -> None:
        wd = _IdleWatchdog(0.0, lambda: None)
        assert not wd.active()

    def test_active_when_timeout_positive(self) -> None:
        wd = _IdleWatchdog(0.1, lambda: None)
        assert wd.active()
        wd.cancel()

    def test_reset_no_op_when_inactive(self) -> None:
        called = threading.Event()
        wd = _IdleWatchdog(0.0, called.set)
        wd.reset()
        time.sleep(0.05)
        assert not called.is_set()
        assert not wd.triggered.is_set()

    def test_fires_after_timeout(self) -> None:
        called = threading.Event()
        wd = _IdleWatchdog(0.05, called.set)
        wd.reset()
        assert called.wait(0.5)
        assert wd.triggered.is_set()

    def test_reset_postpones_fire(self) -> None:
        called = threading.Event()
        wd = _IdleWatchdog(0.1, called.set)
        wd.reset()
        time.sleep(0.05)
        wd.reset()
        time.sleep(0.05)
        assert not called.is_set()
        assert called.wait(0.5)
        wd.cancel()

    def test_cancel_prevents_fire(self) -> None:
        called = threading.Event()
        wd = _IdleWatchdog(0.05, called.set)
        wd.reset()
        wd.cancel()
        time.sleep(0.15)
        assert not called.is_set()
        assert not wd.triggered.is_set()

    def test_cancel_idempotent(self) -> None:
        wd = _IdleWatchdog(0.05, lambda: None)
        wd.reset()
        wd.cancel()
        wd.cancel()
