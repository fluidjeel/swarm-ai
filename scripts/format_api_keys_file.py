#!/usr/bin/env python3
"""Reformat api_keys.txt into labeled KEY=VALUE structure (3 providers)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.key_file import format_key_file, parse_key_file

DEFAULT_SOURCE = Path(r"C:\Manasjit\api_keys.txt")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Format api_keys.txt")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Rewrite source file (creates .bak backup)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write formatted file to alternate path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        values = parse_key_file(args.source)
        formatted = format_key_file(values)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    target = args.output or (args.source if args.in_place else args.source.with_suffix(".formatted.txt"))
    if args.in_place:
        backup = args.source.with_suffix(args.source.suffix + ".bak")
        backup.write_text(args.source.read_text(encoding="utf-8"), encoding="utf-8")

    target.write_text(formatted, encoding="utf-8")
    print(f"Wrote formatted key file: {target}")
    if args.in_place:
        print(f"Backup saved: {backup}")
    print("Configured keys: OPENAI_API_KEY, ANTHROPIC_API_KEY, GROK_API_KEY, DEEPSEEK_API_KEY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
