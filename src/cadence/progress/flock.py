from __future__ import annotations

import sys
from typing import IO

if sys.platform != "win32":
    import fcntl

    def lock_file(f: IO[str]) -> None:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "another rlx instance is already using this progress file"
            ) from None

    def unlock_file(f: IO[str]) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

else:

    def lock_file(f: IO[str]) -> None:  # type: ignore[misc]
        pass

    def unlock_file(f: IO[str]) -> None:  # type: ignore[misc]
        pass
