"""Tests for the dead-man's switch and alert sinks."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.observability.alerting import (
    Alert,
    AlertSeverity,
    LoggingAlertSink,
    MultiAlertSink,
    TelegramAlertSink,
)
from src.orchestration.deadman import (
    DeadMansSwitch,
    HeartbeatHealth,
    heartbeat_age_seconds,
    read_last_heartbeat,
)


class _RecordingSink:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


class _ExplodingSink:
    def send(self, alert: Alert) -> None:
        raise RuntimeError("sink down")


def _write_heartbeat(path: Path, *, ts: datetime, tick: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event": "tick_heartbeat",
                    "session_id": "deadman-test",
                    "timestamp": ts.isoformat(),
                    "tick_number": tick,
                }
            )
            + "\n"
        )


class AlertSinkTests(unittest.TestCase):
    def test_logging_sink_never_raises(self) -> None:
        LoggingAlertSink().send(
            Alert(AlertSeverity.CRITICAL, "t", "d", {"a": 1})
        )

    def test_multi_sink_fans_out_and_survives_failure(self) -> None:
        good = _RecordingSink()
        sink = MultiAlertSink([_ExplodingSink(), good])
        sink.send(Alert(AlertSeverity.WARNING, "t", "d"))
        self.assertEqual(len(good.alerts), 1)

    def test_telegram_unconfigured_is_noop(self) -> None:
        sink = TelegramAlertSink(bot_token=None, chat_id=None)
        self.assertFalse(sink.configured)
        sink.send(Alert(AlertSeverity.INFO, "t", "d"))  # must not raise

    def test_alert_render_includes_context(self) -> None:
        rendered = Alert(AlertSeverity.CRITICAL, "Title", "Body", {"k": "v"}).render()
        self.assertIn("[CRITICAL] Title", rendered)
        self.assertIn("k=v", rendered)


class DeadMansSwitchTests(unittest.TestCase):
    def test_read_last_heartbeat_returns_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hb.jsonl"
            now = datetime.now(timezone.utc)
            _write_heartbeat(path, ts=now - timedelta(seconds=30), tick=1)
            _write_heartbeat(path, ts=now, tick=2)
            row = read_last_heartbeat(path)
            self.assertEqual(row["tick_number"], 2)

    def test_read_last_heartbeat_missing_file(self) -> None:
        self.assertIsNone(read_last_heartbeat(Path("does-not-exist.jsonl")))

    def test_heartbeat_age_seconds(self) -> None:
        now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=timezone.utc)
        row = {"timestamp": (now - timedelta(seconds=120)).isoformat()}
        self.assertAlmostEqual(heartbeat_age_seconds(row, now=now), 120.0, places=1)
        self.assertIsNone(heartbeat_age_seconds({"timestamp": "bad"}, now=now))
        self.assertIsNone(heartbeat_age_seconds(None, now=now))

    def test_fresh_heartbeat_is_ok_no_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hb.jsonl"
            now = datetime.now(timezone.utc)
            _write_heartbeat(path, ts=now, tick=5)
            sink = _RecordingSink()
            switch = DeadMansSwitch(
                heartbeat_path=path, alert_sink=sink, max_age_seconds=360.0
            )
            status = switch.check(now=now)
            self.assertEqual(status.health, HeartbeatHealth.OK)
            self.assertEqual(sink.alerts, [])

    def test_stale_heartbeat_trips_critical_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hb.jsonl"
            now = datetime.now(timezone.utc)
            _write_heartbeat(path, ts=now - timedelta(seconds=600), tick=9)
            sink = _RecordingSink()
            switch = DeadMansSwitch(
                heartbeat_path=path, alert_sink=sink, max_age_seconds=360.0
            )
            status = switch.check(now=now)
            self.assertEqual(status.health, HeartbeatHealth.STALE)
            self.assertEqual(len(sink.alerts), 1)
            self.assertEqual(sink.alerts[0].severity, AlertSeverity.CRITICAL)

    def test_missing_heartbeat_trips_critical_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hb.jsonl"  # never written
            sink = _RecordingSink()
            switch = DeadMansSwitch(heartbeat_path=path, alert_sink=sink)
            status = switch.check()
            self.assertEqual(status.health, HeartbeatHealth.MISSING)
            self.assertEqual(len(sink.alerts), 1)
            self.assertEqual(sink.alerts[0].severity, AlertSeverity.CRITICAL)


if __name__ == "__main__":
    unittest.main()
