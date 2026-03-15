"""
Centralized configuration for the trading system.
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
ENTRY_Z = 2.0                        # Enter at 2-sigma deviation
EXIT_Z = 0.5                         # Exit when z reverts within 0.5-sigma
STOP_Z = 3.5                         # 1.5-sigma beyond entry
LOOKBACK = 60                        # ~2x half-life; balances responsiveness vs noise
MIN_LOOKBACK = 20
MAX_LOOKBACK = 120

# --- Strategy (multi-asset pairs trading) ---------------------------------
COINT_PVALUE = 0.05
CORR_PREFILTER = 0.50
MAX_PAIRS = 5

# --- Strategy (single-asset mean-reversion) -------------------------------
CLOSE_EOD = True
EOD_EXIT_BUFFER = 5
USE_TREND_FILTER = True              # Only mean-revert in direction of trend
TREND_LOOKBACK = 200                 # SMA period for trend determination

# --- Backtester ------------------------------------------------------------
INITIAL_CAPITAL = 100_000.0
TRANSACTION_COST_BPS = 1.0           # 0.01 %
SLIPPAGE_BPS = 0.5                   # 0.005 %

# --- Analysis --------------------------------------------------------------
MINUTES_PER_DAY = 375
TRADING_DAYS_PER_YEAR = 252
MINUTES_PER_YEAR = TRADING_DAYS_PER_YEAR * MINUTES_PER_DAY
RESULTS_DIR = "results"
PLOT_SUBSAMPLE = 10_000              # Max points per plot
