"""
Centralized configuration for the trading system.

Parameter philosophy:
  - Entry Z, lookback, and trend filter use STANDARD values from
    quantitative finance (2-sigma, 2x half-life, 200-bar SMA).
  - Target/stop percentages are DERIVED from in-sample volatility
    at runtime (not hand-tuned) to avoid overfitting.
  - Strategy mode (momentum vs mean-reversion) is AUTO-SELECTED
    based on the Hurst exponent computed in-sample.
"""

# --- Data Pipeline --------------------------------------------------------
DATA_PATH = "data/banknifty_candlestick_data.csv"
DATETIME_COL = None
PRICE_COL = "Close"
MAX_FFILL_GAP = 5
OUTLIER_METHOD = "mad"
OUTLIER_THRESHOLD = 50.0             # Very conservative: only true data errors
OUTLIER_REPLACE_WINDOW = 10
DROP_MISSING_PCT = 0.20

# --- Strategy (common) ----------------------------------------------------
TRAIN_FRAC = 0.70
ENTRY_Z = 2.0                        # Standard 2-sigma threshold
EXIT_Z = 0.5                         # Fallback z-score exit
STOP_Z = 3.5                         # Fallback z-score stop
LOOKBACK = None                      # Auto-derive from half-life (2 * HL)
MIN_LOOKBACK = 20
MAX_LOOKBACK = 120

# --- Strategy (multi-asset pairs trading) ---------------------------------
COINT_PVALUE = 0.05
CORR_PREFILTER = 0.50
MAX_PAIRS = 5

# --- Strategy (single-asset) ---------------------------------------------
STRATEGY_MODE = "auto"               # "auto", "momentum", or "mean_reversion"
CLOSE_EOD = True
EOD_EXIT_BUFFER = 5
USE_TREND_FILTER = True              # Only trade in direction of trend
TREND_LOOKBACK = 200                 # Standard 200-bar SMA
EXIT_MULT = 3.0                      # target/stop = EXIT_MULT * sqrt(HL) * sigma_minute
                                     #   3-sigma: expected move over 1 half-life that
                                     #   separates signal from noise (random-walk theory)

# --- Backtester ------------------------------------------------------------
INITIAL_CAPITAL = 100_000.0
TRANSACTION_COST_BPS = 1.0           # 0.01 %
SLIPPAGE_BPS = 0.5                   # 0.005 %

# --- Analysis --------------------------------------------------------------
MINUTES_PER_DAY = 375
TRADING_DAYS_PER_YEAR = 252
MINUTES_PER_YEAR = TRADING_DAYS_PER_YEAR * MINUTES_PER_DAY
RESULTS_DIR = "results"
PLOT_SUBSAMPLE = 5_000               # Max points per plot
