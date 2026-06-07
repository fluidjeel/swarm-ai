"""Tests for SessionPipeline tick mutex."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from src.orchestration.tick_lock import FileTickLock, NullTickLock, TickLockError


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


if __name__ == "__main__":
    unittest.main()
