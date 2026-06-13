# Quantitative Strategy & Risk Mandate: A2A v5.0

> v4.1 mandate (35–45% CAGR, 1-lot): `../v4.1/04_finance_guru.md`

## 1. Core Hypothesis

Alpha in NIFTY / BANKNIFTY comes from **institutional liquidity signals** (breadth, PCR momentum, VIX structure), not retail price patterns. v5 adds **compounding mechanics** (fractional sizing, WebSocket reaction) without surrendering the white-box, deterministic execution model.

**Regulatory:** SEBI algo framework — execution is auditable Python math; LLMs tune parameters offline only.

## 2. Capital Allocation

| Bucket | Amount | Mandate |
|--------|--------|---------|
| Liquid BeES reserve | ₹2,50,000 | Unbreachable; pledged for margin; ~5–7% yield |
| Core book (v4.1 Track 1) | ₹5,00,000 | Defined-risk spreads + condors; 1-lot until soak sign-off |
| Edge lab (v5 Tier C) | ₹1,00,000 | Ring-fenced; 0-DTE / fractional experiments **after Gate 1** |

**Session breaker (all buckets):** −₹8,000 realized → HALT new entries.

## 3. Feature Pipeline

### v4.1 (running today)

5-minute REST feature refresh: A/D ratio, VIX–ATR divergence, expiry-weighted PCR momentum, local Greeks.

### v5 (target)

Continuous WebSocket sliding window; same features at sub-minute resolution for premium stops and breakout entries.

### Threshold evolution

| Signal | v4.1 | v5 target |
|--------|------|-----------|
| PCR momentum (breakout) | ±0.02 | ±0.12 |
| IV gate | None | Block premium sell if IV &lt; 30th pct |
| Friction | Per-leg ₹40 | Same + EV gate (2× friction min profit) |

## 4. Risk Guardrails

### Entry

- Stale quote abort: &gt; 10 NIFTY points vs snapshot.
- Undefined risk: naked shorts **blocked** until Epic 5 guardrails documented in `PIVOT_DECISION.md`.
- Friction EV: reject when estimated max profit &lt; 2 × round-trip friction.
- IV percentile: no iron condors / straddles in bottom 30% IV (Epic 1).

### Sizing

| Phase | Rule |
|-------|------|
| Core book (now) | 1 lot + `allowed_lots` step scaling above ₹6L |
| v5 fractional (gated) | 2.5–3.0% of risk capital per trade; Expectancy Controller cuts to 1% on 8% rolling DD |

### Exit

| Book | Mechanism |
|------|-----------|
| v4.1 (now) | ATR stop, regime flip, theta capture |
| v5 (target) | Premium-denominated: debit TP 75%, credit stop 1.5–2× credit, 0-DTE per-leg trails |

## 5. Return Expectations (honest)

| Track | CAGR target | Max DD target | Status |
|-------|-------------|---------------|--------|
| v4.1 core | 35–45% | &lt;15% | Baseline proof-of-architecture |
| v5 stretch | 100% | 15% survival floor via Expectancy Controller | **Hypothesis** — requires Gate 1 proof |

100% CAGR on full ₹6L with multi-leg friction (₹80–₹160/round-trip) is **not assumed** until paper MTM demonstrates positive expectancy.

## 6. Agent 7 — Expectancy Controller

- Computes 14-day Sharpe, drawdown, slippage vs friction model.
- Auto-throttles `risk_config.json` on breach; never deploys code.
- All scale-up / restore requires Telegram HITL before next session.

## 7. Primary Goals

1. **Immediate:** Finish Truth Engine; prove edge on True MTM paper (Gate 1).
2. **Near-term:** WebSocket infra without increasing sizing (Gate 2).
3. **Strategic:** Package middleware as B2B IaaS after multi-regime live proof.
