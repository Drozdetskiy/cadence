from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys


class ProcessGroupCleanup:
    def __init__(self, process: subprocess.Popen[str]) -> None:
        self._process = process
        self._killed = False

    def kill_process_group(self) -> None:
        if self._killed:
            return
        self._killed = True
        pid = self._process.pid
        if sys.platform == "win32":
            self._process.terminate()
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError, PermissionError:
            return
        try:
            self._process.wait(timeout=0.1)
            return
        except subprocess.TimeoutExpired:
            pass
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)

    def wait(self) -> int:
        self._process.wait()
        self.kill_process_group()
        return self._process.returncode
