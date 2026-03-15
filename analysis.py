"""
Performance evaluation and visualisation.

Metrics:  Total Return, Annualized Return, Sharpe, Sortino, Calmar,
          Maximum Drawdown, Win Rate, Avg Trade Duration, Profit Factor.

Plots:    Price series, strategy signals, equity curve, drawdown,
          trade P&L distribution.

All outputs are saved to ``results/``.
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from typing import Dict, Tuple, Optional

import config as cfg


class PerformanceAnalyzer:
    """Compute metrics and generate plots for single-asset or pairs mode."""

    def __init__(
        self,
        equity_curve: pd.DataFrame,
        trade_log: pd.DataFrame,
        signals,                        # pd.DataFrame (single) or dict (pairs)
        prices: pd.DataFrame,
        results_dir: str = cfg.RESULTS_DIR,
        mode: str = "single",
        train_frac: float = cfg.TRAIN_FRAC,
    ):
        self.equity_curve = equity_curve
        self.trade_log = trade_log
        self.signals = signals
        self.prices = prices
        self.results_dir = results_dir
        self.mode = mode
        self.train_frac = train_frac
        self.metrics: dict = {}
        os.makedirs(results_dir, exist_ok=True)

    # ================================================================= #
    #  Metrics                                                           #
    # ================================================================= #

    def calculate_metrics(self, mpy: int = cfg.MINUTES_PER_YEAR) -> dict:
        pv = self.equity_curve["portfolio_value"]
        rets = self.equity_curve["returns"]

        # ---- return ----
        total_ret = pv.iloc[-1] / pv.iloc[0] - 1.0
        n_years = max(len(pv) / mpy, 1e-6)
        if n_years >= 1.0 and total_ret > -1.0:
            ann_ret = (1.0 + total_ret) ** (1.0 / n_years) - 1.0
        else:
            ann_ret = total_ret / max(n_years, 1.0)  # simple linear for edge cases

        # ---- risk ----
        sharpe = (
            (rets.mean() / rets.std()) * np.sqrt(mpy) if rets.std() > 0 else 0.0
        )
        # Sortino (downside deviation)
        downside = rets[rets < 0]
        down_std = downside.std() if len(downside) > 0 else 1e-10
        sortino = (rets.mean() / down_std) * np.sqrt(mpy) if down_std > 0 else 0.0

        cum_max = pv.cummax()
        dd = (pv - cum_max) / cum_max
        max_dd = dd.min()

        # Calmar (annualised return / |max drawdown|)
        calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-10 else 0.0

        # ---- trades ----
        n_trades = len(self.trade_log)
        if n_trades > 0:
            wins = self.trade_log[self.trade_log["pnl"] > 0]
            losses = self.trade_log[self.trade_log["pnl"] <= 0]
            win_rate = len(wins) / n_trades
            avg_dur = self.trade_log["duration_minutes"].mean()
            avg_win = wins["pnl"].mean() if len(wins) else 0.0
            avg_loss = abs(losses["pnl"].mean()) if len(losses) else 0.0
            gross_loss = abs(losses["pnl"].sum()) if len(losses) else 0.0
            pf = wins["pnl"].sum() / gross_loss if gross_loss > 0 else float("inf")
            total_pnl = self.trade_log["pnl"].sum()
        else:
            win_rate = avg_dur = avg_win = avg_loss = pf = total_pnl = 0.0

        self.metrics = {
            "Total Return": f"{total_ret:.4%}",
            "Annualized Return": f"{ann_ret:.4%}",
            "Sharpe Ratio": f"{sharpe:.4f}",
            "Sortino Ratio": f"{sortino:.4f}",
            "Maximum Drawdown": f"{max_dd:.4%}",
            "Calmar Ratio": f"{calmar:.4f}",
            "Win Rate": f"{win_rate:.4%}",
            "Avg Trade Duration (min)": f"{avg_dur:.1f}",
            "Total Trades": n_trades,
            "Total P&L": f"${total_pnl:,.2f}",
            "Avg Win": f"${avg_win:,.2f}",
            "Avg Loss": f"${avg_loss:,.2f}",
            "Profit Factor": f"{pf:.2f}",
            "Final Portfolio Value": f"${pv.iloc[-1]:,.2f}",
        }

        print("\n" + "=" * 60)
        print("  PERFORMANCE METRICS")
        print("=" * 60)
        for k, v in self.metrics.items():
            print(f"  {k:.<40s} {v}")
        print("=" * 60)
        return self.metrics

    # ================================================================= #
    #  Plots                                                              #
    # ================================================================= #

    def plot_price_series(self) -> None:
        """Price series (subsampled for speed)."""
        fig, ax = plt.subplots(figsize=(14, 6))
        step = max(1, len(self.prices) // cfg.PLOT_SUBSAMPLE)
        sub = self.prices.iloc[::step]
        for col in sub.columns:
            ax.plot(sub.index, sub[col], label=col, alpha=0.8, lw=0.5)
        ax.set_title("Price Series", fontsize=14, fontweight="bold")
        ax.set_xlabel("Time")
        ax.set_ylabel("Price")
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        self._save("price_series.png")

    def plot_signals(self) -> None:
        if self.mode == "single":
            self._plot_signals_single()
        else:
            self._plot_signals_pairs()

    def _plot_signals_single(self) -> None:
        """Three-panel chart: price + SMA + entries/exits, spread, z-score."""
        sdf = self.signals
        fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

        # Subsample for plotting speed
        step = max(1, len(sdf) // cfg.PLOT_SUBSAMPLE)
        sub = sdf.iloc[::step]

        # Panel 1: price + SMA + entries + exits
        ax = axes[0]
        ax.plot(sub.index, sub["price"], lw=0.5, alpha=0.8, label="Close")
        ax.plot(sub.index, sub["sma"], lw=0.6, alpha=0.7, label="SMA", color="orange")

        pos_chg = sdf["position"].diff().fillna(0)
        long_e  = pos_chg[(pos_chg == 1)].index
        short_e = pos_chg[(pos_chg == -1)].index
        exits   = pos_chg[(pos_chg != 0) & (sdf["position"] == 0)].index

        ax.scatter(long_e,  sdf.loc[long_e, "price"],
                   marker="^", color="green", s=12, zorder=5, label="Long Entry")
        ax.scatter(short_e, sdf.loc[short_e, "price"],
                   marker="v", color="red", s=12, zorder=5, label="Short Entry")
        ax.scatter(exits,   sdf.loc[exits, "price"],
                   marker="x", color="gray", s=10, zorder=5, label="Exit", alpha=0.5)

        # in-sample / out-of-sample divider
        oos_start = sdf.index[int(len(sdf) * self.train_frac)]
        ax.axvline(oos_start, color="black", ls="--", lw=0.8, alpha=0.6, label="OOS start")

        ax.set_title("Price, SMA & Trade Signals", fontsize=13, fontweight="bold")
        ax.set_ylabel("Price")
        ax.legend(fontsize=7, loc="upper left", ncol=3)
        ax.grid(True, alpha=0.3)

        # Panel 2: spread
        ax = axes[1]
        ax.plot(sub.index, sub["spread"], color="navy", lw=0.5, alpha=0.8)
        ax.axhline(0, color="gray", ls="--", alpha=0.5)
        ax.axvline(oos_start, color="black", ls="--", lw=0.8, alpha=0.6)
        ax.set_title("Spread (Close - SMA)", fontsize=12)
        ax.set_ylabel("Spread")
        ax.grid(True, alpha=0.3)

        # Panel 3: z-score + thresholds
        ax = axes[2]
        ax.plot(sub.index, sub["z_score"], color="purple", lw=0.5, alpha=0.8, label="Z-Score")
        ax.axhline( cfg.ENTRY_Z, color="red",   ls="--", alpha=0.5, label=f"+/-{cfg.ENTRY_Z}")
        ax.axhline(-cfg.ENTRY_Z, color="red",   ls="--", alpha=0.5)
        ax.axhline( cfg.EXIT_Z,  color="green", ls="--", alpha=0.5, label=f"+/-{cfg.EXIT_Z}")
        ax.axhline(-cfg.EXIT_Z,  color="green", ls="--", alpha=0.5)
        ax.axhline(0, color="gray", alpha=0.3)
        ax.axvline(oos_start, color="black", ls="--", lw=0.8, alpha=0.6)

        self._shade_positions(ax, sub["position"])
        ax.set_title("Z-Score & Positions", fontsize=12)
        ax.set_xlabel("Time")
        ax.set_ylabel("Z-Score")
        ax.legend(fontsize=7, loc="upper left")
        ax.grid(True, alpha=0.3)
        ax.set_ylim(-6, 6)

        plt.tight_layout()
        self._save("signals.png")

    def _plot_signals_pairs(self) -> None:
        for pair, sdf in self.signals.items():
            fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
            step = max(1, len(sdf) // cfg.PLOT_SUBSAMPLE)
            sub = sdf.iloc[::step]

            ax = axes[0]
            na = 100 * sub["price_a"] / sub["price_a"].iloc[0]
            nb = 100 * sub["price_b"] / sub["price_b"].iloc[0]
            ax.plot(na, lw=0.5, label=pair[0])
            ax.plot(nb, lw=0.5, label=pair[1])
            ax.set_title(f"Pair: {pair[0]}/{pair[1]}", fontsize=13, fontweight="bold")
            ax.set_ylabel("Normalised Price")
            ax.legend(fontsize=8)
            ax.grid(True, alpha=0.3)

            ax = axes[1]
            ax.plot(sub["spread"], color="navy", lw=0.5)
            ax.axhline(sub["spread"].mean(), color="gray", ls="--", alpha=0.5)
            ax.set_ylabel("Spread")
            ax.grid(True, alpha=0.3)

            ax = axes[2]
            ax.plot(sub["z_score"], color="purple", lw=0.5)
            ax.axhline(cfg.ENTRY_Z, color="red", ls="--", alpha=0.5)
            ax.axhline(-cfg.ENTRY_Z, color="red", ls="--", alpha=0.5)
            ax.axhline(cfg.EXIT_Z, color="green", ls="--", alpha=0.5)
            ax.axhline(-cfg.EXIT_Z, color="green", ls="--", alpha=0.5)
            self._shade_positions(ax, sub["position"])
            ax.set_ylabel("Z-Score")
            ax.set_xlabel("Time")
            ax.set_ylim(-6, 6)
            ax.grid(True, alpha=0.3)

            plt.tight_layout()
            self._save(f"signals_{pair[0]}_{pair[1]}.png")

    def plot_equity_curve(self) -> None:
        fig, ax = plt.subplots(figsize=(14, 6))
        pv = self.equity_curve["portfolio_value"]
        init = pv.iloc[0]
        step = max(1, len(pv) // cfg.PLOT_SUBSAMPLE)
        pvs = pv.iloc[::step]

        ax.plot(pvs, color="darkblue", lw=0.8, label="Portfolio Value")
        ax.axhline(init, color="gray", ls="--", alpha=0.5, label="Initial Capital")
        ax.fill_between(pvs.index, init, pvs.values, where=(pvs.values >= init), alpha=0.08, color="green")
        ax.fill_between(pvs.index, init, pvs.values, where=(pvs.values <  init), alpha=0.08, color="red")

        # OOS marker
        oos_i = int(len(pv) * self.train_frac)
        if oos_i < len(pv):
            ax.axvline(pv.index[oos_i], color="black", ls="--", lw=0.8,
                       alpha=0.6, label="OOS start")

        final = pv.iloc[-1]
        ret = (final / init - 1) * 100
        ax.annotate(
            f"Final: ${final:,.0f} ({ret:+.2f}%)",
            xy=(pv.index[-1], final), fontsize=10, fontweight="bold",
            xytext=(-180, 30), textcoords="offset points",
            arrowprops=dict(arrowstyle="->", color="black"),
            bbox=dict(boxstyle="round,pad=0.3", fc="lightyellow", alpha=0.8),
        )
        ax.set_title("Portfolio Equity Curve", fontsize=14, fontweight="bold")
        ax.set_xlabel("Time")
        ax.set_ylabel("Portfolio Value ($)")
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        self._save("equity_curve.png")

    def plot_drawdown(self) -> None:
        fig, ax = plt.subplots(figsize=(14, 4))
        pv = self.equity_curve["portfolio_value"]
        dd = (pv - pv.cummax()) / pv.cummax() * 100
        step = max(1, len(dd) // cfg.PLOT_SUBSAMPLE)
        dds = dd.iloc[::step]

        ax.fill_between(dds.index, 0, dds, color="red", alpha=0.3)
        ax.plot(dds, color="darkred", lw=0.5)

        md = dd.min()
        mdt = dd.idxmin()
        ax.annotate(
            f"Max DD: {md:.2f}%", xy=(mdt, md),
            fontsize=10, fontweight="bold",
            xytext=(50, -30), textcoords="offset points",
            arrowprops=dict(arrowstyle="->", color="black"),
            bbox=dict(boxstyle="round,pad=0.3", fc="lightyellow", alpha=0.8),
        )
        ax.set_title("Portfolio Drawdown", fontsize=14, fontweight="bold")
        ax.set_xlabel("Time")
        ax.set_ylabel("Drawdown (%)")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        self._save("drawdown.png")

    def plot_trade_analysis(self) -> None:
        if len(self.trade_log) == 0:
            print("[Analysis] No trades to plot.")
            return
        fig, axes = plt.subplots(1, 3, figsize=(16, 5))
        pnls = self.trade_log["pnl"]
        durs = self.trade_log["duration_minutes"]

        ax = axes[0]
        ax.hist(pnls, bins=min(50, len(pnls)), color="steelblue", edgecolor="black", alpha=0.7)
        ax.axvline(0, color="black", lw=1)
        ax.axvline(pnls.mean(), color="orange", ls="--", label=f"Mean: ${pnls.mean():,.2f}")
        ax.set_title("Trade P&L Distribution", fontsize=12, fontweight="bold")
        ax.set_xlabel("P&L ($)")
        ax.set_ylabel("Frequency")
        ax.legend(fontsize=8)

        ax = axes[1]
        ax.plot(range(len(pnls)), pnls.cumsum().values, color="darkblue", lw=0.8)
        ax.axhline(0, color="gray", ls="--", alpha=0.5)
        ax.set_title("Cumulative Trade P&L", fontsize=12, fontweight="bold")
        ax.set_xlabel("Trade #")
        ax.set_ylabel("Cumulative P&L ($)")
        ax.grid(True, alpha=0.3)

        ax = axes[2]
        ax.hist(durs, bins=min(50, len(durs)), color="teal", edgecolor="black", alpha=0.7)
        ax.axvline(durs.mean(), color="orange", ls="--", label=f"Mean: {durs.mean():.0f} min")
        ax.set_title("Trade Duration", fontsize=12, fontweight="bold")
        ax.set_xlabel("Duration (min)")
        ax.set_ylabel("Frequency")
        ax.legend(fontsize=8)

        plt.tight_layout()
        self._save("trade_analysis.png")

    # ================================================================= #
    #  Full report                                                        #
    # ================================================================= #

    def generate_full_report(self) -> dict:
        print("\n" + "=" * 60)
        print("  GENERATING PERFORMANCE REPORT")
        print("=" * 60)

        self.calculate_metrics()
        self.plot_price_series()
        self.plot_signals()
        self.plot_equity_curve()
        self.plot_drawdown()
        self.plot_trade_analysis()

        pd.DataFrame(
            list(self.metrics.items()), columns=["Metric", "Value"]
        ).to_csv(os.path.join(self.results_dir, "metrics.csv"), index=False)
        print("[Analysis] Saved metrics.csv")

        if len(self.trade_log) > 0:
            self.trade_log.to_csv(
                os.path.join(self.results_dir, "trade_log.csv"), index=False
            )
            print("[Analysis] Saved trade_log.csv")

        print(f"\n[Analysis] All results saved to '{self.results_dir}/'")
        return self.metrics

    # ================================================================= #
    #  Helpers                                                            #
    # ================================================================= #

    @staticmethod
    def _shade_positions(ax, positions: pd.Series, max_spans: int = 500) -> None:
        pos = positions.values
        idx = positions.index
        changes = np.where(np.diff(pos) != 0)[0] + 1
        bounds = np.concatenate(([0], changes, [len(pos)]))
        n_spans = len(bounds) - 1
        if n_spans > max_spans:
            return  # too many spans would freeze the plot
        for k in range(n_spans):
            s, e = bounds[k], bounds[k + 1] - 1
            v = pos[s]
            if v == 1:
                ax.axvspan(idx[s], idx[e], alpha=0.04, color="green")
            elif v == -1:
                ax.axvspan(idx[s], idx[e], alpha=0.04, color="red")

    def _save(self, fname: str) -> None:
        plt.savefig(os.path.join(self.results_dir, fname), dpi=150, bbox_inches="tight")
        plt.close()
        print(f"[Analysis] Saved {fname}")
