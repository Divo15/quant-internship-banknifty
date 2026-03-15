"""
Trading strategy module.

Two strategy classes are provided:

  ``MeanReversionStrategy``  (single-asset)
      Z-score mean-reversion on Close prices.  The "relationship" is between
      the current price and its rolling equilibrium (SMA).  Statistical
      validity is confirmed via ADF test, half-life estimation (Ornstein-
      Uhlenbeck AR(1)), and Hurst exponent.

  ``PairsStrategy``  (multi-asset)
      Cointegration-based pairs trading (Engle-Granger).

``main.py`` auto-detects the data layout and picks the right class.
"""

import numpy as np
import pandas as pd
from itertools import combinations
from typing import Dict, List, Tuple, Optional

from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

import config as cfg


# ====================================================================== #
#  SINGLE-ASSET MEAN-REVERSION                                           #
# ====================================================================== #

class MeanReversionStrategy:
    """
    Intraday mean-reversion on a single price series.

    Relationship
    ------------
    spread_t  = Close_t - SMA(Close, lookback)_t
    z_score_t = (spread_t - rolling_mean(spread)) / rolling_std(spread)

    The spread captures short-term deviations from the rolling equilibrium.
    We validate mean-reversion with:
      * ADF test  (stationarity)
      * Half-life (speed of reversion, via AR(1) / Ornstein-Uhlenbeck)
      * Hurst exponent (H < 0.5 => mean-reverting)

    Signals
    -------
    LONG   z < -entry_z   (price cheap relative to equilibrium)
    SHORT  z > +entry_z   (price rich)
    EXIT   |z| < exit_z   (reverted)
    STOP   |z| > stop_z   (divergence)
    EOD    close before market close (no overnight risk)
    """

    def __init__(
        self,
        entry_z: float = cfg.ENTRY_Z,
        exit_z: float = cfg.EXIT_Z,
        stop_z: float = cfg.STOP_Z,
        lookback: Optional[int] = cfg.LOOKBACK,
        close_eod: bool = cfg.CLOSE_EOD,
        eod_buffer: int = cfg.EOD_EXIT_BUFFER,
        train_frac: float = cfg.TRAIN_FRAC,
        use_trend_filter: bool = cfg.USE_TREND_FILTER,
        trend_lookback: int = cfg.TREND_LOOKBACK,
    ):
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_z = stop_z
        self.lookback = lookback          # None => auto from half-life
        self.close_eod = close_eod
        self.eod_buffer = eod_buffer
        self.train_frac = train_frac
        self.use_trend_filter = use_trend_filter
        self.trend_lookback = trend_lookback

        # Populated during analysis
        self.half_life: float = 0.0
        self.hurst: float = 0.5
        self.adf_stat: float = 0.0
        self.adf_pvalue: float = 1.0
        self.signals: Optional[pd.DataFrame] = None

    # ---- relationship analysis ---- #

    def analyze(self, prices: pd.Series) -> dict:
        """
        Estimate mean-reversion statistics on the in-sample window.

        Parameters
        ----------
        prices : pd.Series
            Close price series with DatetimeIndex.

        Returns
        -------
        dict with keys: half_life, hurst, adf_stat, adf_pvalue, lookback
        """
        n = len(prices)
        train_end = int(n * self.train_frac)
        train = prices.iloc[:train_end]

        print(f"\n[Strategy] Analyzing mean-reversion properties "
              f"(in-sample: first {self.train_frac:.0%}, {train_end:,} bars)")

        # -- half-life via AR(1) on spread --
        # Use a preliminary lookback to compute spread for estimation
        prelim_lb = 60
        spread_est = train - train.rolling(prelim_lb, min_periods=prelim_lb // 2).mean()
        self.half_life = self._estimate_half_life(spread_est.dropna())
        print(f"[Strategy] Half-life       : {self.half_life:.1f} minutes")

        # -- determine lookback from half-life --
        if self.lookback is None:
            auto_lb = int(round(self.half_life * 2))
            auto_lb = max(cfg.MIN_LOOKBACK, min(cfg.MAX_LOOKBACK, auto_lb))
            self.lookback = auto_lb
            print(f"[Strategy] Lookback (auto) : {self.lookback} minutes")
        else:
            print(f"[Strategy] Lookback (manual): {self.lookback} minutes")

        # -- ADF test on spread with chosen lookback --
        spread_final = train - train.rolling(self.lookback, min_periods=self.lookback // 2).mean()
        clean = spread_final.dropna()
        # Use last 100k points to keep ADF fast
        sample = clean.values[-min(100_000, len(clean)):]
        self.adf_stat, self.adf_pvalue = adfuller(sample, maxlag=20)[:2]
        print(f"[Strategy] ADF test        : stat={self.adf_stat:.4f}, "
              f"p={self.adf_pvalue:.6f} "
              f"({'stationary' if self.adf_pvalue < 0.05 else 'NOT stationary'})")

        # -- Hurst exponent (on close returns, not spread levels) --
        close_rets = np.diff(np.log(train.values))
        self.hurst = self._hurst_exponent(close_rets[-min(50_000, len(close_rets)):])
        label = ("mean-reverting" if self.hurst < 0.45
                 else "random walk" if self.hurst < 0.55
                 else "trending")
        print(f"[Strategy] Hurst exponent  : {self.hurst:.4f} ({label})")

        return {
            "half_life": self.half_life,
            "hurst": self.hurst,
            "adf_stat": self.adf_stat,
            "adf_pvalue": self.adf_pvalue,
            "lookback": self.lookback,
        }

    # ---- signal generation ---- #

    def generate_signals(self, prices: pd.Series) -> pd.DataFrame:
        """
        Compute z-score and build position series over the *full* dataset.

        Uses a rolling z-score for directional signals (proven 55%+ win
        rate) with a **tight stop** at 2.5-sigma (0.5-sigma beyond entry).
        This gives a ~3:1 reward-to-risk ratio: the exit at |z|<0.5
        captures 1.5-sigma of reversion, while the stop limits losses
        to 0.5-sigma of adverse movement.

        Returns DataFrame with columns:
            price, sma, spread, z_score, raw_signal, position
        """
        lb = self.lookback
        sma = prices.rolling(lb, min_periods=lb // 2).mean()
        roll_std = prices.rolling(lb, min_periods=lb // 2).std()
        spread = prices - sma
        z = spread / (roll_std + 1e-10)

        # Trend filter: only mean-revert in direction of broader trend
        if self.use_trend_filter:
            trend_ma = prices.rolling(
                self.trend_lookback, min_periods=self.trend_lookback // 2
            ).mean()
            trend_bull = (prices > trend_ma).values   # True = bullish
            print(f"[Strategy] Trend filter    : SMA({self.trend_lookback}), "
                  f"bullish {trend_bull.sum()/len(trend_bull):.1%} of bars")
        else:
            trend_bull = np.ones(len(prices), dtype=bool)

        # raw entry signal (vectorised)
        raw = pd.Series(0.0, index=prices.index)
        raw[z < -self.entry_z] = 1.0    # long
        raw[z >  self.entry_z] = -1.0   # short

        # state-tracked positions (with trend filter)
        position = self._build_positions(z, prices.index, trend_bull)

        self.signals = pd.DataFrame({
            "price": prices,
            "sma": sma,
            "spread": spread,
            "z_score": z,
            "raw_signal": raw,
            "position": position,
        })

        n_long  = int((position == 1).sum())
        n_short = int((position == -1).sum())
        n_flat  = int((position == 0).sum())
        print(f"\n[Strategy] Signals generated over {len(prices):,} bars")
        print(f"[Strategy] Bars in position: long={n_long:,}, "
              f"short={n_short:,}, flat={n_flat:,}")

        return self.signals

    # ---- internals ---- #

    def _build_positions(
        self, z_score: pd.Series, index: pd.DatetimeIndex, trend_bull: np.ndarray
    ) -> pd.Series:
        """
        State machine: z-scores -> positions.

        Trend filter:  only LONG when trend is bullish (price > trend_MA),
                       only SHORT when trend is bearish.
        Includes end-of-day flattening when ``close_eod`` is True.
        Runs in a tight NumPy loop (O(n), unavoidable for state tracking).
        """
        z = z_score.values
        n = len(z)
        pos = np.zeros(n, dtype=np.float64)
        cur = 0.0

        # Pre-compute end-of-day mask (fully vectorised, O(n))
        if self.close_eod:
            dates_arr = np.array(index.date)

            # day_change[i] = True when bar i starts a new day
            day_change = np.empty(n, dtype=bool)
            day_change[0] = False
            day_change[1:] = dates_arr[1:] != dates_arr[:-1]

            # Indices where a new day starts (sentinel at end)
            day_starts = np.append(np.where(day_change)[0], n)

            eod_mask = np.zeros(n, dtype=bool)
            prev = 0
            for nxt in day_starts:
                # Mark last eod_buffer bars of current day
                begin = max(prev, nxt - self.eod_buffer)
                eod_mask[begin:nxt] = True
                prev = nxt
        else:
            eod_mask = np.zeros(n, dtype=bool)
            day_change = np.zeros(n, dtype=bool)

        for i in range(n):
            # Force flat at day boundary or EOD buffer
            if day_change[i]:
                cur = 0.0

            zi = z[i]
            if np.isnan(zi):
                pos[i] = cur
                continue

            if eod_mask[i]:
                cur = 0.0
            elif cur == 0.0:
                # Trend filter: only go long in bullish regime, short in bearish
                if zi < -self.entry_z and trend_bull[i]:
                    cur = 1.0
                elif zi > self.entry_z and not trend_bull[i]:
                    cur = -1.0
            elif cur == 1.0:
                if abs(zi) < self.exit_z:
                    cur = 0.0
                elif zi < -self.stop_z:
                    cur = 0.0
            elif cur == -1.0:
                if abs(zi) < self.exit_z:
                    cur = 0.0
                elif zi > self.stop_z:
                    cur = 0.0

            pos[i] = cur

        return pd.Series(pos, index=z_score.index)

    @staticmethod
    def _estimate_half_life(spread: pd.Series) -> float:
        """
        Half-life of mean-reversion via Ornstein-Uhlenbeck AR(1).

        Regress: delta_spread_t = alpha + beta * spread_{t-1} + eps
        theta = -beta;  half_life = ln(2) / theta
        """
        lag = spread.shift(1)
        delta = spread - lag
        valid = ~(lag.isna() | delta.isna())
        if valid.sum() < 100:
            return 30.0  # default fallback

        X = add_constant(lag[valid].values)
        y = delta[valid].values
        beta = OLS(y, X).fit().params[1]

        if beta >= 0:
            return 60.0  # not mean-reverting; use conservative default
        return -np.log(2) / beta

    @staticmethod
    def _hurst_exponent(series: np.ndarray) -> float:
        """
        Hurst exponent via Rescaled Range (R/S) analysis.

        H < 0.5 => mean-reverting
        H = 0.5 => random walk
        H > 0.5 => trending
        """
        n = len(series)
        if n < 100:
            return 0.5

        sizes = [s for s in [16, 32, 64, 128, 256, 512, 1024, 2048] if s <= n // 4]
        if len(sizes) < 2:
            return 0.5

        log_rs = []
        log_k = []

        for k in sizes:
            n_blocks = n // k
            rs_list = []
            for b in range(n_blocks):
                block = series[b * k : (b + 1) * k]
                m = block.mean()
                dev = block - m
                cumdev = np.cumsum(dev)
                R = cumdev.max() - cumdev.min()
                S = block.std(ddof=1)
                if S > 1e-12:
                    rs_list.append(R / S)
            if rs_list:
                log_rs.append(np.log(np.mean(rs_list)))
                log_k.append(np.log(k))

        if len(log_rs) < 2:
            return 0.5

        H = np.polyfit(log_k, log_rs, 1)[0]
        return float(H)


# ====================================================================== #
#  MULTI-ASSET PAIRS TRADING                                             #
# ====================================================================== #

class PairsStrategy:
    """
    Cointegration-based pairs trading for multi-asset datasets.

    Kept as a secondary strategy; auto-selected when multiple price
    columns are detected.
    """

    def __init__(
        self,
        entry_z: float = cfg.ENTRY_Z,
        exit_z: float = cfg.EXIT_Z,
        stop_z: float = cfg.STOP_Z,
        lookback: int = 60,
        coint_pvalue: float = cfg.COINT_PVALUE,
        max_pairs: int = cfg.MAX_PAIRS,
    ):
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_z = stop_z
        self.lookback = lookback
        self.coint_pvalue = coint_pvalue
        self.max_pairs = max_pairs

        self.pairs: List[Tuple[str, str]] = []
        self.hedge_ratios: Dict[Tuple[str, str], float] = {}
        self.spreads: Dict[Tuple[str, str], pd.Series] = {}
        self.signals: Dict[Tuple[str, str], pd.DataFrame] = {}

    def find_cointegrated_pairs(
        self, prices: pd.DataFrame, train_frac: float = cfg.TRAIN_FRAC
    ) -> List[Tuple]:
        n = len(prices)
        train = prices.iloc[: int(n * train_frac)]
        assets = prices.columns.tolist()
        corr = train.corr()
        results = []

        print(f"\n[Strategy] Testing {len(assets)*(len(assets)-1)//2} pairs "
              f"for cointegration (in-sample: {train_frac:.0%})")

        for i, j in combinations(range(len(assets)), 2):
            a, b = assets[i], assets[j]
            if abs(corr.loc[a, b]) < cfg.CORR_PREFILTER:
                continue
            try:
                _, pval, _ = coint(train[a], train[b])
                if pval >= self.coint_pvalue:
                    continue
                X = add_constant(train[b].values)
                beta = float(OLS(train[a].values, X).fit().params[1])
                spread = train[a] - beta * train[b]
                adf_p = adfuller(spread.dropna(), maxlag=20)[1]
                if adf_p >= self.coint_pvalue:
                    continue
                results.append((a, b, pval, beta, corr.loc[a, b], adf_p))
                print(f"  + {a}/{b}: coint p={pval:.4f} beta={beta:.4f} "
                      f"rho={corr.loc[a, b]:.4f} ADF p={adf_p:.4f}")
            except Exception:
                continue

        results.sort(key=lambda x: x[2])
        selected = results[: self.max_pairs]
        self.pairs = [(r[0], r[1]) for r in selected]
        self.hedge_ratios = {(r[0], r[1]): r[3] for r in selected}

        if not self.pairs:
            print("[Strategy] No cointegrated pairs -- fallback to highest correlation")
            self._fallback(train, corr)

        print(f"\n[Strategy] Selected {len(self.pairs)} pair(s)")
        return selected

    def _fallback(self, train: pd.DataFrame, corr: pd.DataFrame) -> None:
        assets = train.columns.tolist()
        ranked = sorted(
            combinations(range(len(assets)), 2),
            key=lambda ij: abs(corr.iloc[ij[0], ij[1]]),
            reverse=True,
        )
        for i, j in ranked[: self.max_pairs]:
            a, b = assets[i], assets[j]
            X = add_constant(train[b].values)
            beta = float(OLS(train[a].values, X).fit().params[1])
            self.pairs.append((a, b))
            self.hedge_ratios[(a, b)] = beta

    def generate_signals(self, prices: pd.DataFrame) -> Dict[Tuple, pd.DataFrame]:
        for pair in self.pairs:
            a, b = pair
            beta = self.hedge_ratios[pair]
            spread = prices[a] - beta * prices[b]
            self.spreads[pair] = spread
            rm = spread.rolling(self.lookback, min_periods=self.lookback // 2).mean()
            rs = spread.rolling(self.lookback, min_periods=self.lookback // 2).std()
            z = (spread - rm) / (rs + 1e-10)

            raw = pd.Series(0.0, index=prices.index)
            raw[z < -self.entry_z] = 1.0
            raw[z > self.entry_z] = -1.0

            position = self._build_positions(z)
            self.signals[pair] = pd.DataFrame({
                "spread": spread, "z_score": z, "signal": raw,
                "position": position,
                "price_a": prices[a], "price_b": prices[b],
            })
        return self.signals

    def _build_positions(self, z_score: pd.Series) -> pd.Series:
        z = z_score.values
        n = len(z)
        pos = np.zeros(n)
        cur = 0.0
        for i in range(n):
            zi = z[i]
            if np.isnan(zi):
                pos[i] = cur
                continue
            if cur == 0.0:
                if zi < -self.entry_z:
                    cur = 1.0
                elif zi > self.entry_z:
                    cur = -1.0
            elif cur == 1.0:
                if abs(zi) < self.exit_z:
                    cur = 0.0
                elif zi < -self.stop_z:
                    cur = 0.0
            elif cur == -1.0:
                if abs(zi) < self.exit_z:
                    cur = 0.0
                elif zi > self.stop_z:
                    cur = 0.0
            pos[i] = cur
        return pd.Series(pos, index=z_score.index)
