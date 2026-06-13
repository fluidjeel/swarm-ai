"""Operator alerting sinks (dead-man's switch, halts, reconciliation breaks).

The engine's stops are synthetic (EC2-managed) until exchange-side bracket/cover
orders are confirmed, so a silent EC2 death leaves a live position unprotected.
Alerting is therefore a P0 safety surface, not a nicety.

This module defines the transport-agnostic ``AlertSink`` protocol and a few
implementations. ``TelegramAlertSink`` posts via stdlib ``urllib`` and is a
no-op when credentials are absent, so it is safe to wire everywhere.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("a2a.alerting")


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class Alert:
    severity: AlertSeverity
    title: str
    detail: str
    context: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        head = f"[{self.severity.value}] {self.title}"
        if not self.context:
            return f"{head}\n{self.detail}"
        ctx = " ".join(f"{k}={v}" for k, v in self.context.items())
        return f"{head}\n{self.detail}\n{ctx}"


@runtime_checkable
class AlertSink(Protocol):
    def send(self, alert: Alert) -> None: ...


class LoggingAlertSink:
    """Always-available sink: routes alerts to the logger by severity."""

    _LEVELS = {
        AlertSeverity.INFO: logging.INFO,
        AlertSeverity.WARNING: logging.WARNING,
        AlertSeverity.CRITICAL: logging.CRITICAL,
    }

    def send(self, alert: Alert) -> None:
        logger.log(self._LEVELS.get(alert.severity, logging.WARNING), alert.render())


class MultiAlertSink:
    """Fan out to multiple sinks. One sink failing never blocks the others."""

    def __init__(self, sinks: list[AlertSink]) -> None:
        self._sinks = sinks

    def send(self, alert: Alert) -> None:
        for sink in self._sinks:
            try:
                sink.send(alert)
            except Exception:  # pragma: no cover - defensive fan-out
                logger.warning("Alert sink %r failed", sink, exc_info=True)


class TelegramAlertSink:
    """Telegram Bot API sink. No-op (logs) when credentials are unset.

    Credentials are read from args or env (``A2A_TELEGRAM_BOT_TOKEN`` /
    ``A2A_TELEGRAM_CHAT_ID``). Network failures are swallowed and logged so
    alerting never throws into the trading loop.
    """

    _API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(
        self,
        *,
        bot_token: str | None = None,
        chat_id: str | None = None,
        timeout_sec: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self._token = bot_token or os.getenv("A2A_TELEGRAM_BOT_TOKEN")
        self._chat_id = chat_id or os.getenv("A2A_TELEGRAM_CHAT_ID")
        self._timeout_sec = timeout_sec
        self._enabled = enabled

    @property
    def configured(self) -> bool:
        return bool(self._enabled and self._token and self._chat_id)

    def send(self, alert: Alert) -> None:
        if not self.configured:
            logger.info("Telegram not configured; alert dropped: %s", alert.render())
            return
        payload = json.dumps(
            {"chat_id": self._chat_id, "text": alert.render()}
        ).encode("utf-8")
        request = urllib.request.Request(
            self._API.format(token=self._token),
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_sec) as resp:
                if resp.status >= 300:
                    logger.warning("Telegram alert HTTP %s", resp.status)
        except (urllib.error.URLError, TimeoutError, OSError):
            logger.warning("Telegram alert delivery failed", exc_info=True)
