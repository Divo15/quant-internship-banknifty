"""
Entry point -- orchestrates the full trading pipeline.

Auto-detects the data format and routes to the appropriate strategy:
  * Single-asset OHLC  -->  MeanReversionStrategy
  * Multi-asset prices  -->  PairsStrategy

Usage:
    python main.py                            # default path from config
    python main.py path/to/custom_data.csv    # override
"""

import sys
import time

from data_loader import DataLoader
from strategy import MeanReversionStrategy, PairsStrategy
from backtester import Backtester
from analysis import PerformanceAnalyzer
import config as cfg


def main(data_path: str = cfg.DATA_PATH) -> dict:
    t0 = time.time()

    print("=" * 60)
    print("  SYSTEMATIC TRADING SYSTEM")
    print("=" * 60)

    # ================================================================= #
    #  1. Data Loading & Preprocessing                                   #
    # ================================================================= #
    loader = DataLoader(filepath=data_path, price_col=cfg.PRICE_COL)
    prices = loader.process()

    # ================================================================= #
    #  2 & 3. Strategy Selection + Signal Generation                     #
    # ================================================================= #
    if loader.is_multi_asset:
        # --- multi-asset: pairs trading ---
        mode = "pairs"
        strategy = PairsStrategy(
            entry_z=cfg.ENTRY_Z,
            exit_z=cfg.EXIT_Z,
            stop_z=cfg.STOP_Z,
            coint_pvalue=cfg.COINT_PVALUE,
            max_pairs=cfg.MAX_PAIRS,
        )
        strategy.find_cointegrated_pairs(prices)
        signals = strategy.generate_signals(prices)
    else:
        # --- single-asset: mean-reversion ---
        mode = "single"
        price_series = prices[cfg.PRICE_COL]

        strategy = MeanReversionStrategy(
            entry_z=cfg.ENTRY_Z,
            exit_z=cfg.EXIT_Z,
            stop_z=cfg.STOP_Z,
            lookback=cfg.LOOKBACK,
            close_eod=cfg.CLOSE_EOD,
            eod_buffer=cfg.EOD_EXIT_BUFFER,
            train_frac=cfg.TRAIN_FRAC,
            use_trend_filter=cfg.USE_TREND_FILTER,
            trend_lookback=cfg.TREND_LOOKBACK,
        )
        strategy.analyze(price_series)
        signals = strategy.generate_signals(price_series)

    # ================================================================= #
    #  4. Backtesting                                                    #
    # ================================================================= #
    bt = Backtester(
        initial_capital=cfg.INITIAL_CAPITAL,
        transaction_cost_bps=cfg.TRANSACTION_COST_BPS,
        slippage_bps=cfg.SLIPPAGE_BPS,
    )

    if mode == "single":
        equity_curve = bt.run_single(signals)
    else:
        equity_curve = bt.run_pairs(signals)

    trade_log = bt.get_trade_log()

    # ================================================================= #
    #  5. Performance Evaluation                                         #
    # ================================================================= #
    analyzer = PerformanceAnalyzer(
        equity_curve=equity_curve,
        trade_log=trade_log,
        signals=signals,
        prices=prices,
        results_dir=cfg.RESULTS_DIR,
        mode=mode,
        train_frac=cfg.TRAIN_FRAC,
    )
    metrics = analyzer.generate_full_report()

    elapsed = time.time() - t0
    print(f"\n[OK] Total execution time: {elapsed:.2f}s")
    if elapsed > 30:
        print("[!] WARNING: exceeded the 30-second runtime constraint!")

    return metrics


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else cfg.DATA_PATH
    main(path)
