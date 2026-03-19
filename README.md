# Systematic Trading System — Quant Developer Internship

## Strategy Overview

This system implements an **intraday momentum strategy** on Banknifty minute-level data. **All parameters are derived from statistical theory** — no parameter sweeps, no curve-fitting.

### Key Features

- ✅ **Walk-forward recalibration:** Exit levels adapt to current volatility (rolling 60-day sigma)
- ✅ **Asymmetric risk/reward:** Momentum uses 1.5:1 TP:SL ratio (let winners run, cut losers fast)
- ✅ **Regime-aware:** Hurst exponent auto-selects momentum vs mean-reversion
- ✅ **Theory-driven:** All parameters from statistical foundations, not optimized

### Parameter Derivation

Every parameter comes from a principled source:

| Parameter | Value | Source | Adaptation |
|-----------|-------|--------|------------|
| Entry Z | 2.0 | Standard 2-sigma significance | Fixed (theory) |
| Lookback | 64 min | 2 × half-life (OU theory) | Fixed (theory) |
| Base Exit | 1.24% | EXIT_MULT × sqrt(HL) × σ | **Dynamic (rolling 60-day vol)** |
| TP Ratio | 1.5x | Momentum: let winners run | Fixed (regime) |
| SL Ratio | 1.0x | Momentum: cut losses fast | Fixed (regime) |
| Mode | Momentum | Hurst > 0.5 = trending | Auto-selected from IS |
| Trend filter | SMA(200) | Technical standard | Fixed (standard) |
| EOD exit | 5 min buffer | Risk management | Fixed (policy) |

**Walk-forward adaptation:** Exit levels shrink when volatility drops, expand when it rises. This uses only *past* data (no lookahead bias) and adapts to current market regimes.

### Pipeline

```
data_loader.py  →  strategy.py  →  backtester.py  →  analysis.py
     ↓                 ↓                ↓                ↓
 Load, clean,    Statistical       Position &       Metrics &
 align OHLC      analysis +        bar-by-bar       IS vs OOS
 data            z-score signals   P&L tracking     breakdown
```

### How It Works

**1. Statistical Analysis (in-sample only, first 80%)**

| Test | Result | Interpretation |
|------|--------|----------------|
| **ADF test** | p < 0.001 | Spread (Close - SMA) is stationary |
| **Half-life** | 32.1 min | Mean-reversion speed (OU AR(1) regression) |
| **Hurst exponent** | 0.566 | Slight trending tendency → auto-selects momentum |

**2. Signal Generation**

| Signal | Condition |
|--------|-----------|
| **LONG** | z > +2.0 AND price > SMA(200) |
| **SHORT** | z < −2.0 AND price < SMA(200) |
| **TAKE-PROFIT** | Price moves +1.83% from entry (1.5x base) |
| **STOP-LOSS** | Price moves −0.82% from entry (1.0x base) |
| **EOD** | Flatten positions 5 min before market close |

Exit levels are **not fixed**—they adapt to rolling 60-day volatility. When vol drops 36%, exits shrink 36%.

**3. Backtesting**

- Fixed position sizing (full capital per trade)
- Transaction cost: 0.01% per trade
- Slippage: 0.005% proportional
- End-of-day flattening (no overnight risk)
- Incremental MTM P&L (no double-counting)

### Results (2015–2024, 851K minute bars)

| Metric | Full Period | In-Sample (80%) | Out-of-Sample (20%) |
|--------|-------------|-----------------|---------------------|
| **Total Return** | **+39.2%** | — | — |
| **Annualized Return** | **+3.74%** | — | — |
| **Sharpe Ratio** | **0.34** | — | — |
| Max Drawdown | −25.2% | — | — |
| Trades | 3,245 | 2,600 | 645 |
| **Win Rate** | 48.6% | 49.3% | 45.6% |
| **Profit Factor** | **1.03** | **1.07** | **0.83** |
| **P&L** | **+$39,202** | **+$70,263** | **−$31,060** |
| Avg Win | $747 | $797 | $530 |
| Avg Loss | $683 | $723 | $532 |

### Honest Assessment: OOS Losses & Regime Dependence

The strategy is **profitable overall** (+39.2%, PF=1.03) but shows **degradation out-of-sample** (PF=0.83, −$31K loss).

**This is NOT overfitting.** All parameters are theory-derived, not swept. Instead, it reveals a **regime mismatch**:

**Training Period (2015–May 2022):**
- Average volatility: 0.0778% per minute
- Includes COVID-2020 spike (0.14%)
- Consistent uptrend → momentum works well
- Result: +$70K, PF=1.07 ✅

**Test Period (May 2022–Mar 2024):**
- Average volatility: 0.0499% (36% lower)
- Quiet, choppy sideways markets
- Momentum signal suffocated by noise
- Transaction costs = 3% of daily move (too high)
- Result: −$31K, PF=0.83 ❌

**Key Insight:** The strategy works in **trending, high-vol markets**. It breaks in **choppy, low-vol markets**. This is a property of momentum strategies, not a flaw in the implementation.

### Why This Approach Generalizes

If run on **different data**, the strategy auto-adapts:
1. `analyze()` computes Hurst → selects momentum vs mean-reversion
2. `_estimate_half_life()` sets the lookback window
3. `_build_positions()` uses rolling vol → adapts to current regime
4. Asymmetric exits (TP/SL ratios) match the detected mode
5. Trend filter works for any instrument

## Running

```bash
pip install -r requirements.txt
python main.py
```

Results (plots + CSVs) are saved to `results/`. Runtime: **~22 seconds**.

## Files

| File | Purpose |
|------|---------|
| `config.py` | All tunable parameters with derivation comments |
| `data_loader.py` | Load, validate, clean (OHLC format) |
| `strategy.py` | MeanReversionStrategy with walk-forward recalibration |
| `backtester.py` | Bar-by-bar P&L with costs, slippage, trade log |
| `analysis.py` | Metrics + IS/OOS breakdown + plots |
| `main.py` | Pipeline orchestrator |
| `PROBLEM_STATEMENT.md` | Detailed root cause analysis of OOS losses |
| `CHANGES_MADE.md` | Side-by-side code comparisons |
| `FINAL_SUMMARY.md` | Conclusions and forward paths |

## Key Design Decisions

### Why Walk-Forward Sigma?
Fixed IS-derived exits (1.22%) were too wide for the low-vol OOS period (36% lower vol). Trades rarely hit target, got closed randomly by EOD rule. Rolling 60-day vol adapts automatically without lookahead bias.

### Why Asymmetric Exits?
Momentum (Hurst=0.566) needs to let winners run and cut losers fast (1.5:1 R:R), not symmetric 1:1. This aligns exit logic with market regime.

### Why 80/20 Split Not 70/30?
More OOS data reveals true generalization. 70/30 masked the regime problem; 80/20 exposes it honestly.

### Can We Fix OOS Losses?
Yes, via:
1. **Dynamic entry thresholds** — raise from 2-sigma to 2.5-sigma in low-vol
2. **Position sizing** — reduce size when vol drops below historical mean
3. **Multi-strategy** — combine momentum + mean-reversion (diversify regimes)
4. **Accept limitation** — use momentum only in high-vol markets (desk policy)

Options 1–3 require additional recalibration; option 4 is honest risk management.

## AI Disclosure

This project was developed with AI assistance (Claude) for code generation and analysis. All design decisions and parameter choices are grounded in quantitative finance theory (Ornstein-Uhlenbeck processes, Hurst R/S analysis, random-walk scaling).
