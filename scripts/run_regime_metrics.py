#!/usr/bin/env python3
"""
Epic 2.1 smoke test: fetch regime metrics from Fyers and print strict JSON.

Examples:
  # One-shot smoke test
  python scripts/run_regime_metrics.py

  # Poll every 5 minutes for 4 hours (Epic 2.1 soak harness)
  python scripts/run_regime_metrics.py --loop --interval-secs 300 --duration-secs 14400

Requires FYERS_APP_ID and FYERS_ACCESS_TOKEN in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.fyers_provider import FyersMarketDataProvider
from src.features.regime_metrics import RegimeMetricsError, compute_regime_metrics, poll_regime_metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run A2A regime metrics via Fyers")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Poll continuously instead of a single snapshot",
    )
    parser.add_argument(
        "--interval-secs",
        type=int,
        default=300,
        help="Polling interval in seconds (default: 300 = 5 minutes)",
    )
    parser.add_argument(
        "--duration-secs",
        type=float,
        default=0,
        help="Stop after N seconds when --loop is set (0 = run until Ctrl+C)",
    )
    parser.add_argument(
        "--timeout-secs",
        type=float,
        default=15.0,
        help="Per-tick API timeout budget",
    )
    parser.add_argument(
        "--option-symbol",
        default="NSE:NIFTY50-INDEX",
        help="Underlying index symbol for PCR calculation",
    )
    parser.add_argument(
        "--log-memory",
        action="store_true",
        help="Include RSS memory stats in loop logs",
    )
    return parser.parse_args()


async def _run_once(args: argparse.Namespace) -> int:
    provider = FyersMarketDataProvider(request_timeout_sec=args.timeout_secs)
    metrics = await compute_regime_metrics(
        provider,
        option_symbol=args.option_symbol,
        request_timeout_sec=args.timeout_secs,
    )
    print(json.dumps(metrics, separators=(",", ":")))
    return 0


async def _run_loop(args: argparse.Namespace) -> int:
    provider = FyersMarketDataProvider(request_timeout_sec=args.timeout_secs)
    duration = args.duration_secs if args.duration_secs > 0 else None

    def _on_tick(payload: dict) -> None:
        print(json.dumps(payload, separators=(",", ":")), flush=True)

    summary = await poll_regime_metrics(
        provider,
        interval_secs=args.interval_secs,
        duration_secs=duration,
        option_symbol=args.option_symbol,
        request_timeout_sec=args.timeout_secs,
        log_memory=args.log_memory,
        on_tick=_on_tick,
    )
    print(
        json.dumps(
            {
                "event": "poll_complete",
                "successful_ticks": summary["successful_ticks"],
                "failed_ticks": summary["failed_ticks"],
                "elapsed_secs": round(summary["elapsed_secs"], 2),
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0 if summary["failed_ticks"] == 0 or summary["successful_ticks"] > 0 else 1


async def _async_main(args: argparse.Namespace) -> int:
    if args.loop:
        return await _run_loop(args)
    return await _run_once(args)


def main() -> int:
    args = parse_args()
    if args.interval_secs < 1:
        print("ERROR: --interval-secs must be >= 1", file=sys.stderr)
        return 1

    try:
        return asyncio.run(_async_main(args))
    except RegimeMetricsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
