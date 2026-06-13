"""OS-level mutex for 5-minute SessionPipeline ticks (HLDD §1.5).

The fcntl/msvcrt lock is released automatically by the OS when the holding
process *dies*. The residual risk is a process that is **alive but hung** (e.g.
blocked on a network call with no timeout): it keeps the lock forever and
starves every future tick. To defend against that we:

1. Pair the lock with a freely-readable metadata sidecar (pid, host, heartbeat).
2. Let the daemon refresh the heartbeat each healthy tick.
3. On a failed acquire, inspect the sidecar; if the heartbeat is older than the
   TTL the holder is presumed hung — we kill its PID (same host only) so the OS
   releases the kernel lock, then retry once.

This is the cross-process backstop to the in-process tick deadline enforced by
``SessionPipeline.run_tick``.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger("a2a.tick_lock")

DEFAULT_LOCK_TTL_SECONDS = 360.0  # > 5-min tick cadence; holder is hung beyond this.


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

    def refresh_heartbeat(self) -> None:
        """Mark the lock as still actively held. No-op by default."""

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


def lock_metadata_is_stale(
    metadata: dict[str, Any] | None,
    *,
    now: float,
    ttl_seconds: float,
) -> bool:
    """Pure predicate: True when the lock holder's heartbeat is older than TTL."""
    if not metadata:
        return False
    heartbeat = metadata.get("heartbeat_at")
    if not isinstance(heartbeat, (int, float)):
        return False
    return (now - float(heartbeat)) > ttl_seconds


def pid_alive(pid: int) -> bool:
    """Cross-platform liveness check for a process id."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill_pid(pid: int) -> None:
    import signal

    sig = signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        logger.warning("Failed to kill stale tick-lock holder pid=%s", pid, exc_info=True)


class FileTickLock(TickLock):
    """
    Cross-process file lock with a stale-holder watchdog.

    Uses fcntl on Linux (EC2) and msvcrt on Windows for developer machines.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        ttl_seconds: float = DEFAULT_LOCK_TTL_SECONDS,
        enable_watchdog: bool = True,
    ) -> None:
        if path is None:
            path = Path(tempfile.gettempdir()) / "a2a-tick.lock"
        self._path = path
        self._meta_path = path.with_suffix(path.suffix + ".meta.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: int | None = None
        self._ttl_seconds = ttl_seconds
        self._enable_watchdog = enable_watchdog
        self._acquired_at: float | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def meta_path(self) -> Path:
        return self._meta_path

    def acquire(self, *, blocking: bool = False) -> None:
        if self._handle is not None:
            raise TickLockError("Tick lock already acquired in this process.")

        try:
            self._raw_acquire(blocking=blocking)
        except TickLockError:
            if self._enable_watchdog and self._try_break_stale_lock():
                self._raw_acquire(blocking=blocking)
            else:
                raise

        self._acquired_at = time.time()
        self._write_metadata()

    def refresh_heartbeat(self) -> None:
        if self._handle is None:
            return
        self._write_metadata()

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
            self._clear_metadata()
            self._acquired_at = None

    # -- internals -------------------------------------------------------

    def _raw_acquire(self, *, blocking: bool) -> None:
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
        except TickLockError:
            raise
        except Exception:
            try:
                os.close(handle)
            except OSError:
                pass
            raise

        self._handle = handle

    def _write_metadata(self) -> None:
        now = time.time()
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "acquired_at": self._acquired_at or now,
            "heartbeat_at": now,
        }
        try:
            self._meta_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            logger.warning("Failed to write tick-lock metadata", exc_info=True)

    def _read_metadata(self) -> dict[str, Any] | None:
        try:
            raw = self._meta_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _clear_metadata(self) -> None:
        try:
            self._meta_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to clear tick-lock metadata", exc_info=True)

    def _try_break_stale_lock(self) -> bool:
        """Kill a hung-but-alive holder so the OS releases the lock. Returns
        True only when a genuinely stale lock was broken."""
        metadata = self._read_metadata()
        if not lock_metadata_is_stale(
            metadata, now=time.time(), ttl_seconds=self._ttl_seconds
        ):
            return False

        assert metadata is not None
        pid = metadata.get("pid")
        host = metadata.get("host")
        if host != socket.gethostname():
            logger.error(
                "Stale tick lock held by pid=%s on remote host=%s; cannot break "
                "from here (handled by reconciliation/halt path).",
                pid,
                host,
            )
            return False

        if isinstance(pid, int) and pid != os.getpid() and pid_alive(pid):
            logger.warning(
                "Breaking stale tick lock: killing hung holder pid=%s (heartbeat "
                "age > %.0fs TTL).",
                pid,
                self._ttl_seconds,
            )
            _kill_pid(pid)
            # Give the OS a moment to reap the process and release the flock.
            for _ in range(25):
                if not pid_alive(pid):
                    break
                time.sleep(0.1)
        else:
            logger.warning(
                "Clearing orphaned stale tick-lock metadata (pid=%s not alive).",
                pid,
            )

        self._clear_metadata()
        return True
