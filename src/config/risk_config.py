"""Runtime risk thresholds for deterministic agents and gatekeeper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.context import STALE_QUOTE_POINTS


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vix_choppy_threshold: float = Field(default=18.0, gt=0.0)
    ad_trend_up_threshold: float = Field(default=1.5, gt=0.0)
    ad_trend_down_threshold: float = Field(default=0.7, gt=0.0)
    pcr_bull_threshold: float = Field(default=0.12)
    pcr_bear_threshold: float = Field(default=-0.12)
    iv_percentile_min: float = Field(default=30.0, ge=0.0, le=100.0)
    vix_low_vol_floor: float = Field(default=13.0, gt=0.0)
    credit_stop_multiplier: float = Field(default=1.5, ge=1.5, le=2.0)
    range_divergence_band: float = Field(default=0.10, ge=0.0)
    stale_quote_points: float = Field(default=STALE_QUOTE_POINTS, gt=0.0)
    max_spread_pct: float = Field(default=0.05, gt=0.0)
    max_gamma: float = Field(default=0.05, gt=0.0)
    max_lots_per_trade: int = Field(default=4, ge=1)
    max_loss_per_trade_inr: float = Field(default=4000.0, gt=0.0)
    max_loss_per_day_inr: float = Field(default=8000.0, gt=0.0)
    delta_target_short_put: float = Field(default=-0.30, ge=-1.0, le=0.0)
    delta_target_short_call: float = Field(default=0.30, ge=0.0, le=1.0)
    delta_tolerance: float = Field(default=0.10, gt=0.0, le=1.0)
    max_dte_for_entry: int = Field(default=7, ge=0, le=45)
    min_dte_for_entry: int = Field(default=1, ge=0, le=45)
    wing_width_points: int = Field(default=200, ge=50, le=1000)
    risk_free_rate: float = Field(default=0.065, ge=0.0, le=0.20)
    dividend_yield: float = Field(default=0.0, ge=0.0, le=0.10)
    greeks_price_side: Literal["mid", "ask"] = Field(default="mid")
    iv_solver_max_iter: int = Field(default=50, ge=1, le=200)
    iv_tolerance: float = Field(default=1e-5, ge=1e-9, le=1e-2)


def load_risk_config(path: Path | None = None) -> RiskConfig:
    if path is None:
        path = Path("config/risk_config.json")
    if not path.exists():
        return RiskConfig()
    with path.open(encoding="utf-8") as handle:
        return RiskConfig(**json.load(handle))
