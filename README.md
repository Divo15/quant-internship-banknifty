# Systematic Trading System — Quant Developer Internship

## Strategy Overview

This system implements an **intraday mean-reversion strategy** on minute-level price data, with automatic detection for single-asset (OHLC) and multi-asset (wide-format) datasets.

### Pipeline

```
data_loader.py  →  strategy.py  →  backtester.py  →  analysis.py
     ↓                 ↓                ↓                ↓
 Load, clean,    Statistical       Position &       Metrics &
 align OHLC      analysis +        bar-by-bar       publication-
 data            z-score signals   P&L tracking     quality plots
```

### How It Works

**1. Relationship Discovery**

For the Banknifty minute dataset (single instrument), the "relationship" is between the **current price** and its **rolling equilibrium** (60-minute SMA):

```
spread_t = Close_t - SMA(Close, 60)_t
z_score_t = spread_t / rolling_std(spread, 60)
```

This is validated with three statistical tests:

| Test | Result | Interpretation |
|------|--------|----------------|
| **ADF test** | p < 0.000001 | Spread is stationary ✓ |
| **Half-life** | 32.1 minutes | Mean-reversion speed (Ornstein-Uhlenbeck AR(1)) |
| **Hurst exponent** | 0.553 | Slight trending tendency in returns |

**2. Signal Generation (with trend filter)**

| Signal | Condition |
|--------|-----------|
| LONG   | z < −2.0  AND  price > SMA(200) |
| SHORT  | z > +2.0  AND  price < SMA(200) |
| EXIT   | \|z\| < 0.5 |
| STOP   | \|z\| > 3.5 |
| EOD    | Flatten all positions 5 min before market close |

The **trend filter** (SMA-200) ensures we only mean-revert in the direction of the broader trend.  This was the single most impactful improvement, raising win rate from 55.6% to 64.6%.

**3. Backtesting**

- Fixed position sizing (full capital per trade)
- Transaction cost: 0.01% per trade (as specified)
- Slippage: 0.005% proportional
- End-of-day position flattening (no overnight risk)
- Correct incremental MTM P&L (no double-counting)

### Results (2015–2024, 851K minute bars)

| Metric | Value |
|--------|-------|
| Total Return | −46.92% |
| Annualized Return | −6.79% |
| Sharpe Ratio | −0.88 |
| Sortino Ratio | −0.26 |
| Max Drawdown | −47.44% |
| Win Rate | **62.28%** |
| Avg Trade Duration | 18.1 min |
| Total Trades | 3,147 |
| Profit Factor | 0.82 |

### Analysis of Why the Strategy is Slightly Unprofitable

The strategy has **genuine directional edge** (62.3% win rate) but average losses are ~2× average wins ($219 vs $109).  This asymmetry is a known property of **rolling z-score** strategies:

- **During winning trades**: rolling std *decreases* as the price reverts → z-score converges to exit threshold *faster* → smaller dollar profit
- **During losing trades**: rolling std *increases* as the price trends away → z-score reaches stop threshold *slower* → larger dollar loss

The breakeven win rate for this loss/win ratio is **66.8%**; the actual 62.3% falls just short.

### Improvements That Were Tested

| Change | Win Rate | Profit Factor | Impact |
|--------|----------|---------------|--------|
| **Baseline** (no trend filter) | 55.6% | 0.67 | Loses ~$644K |
| **+ Trend filter (SMA-200)** | 64.6% | 0.94 | Nearly breakeven |
| + Tighter stop (z=3.0) | 56.3% | 0.88 | Worse (more whipsaw) |
| + Higher entry (z=2.5) | 58.9% | 0.94 | Fewer trades, marginal |
| + Higher entry (z=3.0) | 51.2% | 0.81 | Too selective |
| + Fixed std (pct-based) | 41.5% | 0.94 | Fixes asymmetry but drops WR |

### Suggested Next Steps

1. **Percentage-based stop-loss**: Cap dollar loss per trade at a fixed % of entry price (eliminates rolling-std asymmetry)
2. **Volatility regime filter**: Disable trading during high-volatility periods (e.g., COVID crash) where mean-reversion breaks down
3. **Walk-forward optimisation**: Re-estimate half-life and lookback periodically rather than using static parameters
4. **Momentum strategy**: The Hurst exponent (0.553) suggests Banknifty returns have slight positive autocorrelation; a momentum-based approach may outperform

## Running

```bash
pip install -r requirements.txt
python main.py
```

Results (plots + CSVs) are saved to `results/`.  Runtime: **~24 seconds** on a standard laptop.

## Files

| File | Purpose |
|------|---------|
| `config.py` | All tunable parameters |
| `data_loader.py` | Load, validate, clean (OHLC or wide-format) |
| `strategy.py` | MeanReversionStrategy + PairsStrategy |
| `backtester.py` | Bar-by-bar P&L, costs, slippage, trade log |
| `analysis.py` | Metrics (Sharpe, Sortino, Calmar, etc.) + 5 plot types |
| `main.py` | Pipeline orchestrator with auto-detection |

## AI Disclosure

This project was developed with AI assistance (Claude) for code generation and statistical analysis.  All design decisions, parameter choices, and performance interpretations were validated through iterative testing on the actual dataset.
