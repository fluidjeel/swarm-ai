"""Tests for SessionPipeline tick mutex."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from src.orchestration.tick_lock import (
    FileTickLock,
    NullTickLock,
    TickLockError,
    lock_metadata_is_stale,
    pid_alive,
)

_DEAD_PID = 2**31 - 1  # almost certainly not a running process


class TickLockTests(unittest.TestCase):
    def test_null_lock_allows_reentrant_acquire_release(self) -> None:
        lock = NullTickLock()
        with lock:
            lock.acquire(blocking=False)
        lock.release()

    def test_file_lock_blocks_second_acquire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tick.lock"
            lock_a = FileTickLock(path)
            lock_b = FileTickLock(path)

            lock_a.acquire(blocking=False)
            try:
                with self.assertRaises(TickLockError):
                    lock_b.acquire(blocking=False)
            finally:
                lock_a.release()

            lock_b.acquire(blocking=False)
            lock_b.release()

    def test_file_lock_blocks_concurrent_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tick.lock"
            holder = FileTickLock(path)
            holder.acquire(blocking=False)
            blocked = threading.Event()

            def _try_acquire() -> None:
                challenger = FileTickLock(path)
                try:
                    challenger.acquire(blocking=False)
                except TickLockError:
                    blocked.set()
                    return
                finally:
                    challenger.release()

            thread = threading.Thread(target=_try_acquire)
            thread.start()
            thread.join(timeout=2.0)
            holder.release()

            self.assertTrue(blocked.is_set())


class TickLockWatchdogTests(unittest.TestCase):
    def test_lock_metadata_is_stale_predicate(self) -> None:
        now = 1000.0
        self.assertFalse(lock_metadata_is_stale(None, now=now, ttl_seconds=360.0))
        self.assertFalse(
            lock_metadata_is_stale({"heartbeat_at": now - 10}, now=now, ttl_seconds=360.0)
        )
        self.assertTrue(
            lock_metadata_is_stale({"heartbeat_at": now - 400}, now=now, ttl_seconds=360.0)
        )
        self.assertFalse(
            lock_metadata_is_stale({"heartbeat_at": "bad"}, now=now, ttl_seconds=360.0)
        )

    def test_pid_alive(self) -> None:
        self.assertTrue(pid_alive(os.getpid()))
        self.assertFalse(pid_alive(_DEAD_PID))
        self.assertFalse(pid_alive(-1))

    def test_acquire_writes_and_release_clears_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tick.lock"
            lock = FileTickLock(path)
            lock.acquire(blocking=False)
            try:
                self.assertTrue(lock.meta_path.exists())
                meta = json.loads(lock.meta_path.read_text(encoding="utf-8"))
                self.assertEqual(meta["pid"], os.getpid())
                self.assertEqual(meta["host"], socket.gethostname())
            finally:
                lock.release()
            self.assertFalse(lock.meta_path.exists())

    def test_try_break_clears_orphaned_stale_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tick.lock"
            lock = FileTickLock(path, ttl_seconds=1.0)
            lock.meta_path.write_text(
                json.dumps(
                    {
                        "pid": _DEAD_PID,
                        "host": socket.gethostname(),
                        "acquired_at": 0.0,
                        "heartbeat_at": 0.0,
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(lock._try_break_stale_lock())
            self.assertFalse(lock.meta_path.exists())

    def test_try_break_keeps_fresh_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tick.lock"
            lock = FileTickLock(path, ttl_seconds=360.0)
            now = time.time()
            lock.meta_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "acquired_at": now,
                        "heartbeat_at": now,
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(lock._try_break_stale_lock())
            self.assertTrue(lock.meta_path.exists())

    def test_fresh_holder_not_broken_by_challenger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tick.lock"
            holder = FileTickLock(path)
            holder.acquire(blocking=False)
            try:
                challenger = FileTickLock(path, ttl_seconds=0.01)
                with self.assertRaises(TickLockError):
                    challenger.acquire(blocking=False)
            finally:
                holder.release()


if __name__ == "__main__":
    unittest.main()
