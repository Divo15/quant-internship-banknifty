"""
Backtesting engine.

Supports two modes:

  ``singlee``  – long / short a single asset (mean-reversion strategy)
  ``pairs``   – long / short a spread across two assets

Both modes track:
  * open positions & mark-to-market P&L
  * realised trade P&L with detailed trade log
  * transaction costs  (0.01 % = 1 bp per trade, as specified)
  * proportional slippage (0.005 % = 0.5 bp)
"""

import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional

import config as cfg


class Backtester:
    """Backtest engine for single-asset or pairs strategies."""

    def __init__(
        self,
        initial_capital: float = cfg.INITIAL_CAPITAL,
        transaction_cost_bps: float = cfg.TRANSACTION_COST_BPS,
        slippage_bps: float = cfg.SLIPPAGE_BPS,
    ):
        self.initial_capital = initial_capital
        self.tc = transaction_cost_bps / 1e4
        self.slip = slippage_bps / 1e4

        self.trade_log: list = []
        self.equity_curve: pd.DataFrame = pd.DataFrame()

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def run_single(self, signals: pd.DataFrame) -> pd.DataFrame:
        """
        Backtest a single-asset long/short strategy.

        Parameters
        ----------
        signals : pd.DataFrame
            Must have columns: ``price``, ``position``, ``z_score``.

        Returns
        -------
        pd.DataFrame  – equity curve.
        """
        print(f"\n[Backtester] Mode          : single-asset")
        print(f"[Backtester] Capital       : ${self.initial_capital:,.0f}")
        print(f"[Backtester] Costs         : {self.tc*1e4:.1f} bps  |  "
              f"Slippage: {self.slip*1e4:.1f} bps")
        print(f"[Backtester] Bars          : {len(signals):,}")

        pnl = self._backtest_single_asset(signals)

        cum_pnl = pnl.cumsum()
        port_value = self.initial_capital + cum_pnl

        self.equity_curve = pd.DataFrame({
            "portfolio_value": port_value,
            "returns": port_value.pct_change().fillna(0.0),
            "cumulative_pnl": cum_pnl,
        }, index=signals.index)

        final = port_value.iloc[-1]
        total = cum_pnl.iloc[-1]
        print(f"\n[Backtester] Final value   : ${final:,.2f}")
        print(f"[Backtester] Total P&L     : ${total:,.2f}")
        print(f"[Backtester] Trades        : {len(self.trade_log):,}")

        return self.equity_curve

    def run_pairs(
        self, signals: Dict[Tuple[str, str], pd.DataFrame]
    ) -> pd.DataFrame:
        """Backtest pairs-trading strategy (multiple pairs)."""
        if not signals:
            raise ValueError("No signals provided.")

        first_key = next(iter(signals))
        index = signals[first_key].index
        n_pairs = len(signals)
        cap_per = self.initial_capital / max(n_pairs, 1)

        print(f"\n[Backtester] Mode          : pairs ({n_pairs} pair(s))")
        print(f"[Backtester] Capital       : ${self.initial_capital:,.0f}  "
              f"(${cap_per:,.0f}/pair)")
        print(f"[Backtester] Costs         : {self.tc*1e4:.1f} bps  |  "
              f"Slippage: {self.slip*1e4:.1f} bps")

        pair_pnls = {}
        for pair, sdf in signals.items():
            pair_pnls[pair] = self._backtest_pair(sdf, pair, cap_per)

        pnl_df = pd.DataFrame(pair_pnls).fillna(0.0)
        cum = pnl_df.cumsum()
        pv = self.initial_capital + cum.sum(axis=1)

        self.equity_curve = pd.DataFrame({
            "portfolio_value": pv,
            "returns": pv.pct_change().fillna(0.0),
            "cumulative_pnl": cum.sum(axis=1),
        }, index=index)

        for pair in pair_pnls:
            self.equity_curve[f"pnl_{pair[0]}_{pair[1]}"] = pnl_df[pair].cumsum()

        print(f"\n[Backtester] Final value   : ${pv.iloc[-1]:,.2f}")
        print(f"[Backtester] Total P&L     : ${cum.sum(axis=1).iloc[-1]:,.2f}")
        print(f"[Backtester] Trades        : {len(self.trade_log):,}")
        return self.equity_curve

    def get_trade_log(self) -> pd.DataFrame:
        return pd.DataFrame(self.trade_log) if self.trade_log else pd.DataFrame()

    def get_equity_curve(self) -> pd.DataFrame:
        return self.equity_curve

    # ------------------------------------------------------------------ #
    #  Single-asset backtest                                              #
    # ------------------------------------------------------------------ #

    def _backtest_single_asset(self, sig: pd.DataFrame) -> pd.Series:
        """
        Walk through positions and compute bar-by-bar P&L.

        Position sizing: invest ``initial_capital`` when entering.
        """
        positions = sig["position"].values
        price = sig["price"].values
        zs = sig["z_score"].values
        idx = sig.index
        n = len(positions)

        pnl = np.zeros(n, dtype=np.float64)

        in_pos = False
        direction = 0
        entry_price = 0.0
        entry_i = 0
        entry_cost = 0.0
        n_units = 0.0

        for i in range(1, n):
            prev = positions[i - 1]
            curr = positions[i]

            if curr != prev:
                # ---- close ----
                if in_pos:
                    exit_price = price[i] * (1.0 - self.slip * direction)
                    exit_cost = abs(n_units) * exit_price * self.tc
                    # INCREMENTAL P&L for this bar (last MTM bar → exit)
                    pnl[i] += n_units * (exit_price - price[i - 1]) - exit_cost
                    # TOTAL trade P&L for the log (entry → exit, all costs)
                    total_trade = (n_units * (exit_price - entry_price)
                                   - entry_cost - exit_cost)

                    self.trade_log.append({
                        "pair": "single",
                        "direction": "Long" if direction == 1 else "Short",
                        "entry_time": idx[entry_i],
                        "exit_time": idx[i],
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl": total_trade,
                        "duration_minutes": i - entry_i,
                        "entry_z": zs[entry_i] if not np.isnan(zs[entry_i]) else 0.0,
                        "exit_z": zs[i] if not np.isnan(zs[i]) else 0.0,
                    })
                    in_pos = False
                    direction = 0

                # ---- open (fixed sizing to initial capital) ----
                if curr != 0.0:
                    direction = int(curr)
                    entry_price = price[i] * (1.0 + self.slip * direction)
                    entry_i = i
                    n_units = direction * self.initial_capital / entry_price
                    entry_cost = abs(n_units) * entry_price * self.tc
                    # Deduct entry cost + within-bar slippage
                    pnl[i] -= entry_cost
                    pnl[i] += n_units * (price[i] - entry_price)
                    in_pos = True

            elif in_pos:
                # mark to market
                pnl[i] = n_units * (price[i] - price[i - 1])

        # Force-close any open position at end of data
        if in_pos:
            exit_price = price[-1] * (1.0 - self.slip * direction)
            exit_cost = abs(n_units) * exit_price * self.tc
            pnl[-1] += n_units * (exit_price - price[-2]) - exit_cost
            total_trade = (n_units * (exit_price - entry_price)
                           - entry_cost - exit_cost)
            self.trade_log.append({
                "pair": "single",
                "direction": "Long" if direction == 1 else "Short",
                "entry_time": idx[entry_i],
                "exit_time": idx[-1],
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": total_trade,
                "duration_minutes": n - 1 - entry_i,
                "entry_z": zs[entry_i] if not np.isnan(zs[entry_i]) else 0.0,
                "exit_z": zs[-1] if not np.isnan(zs[-1]) else 0.0,
            })

        return pd.Series(pnl, index=idx)

    # ------------------------------------------------------------------ #
    #  Pairs backtest                                                     #
    # ------------------------------------------------------------------ #

    def _backtest_pair(
        self, sig: pd.DataFrame, pair: Tuple[str, str], capital: float
    ) -> pd.Series:
        positions = sig["position"].values
        pa = sig["price_a"].values
        pb = sig["price_b"].values
        zs = sig["z_score"].values
        idx = sig.index
        n = len(positions)

        pnl = np.zeros(n, dtype=np.float64)
        in_pos = False
        direction = 0
        entry_pa = entry_pb = 0.0
        entry_i = 0
        ua = ub = 0.0
        notional = capital / 2.0
        p_entry_cost = 0.0

        for i in range(1, n):
            prev, curr = positions[i - 1], positions[i]

            if curr != prev:
                if in_pos:
                    epa = pa[i] * (1.0 - self.slip * np.sign(ua))
                    epb = pb[i] * (1.0 - self.slip * np.sign(ub))
                    exit_cost = (abs(ua) * epa + abs(ub) * epb) * self.tc
                    # Incremental P&L (last bar → exit)
                    pnl[i] += (ua * (epa - pa[i - 1])
                               + ub * (epb - pb[i - 1]) - exit_cost)
                    # Total for log
                    tpnl = (ua * (epa - entry_pa) + ub * (epb - entry_pb)
                            - p_entry_cost - exit_cost)
                    self.trade_log.append({
                        "pair": f"{pair[0]}/{pair[1]}",
                        "direction": "Long Spread" if direction == 1 else "Short Spread",
                        "entry_time": idx[entry_i], "exit_time": idx[i],
                        "entry_price": entry_pa, "exit_price": epa,
                        "pnl": tpnl,
                        "duration_minutes": i - entry_i,
                        "entry_z": zs[entry_i] if not np.isnan(zs[entry_i]) else 0.0,
                        "exit_z": zs[i] if not np.isnan(zs[i]) else 0.0,
                    })
                    in_pos = False
                    direction = 0

                if curr != 0.0:
                    direction = int(curr)
                    entry_pa = pa[i] * (1.0 + self.slip * direction)
                    entry_pb = pb[i] * (1.0 - self.slip * direction)
                    entry_i = i
                    ua = direction * notional / entry_pa
                    ub = -direction * notional / entry_pb
                    p_entry_cost = (abs(ua) * entry_pa + abs(ub) * entry_pb) * self.tc
                    pnl[i] -= p_entry_cost
                    # Within-bar slippage
                    pnl[i] += ua * (pa[i] - entry_pa) + ub * (pb[i] - entry_pb)
                    in_pos = True

            elif in_pos:
                pnl[i] = ua * (pa[i] - pa[i - 1]) + ub * (pb[i] - pb[i - 1])

        # Force-close at end
        if in_pos:
            epa = pa[-1] * (1.0 - self.slip * np.sign(ua))
            epb = pb[-1] * (1.0 - self.slip * np.sign(ub))
            exit_cost = (abs(ua) * epa + abs(ub) * epb) * self.tc
            pnl[-1] += (ua * (epa - pa[-2]) + ub * (epb - pb[-2]) - exit_cost)
            tpnl = (ua * (epa - entry_pa) + ub * (epb - entry_pb)
                    - p_entry_cost - exit_cost)
            self.trade_log.append({
                "pair": f"{pair[0]}/{pair[1]}",
                "direction": "Long Spread" if direction == 1 else "Short Spread",
                "entry_time": idx[entry_i], "exit_time": idx[-1],
                "entry_price": entry_pa, "exit_price": epa,
                "pnl": tpnl,
                "duration_minutes": n - 1 - entry_i,
                "entry_z": zs[entry_i] if not np.isnan(zs[entry_i]) else 0.0,
                "exit_z": zs[-1] if not np.isnan(zs[-1]) else 0.0,
            })

        return pd.Series(pnl, index=idx)
