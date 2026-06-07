"""OS-level mutex for 5-minute SessionPipeline ticks (HLDD §1.5)."""

from __future__ import annotations

import os
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path


class TickLockError(RuntimeError):
    """Raised when the pipeline tick lock is already held."""


class TickLock(ABC):
    """Exclusive lock preventing concurrent intraday tick execution."""

    @abstractmethod
    def acquire(self, *, blocking: bool = False) -> None:
        """Acquire the lock; raise TickLockError when non-blocking acquire fails."""

    @abstractmethod
    def release(self) -> None:
        """Release the lock if held by this process."""

    def __enter__(self) -> TickLock:
        self.acquire(blocking=False)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class NullTickLock(TickLock):
    """No-op lock for unit tests and local debugging."""

    def acquire(self, *, blocking: bool = False) -> None:
        return None

    def release(self) -> None:
        return None


class FileTickLock(TickLock):
    """
    Cross-process file lock.

    Uses fcntl on Linux (EC2) and msvcrt on Windows for developer machines.
    """

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            path = Path(tempfile.gettempdir()) / "a2a-tick.lock"
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self, *, blocking: bool = False) -> None:
        if self._handle is not None:
            raise TickLockError("Tick lock already acquired in this process.")

        handle = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if sys.platform == "win32":
                import msvcrt

                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                try:
                    msvcrt.locking(handle, mode, 1)
                except OSError as exc:
                    os.close(handle)
                    raise TickLockError(
                        f"Another SessionPipeline tick holds {self._path}"
                    ) from exc
            else:
                import fcntl

                flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                try:
                    fcntl.flock(handle, flags)
                except BlockingIOError as exc:
                    os.close(handle)
                    raise TickLockError(
                        f"Another SessionPipeline tick holds {self._path}"
                    ) from exc
        except Exception:
            if self._handle is None:
                try:
                    os.close(handle)
                except OSError:
                    pass
            raise

        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return

        handle = self._handle
        self._handle = None
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)
