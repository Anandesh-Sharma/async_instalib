"""Exclusive POSIX ownership of a session file across processes."""

import os
from pathlib import Path

from ..exceptions import SessionInUse


class SessionLease:
    """Hold an advisory lock on a stable sidecar inode until the client closes."""

    def __init__(self, path: Path) -> None:
        import fcntl

        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(self._fd, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = -1
            raise SessionInUse("This session file is already open in another client.") from None
        except BaseException:
            os.close(self._fd)
            self._fd = -1
            raise

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
