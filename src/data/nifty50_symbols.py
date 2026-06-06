"""Load cached Nifty 50 constituent symbols for breadth proxy."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SYMBOLS_PATH = Path(__file__).resolve().parents[2] / "data" / "nifty50_symbols.json"


@lru_cache(maxsize=1)
def load_nifty50_symbols() -> tuple[str, ...]:
    if not SYMBOLS_PATH.exists():
        raise FileNotFoundError(f"Nifty 50 symbol list not found: {SYMBOLS_PATH}")
    payload = json.loads(SYMBOLS_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"Invalid Nifty 50 symbol list in {SYMBOLS_PATH}")
    return tuple(str(symbol) for symbol in payload)
