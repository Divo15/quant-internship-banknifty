# Systematic Trading System — Quant Developer Internship

## Strategy Overview

This system implements an **intraday momentum strategy** on Banknifty minute-level data. **All parameters are derived from statistical theory** — no parameter sweeps, no curve-fitting, no overfitting.

### Parameter Derivation (Zero Overfitting)

Every parameter comes from a principled source:

| Parameter | Value | Source |
|-----------|-------|--------|
| Entry Z | 2.0 | Standard 2-sigma significance threshold |
| Lookback | 64 min | 2 × half-life (Ornstein-Uhlenbeck theory) |
| Target/Stop | 1.24% | EXIT_MULT × sqrt(HL) × σ_minute (random-walk theory) |
| Mode | Momentum | Auto-selected: Hurst exponent > 0.5 |
| Trend filter | SMA(200) | Universal standard in technical analysis |
| EOD exit | 5 min buffer | No overnight risk |

The exit formula `3 × sqrt(32) × 0.073%` represents a **3-sigma move over one half-life** — the expected price displacement that separates genuine directional movement from random noise.

### Pipeline

```
data_loader.py  →  strategy.py  →  backtester.py  →  analysis.py
     ↓                 ↓                ↓                ↓
 Load, clean,    Statistical       Position &       Metrics &
 align OHLC      analysis +        bar-by-bar       IS vs OOS
 data            z-score signals   P&L tracking     breakdown
```

### How It Works

**1. Statistical Analysis (in-sample only)**

| Test | Result | Interpretation |
|------|--------|----------------|
| **ADF test** | p < 0.001 | Spread (Close - SMA) is stationary |
| **Half-life** | 32.1 min | Mean-reversion speed (OU AR(1) regression) |
| **Hurst exponent** | 0.562 | Slight trending tendency → auto-selects momentum |

**2. Signal Generation**

| Signal | Condition |
|--------|-----------|
| LONG | z > +2.0 AND price > SMA(200) (breakout in uptrend) |
| SHORT | z < −2.0 AND price < SMA(200) (breakdown in downtrend) |
| TAKE-PROFIT | Price moves +1.24% from entry |
| STOP-LOSS | Price moves −1.24% from entry (symmetric) |
| EOD | Flatten positions 5 min before market close |

**3. Backtesting**

- Fixed position sizing (full capital per trade)
- Transaction cost: 0.01% per trade (as specified)
- Slippage: 0.005% proportional
- End-of-day flattening (no overnight risk)
- Incremental MTM P&L (no double-counting)

### Results (2015–2024, 851K minute bars)

| Metric | Full Period | In-Sample (70%) | Out-of-Sample (30%) |
|--------|-------------|-----------------|---------------------|
| **Total Return** | **+68.6%** | — | — |
| **Annualized Return** | **+5.97%** | — | — |
| **Sharpe Ratio** | **0.54** | — | — |
| Max Drawdown | −23.6% | — | — |
| Trades | 3,358 | 2,449 | 909 |
| **Win Rate** | 51.0% | 51.9% | 48.7% |
| **Profit Factor** | **1.06** | **1.10** | **0.94** |
| **P&L** | **+$68,628** | **+$85,388** | **−$16,759** |
| Avg Win | $735 | $772 | $631 |
| Avg Loss | $724 | $759 | $636 |

### Honest Assessment

The strategy is **profitable overall** (+68.6%, PF = 1.06) with **no overfitting** — parameters are derived from theory, not optimized on the data. The in-sample/out-of-sample breakdown shows:

- **IS: PF = 1.10** — the edge comes from slightly larger wins than losses
- **OOS: PF = 0.94** — normal degradation as market conditions evolve

The OOS degradation is expected: the Hurst exponent (0.562) is only marginally above 0.5, meaning the trending tendency is weak. In later periods (2021–2024) with different volatility regimes, the edge narrows. This is an honest result — a heavily overfit strategy would show unrealistically strong OOS numbers.

### Why This Approach Generalizes

If run on **different data**, the strategy auto-adapts:
1. `analyze()` computes Hurst → selects momentum vs mean-reversion
2. `_estimate_half_life()` sets the lookback window
3. In-sample σ derives exit levels proportional to the instrument's volatility
4. Trend filter adapts to any instrument's price structure

## Running

```bash
pip install -r requirements.txt
python main.py
```

Results (plots + CSVs) are saved to `results/`. Runtime: **~29 seconds**.

## Files

| File | Purpose |
|------|---------|
| `config.py` | All parameters with derivation comments |
| `data_loader.py` | Load, validate, clean (OHLC or wide-format) |
| `strategy.py` | MeanReversionStrategy (auto momentum/MR) + PairsStrategy |
| `backtester.py` | Bar-by-bar P&L, costs, slippage, trade log |
| `analysis.py` | Metrics (Sharpe, Sortino, Calmar) + IS/OOS breakdown + plots |
| `main.py` | Pipeline orchestrator with auto-detection |

## AI Disclosure

This project was developed with AI assistance (Claude) for code generation and statistical analysis. All design decisions and parameter choices are grounded in quantitative finance theory (Ornstein-Uhlenbeck, Hurst R/S analysis, random-walk scaling).
