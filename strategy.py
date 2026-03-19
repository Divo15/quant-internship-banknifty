"""
Trading strategy module.

``MeanReversionStrategy``  (single-asset)
    Adaptive z-score strategy with **walk-forward recalibration**.

    Key design principles (to avoid overfitting):
      * Parameters are derived from statistical theory, not swept.
      * Mode (momentum vs mean-reversion) is auto-selected from Hurst.
      * Lookback comes from 2 × half-life (Ornstein-Uhlenbeck theory).
      * Exit levels use ROLLING volatility (last 60 trading days) so they
        adapt to current market conditions using only past data.
      * Asymmetric TP/SL: momentum lets winners run (1.5:1 R:R);
        mean-reversion takes quick profits and gives room for convergence.

``PairsStrategy``  (multi-asset)
    Cointegration-based pairs trading (Engle-Granger).
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
#  SINGLE-ASSET STRATEGY                                                  #
# ====================================================================== #

class MeanReversionStrategy:
    """
    Intraday z-score strategy with parameters derived from theory.

    All parameters come from standard quant finance theory:
      - Entry Z = 2.0  (standard significance threshold)
      - Lookback = 2 * half-life  (Ornstein-Uhlenbeck process theory)
      - Exit levels = EXIT_MULT * sqrt(HL) * rolling_sigma
        (adapts to current volatility using 60-day rolling window)
      - Asymmetric TP/SL: momentum uses 1.5:1 R:R (let winners run)
      - Mode = auto-selected from Hurst exponent (> 0.5 -> momentum)
      - Trend filter = SMA(200)  (universal standard)
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
        strategy_mode: str = cfg.STRATEGY_MODE,
        exit_mult: float = cfg.EXIT_MULT,
        vol_lookback_days: int = cfg.VOL_LOOKBACK_DAYS,
        tp_ratio: float = cfg.TP_RATIO,
        sl_ratio: float = cfg.SL_RATIO,
    ):
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_z = stop_z
        self.lookback = lookback
        self.close_eod = close_eod
        self.eod_buffer = eod_buffer
        self.train_frac = train_frac
        self.use_trend_filter = use_trend_filter
        self.trend_lookback = trend_lookback
        self.exit_mult = exit_mult
        self.strategy_mode_cfg = strategy_mode
        self.vol_lookback_days = vol_lookback_days
        self.tp_ratio = tp_ratio
        self.sl_ratio = sl_ratio

        # Populated during analyze()
        self.half_life: float = 0.0
        self.hurst: float = 0.5
        self.sigma_minute: float = 0.0
        self.adf_stat: float = 0.0
        self.adf_pvalue: float = 1.0
        self.momentum: bool = False
        self.signals: Optional[pd.DataFrame] = None

    # ---- relationship analysis (initial calibration) ---- #

    def analyze(self, prices: pd.Series) -> dict:
        n = len(prices)
        train_end = int(n * self.train_frac)
        train = prices.iloc[:train_end]

        print(f"\n[Strategy] Initial calibration on first "
              f"{self.train_frac:.0%} ({train_end:,} bars)")

        # 1. Half-life
        prelim_lb = 60
        spread_est = train - train.rolling(prelim_lb, min_periods=30).mean()
        self.half_life = self._estimate_half_life(spread_est.dropna())
        print(f"[Strategy] Half-life       : {self.half_life:.1f} minutes")

        # 2. Lookback = 2 × HL
        if self.lookback is None:
            auto_lb = int(round(self.half_life * 2))
            auto_lb = max(cfg.MIN_LOOKBACK, min(cfg.MAX_LOOKBACK, auto_lb))
            self.lookback = auto_lb
            print(f"[Strategy] Lookback (auto) : {self.lookback} (2 x HL)")

        # 3. ADF test
        spread_final = train - train.rolling(self.lookback, min_periods=self.lookback // 2).mean()
        clean = spread_final.dropna()
        sample = clean.values[-min(50_000, len(clean)):]
        self.adf_stat, self.adf_pvalue = adfuller(sample, maxlag=15)[:2]
        print(f"[Strategy] ADF test        : stat={self.adf_stat:.4f}, "
              f"p={self.adf_pvalue:.6f}")

        # 4. Hurst
        close_rets = np.diff(np.log(train.values))
        self.hurst = self._hurst_exponent(close_rets[-min(20_000, len(close_rets)):])
        print(f"[Strategy] Hurst exponent  : {self.hurst:.4f} "
              f"({'trending' if self.hurst > 0.55 else 'random walk' if self.hurst > 0.45 else 'mean-reverting'})")

        if self.strategy_mode_cfg == "auto":
            self.momentum = (self.hurst > 0.5)
        else:
            self.momentum = (self.strategy_mode_cfg == "momentum")

        print(f"[Strategy] Initial mode    : "
              f"{'MOMENTUM' if self.momentum else 'MEAN-REVERSION'}")

        # 5. Sigma (IS reference only; actual exits use rolling sigma)
        self.sigma_minute = float(np.std(close_rets))
        base_exit = self.exit_mult * np.sqrt(self.half_life) * self.sigma_minute
        print(f"[Strategy] sigma_minute    : {self.sigma_minute*100:.4f}%")
        print(f"[Strategy] Base exit (IS)  : {base_exit*100:.4f}%  "
              f"({self.exit_mult} x sqrt({self.half_life:.0f}) x sigma)")
        print(f"[Strategy] Walk-forward    : rolling {self.vol_lookback_days}-day vol "
              f"(adapts to current regime)")
        if self.momentum:
            print(f"[Strategy] Asymmetric R:R  : TP={self.tp_ratio:.1f}x, "
                  f"SL={self.sl_ratio:.1f}x (let winners run)")
        else:
            print(f"[Strategy] Asymmetric R:R  : TP={self.sl_ratio:.1f}x, "
                  f"SL={self.tp_ratio:.1f}x (take quick profits)")

        return {
            "half_life": self.half_life, "hurst": self.hurst,
            "adf_stat": self.adf_stat, "adf_pvalue": self.adf_pvalue,
            "lookback": self.lookback,
            "mode": "MOMENTUM" if self.momentum else "MEAN-REVERSION",
            "sigma_minute": self.sigma_minute,
            "target_pct": base_exit,
        }

    # ---- signal generation ---- #

    def generate_signals(self, prices: pd.Series) -> pd.DataFrame:
        lb = self.lookback
        sma = prices.rolling(lb, min_periods=lb // 2).mean()
        roll_std = prices.rolling(lb, min_periods=lb // 2).std()
        spread = prices - sma
        z = spread / (roll_std + 1e-10)

        if self.use_trend_filter:
            trend_ma = prices.rolling(
                self.trend_lookback, min_periods=self.trend_lookback // 2
            ).mean()
            trend_bull = (prices > trend_ma).values
            print(f"[Strategy] Trend filter    : SMA({self.trend_lookback}), "
                  f"bullish {trend_bull.sum()/len(trend_bull):.1%}")
        else:
            trend_bull = np.ones(len(prices), dtype=bool)

        position = self._build_positions(
            z, prices.values, prices.index, trend_bull
        )

        # Raw signal (for visualization only)
        raw = pd.Series(0.0, index=prices.index)

        self.signals = pd.DataFrame({
            "price": prices, "sma": sma, "spread": spread,
            "z_score": z, "raw_signal": raw, "position": position,
        })

        n_long  = int((position == 1).sum())
        n_short = int((position == -1).sum())
        print(f"\n[Strategy] Signals over {len(prices):,} bars  |  "
              f"long={n_long:,}  short={n_short:,}  "
              f"flat={len(prices)-n_long-n_short:,}")
        return self.signals

    # ---- position builder with walk-forward ---- #

    def _build_positions(
        self, z_score: pd.Series, prices: np.ndarray,
        index: pd.DatetimeIndex, trend_bull: np.ndarray,
    ) -> pd.Series:
        """
        State machine: z-score entries + dynamic %-exits.

        Exit levels adapt to current volatility via rolling sigma
        (last VOL_LOOKBACK_DAYS trading days). Asymmetric TP/SL:
          momentum  -> TP=1.5x, SL=1.0x (let winners run)
          mean-rev  -> TP=1.0x, SL=1.5x (take quick profits)
        """
        z = z_score.values
        n = len(z)
        pos = np.zeros(n, dtype=np.float64)
        cur = 0.0
        entry_price = 0.0
        _entry_z = self.entry_z
        _use_trend = self.use_trend_filter
        exit_mult = self.exit_mult
        sqrt_hl = np.sqrt(self.half_life)
        is_momentum = self.momentum
        fallback_sigma = self.sigma_minute

        # ---- Precompute rolling sigma (fast numpy version) ----
        log_rets = np.empty(n, dtype=np.float64)
        log_rets[0] = 0.0
        log_rets[1:] = np.diff(np.log(prices))
        vol_window = max(1, self.vol_lookback_days * cfg.MINUTES_PER_DAY)
        
        # Fast rolling std using pandas (optimized)
        rolling_sigma = pd.Series(log_rets).rolling(
            window=vol_window, min_periods=max(100, vol_window // 10)
        ).std().values
        # Fill NaNs with fallback (early bars before window fills)
        rolling_sigma = np.where(
            np.isnan(rolling_sigma) | (rolling_sigma < 1e-10),
            fallback_sigma,
            rolling_sigma,
        )

        # ---- Precompute dynamic exit arrays (vectorised) ----
        base_exit = exit_mult * sqrt_hl * rolling_sigma  # shape (n,)
        if is_momentum:
            tp_exit = base_exit * self.tp_ratio   # wider TP (let winners run)
            sl_exit = base_exit * self.sl_ratio   # standard SL
        else:
            tp_exit = base_exit * self.sl_ratio   # tight TP (take quick profits)
            sl_exit = base_exit * self.tp_ratio   # wide SL (room for convergence)

        # ---- EOD mask (vectorised) ----
        if self.close_eod:
            day_int = index.normalize().asi8
            day_change = np.empty(n, dtype=bool)
            day_change[0] = False
            day_change[1:] = day_int[1:] != day_int[:-1]

            day_starts = np.append(np.where(day_change)[0], n)
            eod_mask = np.zeros(n, dtype=bool)
            prev_ds = 0
            for nxt in day_starts:
                begin = max(prev_ds, nxt - self.eod_buffer)
                eod_mask[begin:nxt] = True
                prev_ds = nxt
        else:
            eod_mask = np.zeros(n, dtype=bool)
            day_change = np.zeros(n, dtype=bool)

        # ---- Main loop ----
        for i in range(n):
            # Force flat at day boundary
            if day_change[i]:
                cur = 0.0

            zi = z[i]
            if np.isnan(zi):
                pos[i] = cur
                continue

            if eod_mask[i]:
                cur = 0.0
            elif cur == 0.0:
                # --- Entry ---
                if is_momentum:
                    long_sig = zi > _entry_z
                    short_sig = zi < -_entry_z
                else:
                    long_sig = zi < -_entry_z
                    short_sig = zi > _entry_z

                # Trend filter: restrict direction when enabled
                long_ok = long_sig and (trend_bull[i] or not _use_trend)
                short_ok = short_sig and (not trend_bull[i] or not _use_trend)

                if long_ok:
                    cur = 1.0
                    entry_price = prices[i]
                elif short_ok:
                    cur = -1.0
                    entry_price = prices[i]

            else:
                # --- Exit: dynamic-% from rolling sigma + asymmetric R:R ---
                tp_i = tp_exit[i]
                sl_i = sl_exit[i]
                if cur == 1.0:
                    if prices[i] >= entry_price * (1.0 + tp_i):
                        cur = 0.0  # take profit
                    elif prices[i] <= entry_price * (1.0 - sl_i):
                        cur = 0.0  # stop loss
                elif cur == -1.0:
                    if prices[i] <= entry_price * (1.0 - tp_i):
                        cur = 0.0  # take profit
                    elif prices[i] >= entry_price * (1.0 + sl_i):
                        cur = 0.0  # stop loss

            pos[i] = cur

        return pd.Series(pos, index=z_score.index)

    # ---- static helpers ---- #

    @staticmethod
    def _estimate_half_life(spread: pd.Series) -> float:
        lag = spread.shift(1)
        delta = spread - lag
        valid = ~(lag.isna() | delta.isna())
        if valid.sum() < 100:
            return 30.0

        X = add_constant(lag[valid].values)
        y = delta[valid].values
        beta = OLS(y, X).fit().params[1]

        if beta >= 0:
            return 60.0
        return -np.log(2) / beta

    @staticmethod
    def _hurst_exponent(series: np.ndarray) -> float:
        n = len(series)
        if n < 100:
            return 0.5

        sizes = [s for s in [16, 32, 64, 128, 256, 512, 1024, 2048] if s <= n // 4]
        if len(sizes) < 2:
            return 0.5

        log_rs, log_k = [], []
        for k in sizes:
            n_blocks = n // k
            rs_list = []
            for b in range(n_blocks):
                block = series[b * k : (b + 1) * k]
                m = block.mean()
                cumdev = np.cumsum(block - m)
                R = cumdev.max() - cumdev.min()
                S = block.std(ddof=1)
                if S > 1e-12:
                    rs_list.append(R / S)
            if rs_list:
                log_rs.append(np.log(np.mean(rs_list)))
                log_k.append(np.log(k))

        if len(log_rs) < 2:
            return 0.5
        return float(np.polyfit(log_k, log_rs, 1)[0])


# ====================================================================== #
#  MULTI-ASSET PAIRS TRADING                                             #
# ====================================================================== #

class PairsStrategy:
    """Cointegration-based pairs trading for multi-asset datasets."""

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
            except Exception:
                continue

        results.sort(key=lambda x: x[2])
        selected = results[: self.max_pairs]
        self.pairs = [(r[0], r[1]) for r in selected]
        self.hedge_ratios = {(r[0], r[1]): r[3] for r in selected}

        if not self.pairs:
            self._fallback(train, corr)
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
