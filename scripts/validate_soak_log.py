#!/usr/bin/env python3
"""Validate a paper soak JSONL log against SOAK_TEST_RECIPE pass criteria."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def validate_soak_log(path: Path, *, smoke: bool = False) -> tuple[bool, list[str]]:
    rows = _load_rows(path)
    issues: list[str] = []

    approves = sum(1 for row in rows if row.get("event") == "PAPER_APPROVE")
    exits = sum(1 for row in rows if row.get("event") == "PAPER_EXIT")
    order_acks = sum(1 for row in rows if row.get("event") == "PAPER_ORDER_ACK")
    tick_errors = sum(1 for row in rows if row.get("event") == "paper_tick_error")
    total_ticks = sum(1 for row in rows if row.get("event") == "paper_tick")
    complete = any(row.get("event") == "paper_soak_complete" for row in rows)

    if smoke:
        if tick_errors > 0:
            issues.append(f"smoke: paper_tick_error={tick_errors} (want 0)")
        if approves > 1:
            issues.append(f"smoke: PAPER_APPROVE={approves} (want 0-1)")
    else:
        if not complete:
            issues.append("missing paper_soak_complete row")
        if tick_errors > max(1, int(total_ticks * 0.01)):
            issues.append(
                f"paper_tick_error={tick_errors} exceeds 1% of ticks ({total_ticks})"
            )
        if approves > 3:
            issues.append(f"PAPER_APPROVE={approves} (expect 0-3 over 4h)")
        if exits > approves:
            issues.append(f"PAPER_EXIT={exits} exceeds PAPER_APPROVE={approves}")
        if approves > 0 and order_acks < approves * 4:
            issues.append(
                f"PAPER_ORDER_ACK={order_acks} (expect >= {approves * 4} for iron condor)"
            )

    return len(issues) == 0, issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate paper soak JSONL log.")
    parser.add_argument("log_path", type=Path, help="Path to logs/paper_soak/*.jsonl")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Apply 30-minute smoke pass criteria instead of 4h soak.",
    )
    args = parser.parse_args(argv)

    if not args.log_path.exists():
        print(f"ERROR: file not found: {args.log_path}", file=sys.stderr)
        return 2

    passed, issues = validate_soak_log(args.log_path, smoke=args.smoke)
    if passed:
        print(f"PASS: {args.log_path}")
        return 0

    print(f"FAIL: {args.log_path}")
    for issue in issues:
        print(f"  - {issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
