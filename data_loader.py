"""
Data loading and preprocessing pipeline.

Supports two dataset layouts that the assignment might provide:

  A. **Multi-asset wide format** – datetime index + one column per asset
     (each column is a price series).
  B. **Single-instrument OHLC** – Instrument / Date / Time / O / H / L / C
     (candlestick data for one ticker).

The loader auto-detects the format, cleans the data, and returns a
``pd.DataFrame`` of price(s) ready for downstream strategy modules.
"""

import pandas as pd
import numpy as np
from typing import Optional, Tuple

import config as cfg


class DataLoader:
    """Load, validate, clean, and align minute-level price data."""

    def __init__(
        self,
        filepath: str = cfg.DATA_PATH,
        datetime_col: Optional[str] = cfg.DATETIME_COL,
        price_col: str = cfg.PRICE_COL,
    ):
        self.filepath = filepath
        self.datetime_col = datetime_col
        self.price_col = price_col

        self.raw_data: Optional[pd.DataFrame] = None
        self.clean_data: Optional[pd.DataFrame] = None
        self.is_ohlc: bool = False
        self.is_multi_asset: bool = False
        self.report: dict = {}

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def process(self) -> pd.DataFrame:
        """Run the full preprocessing pipeline; return clean prices."""
        print("=" * 60)
        print("  DATA PREPROCESSING PIPELINE")
        print("=" * 60)

        self._load()
        self._detect_format()
        self._build_datetime_index()
        self._align_time_series()

        if self.is_ohlc:
            self._validate_ohlc()

        self._handle_missing()
        self._detect_outliers()

        # Final safety net
        self.clean_data = self.raw_data.apply(pd.to_numeric, errors="coerce")
        self.clean_data = self.clean_data.dropna(how="all").ffill().bfill()

        n_rows, n_cols = self.clean_data.shape
        print(f"\n[DataLoader] Final shape : {n_rows:,} rows x {n_cols} columns")
        if self.is_multi_asset:
            print(f"[DataLoader] Mode       : multi-asset ({list(self.clean_data.columns)})")
        else:
            print(f"[DataLoader] Mode       : single-asset OHLC")
        print("=" * 60)
        return self.clean_data

    def get_report(self) -> dict:
        return self.report

    # ------------------------------------------------------------------ #
    #  Internal steps                                                     #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        fp = self.filepath
        if fp.endswith(".csv"):
            self.raw_data = pd.read_csv(fp)
        elif fp.endswith((".xlsx", ".xls")):
            self.raw_data = pd.read_excel(fp)
        elif fp.endswith(".parquet"):
            self.raw_data = pd.read_parquet(fp)
        else:
            raise ValueError(f"Unsupported file format: {fp}")

        self.report["raw_shape"] = self.raw_data.shape
        print(f"[DataLoader] Loaded {self.raw_data.shape[0]:,} rows, "
              f"{self.raw_data.shape[1]} columns")

    # ----- format detection ----- #

    def _detect_format(self) -> None:
        """Decide whether the file is OHLC candlestick or wide multi-asset."""
        cols_lower = {c.lower(): c for c in self.raw_data.columns}
        has_ohlc = all(k in cols_lower for k in ("open", "high", "low", "close"))
        has_instrument = "instrument" in cols_lower

        if has_ohlc:
            self.is_ohlc = True
            if has_instrument:
                n_inst = self.raw_data[cols_lower["instrument"]].nunique()
                if n_inst > 1:
                    self.is_multi_asset = True
                    print(f"[DataLoader] Detected: multi-instrument OHLC ({n_inst} tickers)")
                else:
                    inst = self.raw_data[cols_lower["instrument"]].iloc[0]
                    print(f"[DataLoader] Detected: single-instrument OHLC ({inst})")
            else:
                print("[DataLoader] Detected: OHLC without Instrument column")
        else:
            self.is_multi_asset = True
            print("[DataLoader] Detected: wide multi-asset format")

    # ----- datetime index ----- #

    def _build_datetime_index(self) -> None:
        """Parse / combine date & time columns into a DatetimeIndex."""
        df = self.raw_data
        cols_lower = {c.lower(): c for c in df.columns}

        # Case 1: separate Date + Time columns (e.g. the Banknifty dataset)
        if "date" in cols_lower and "time" in cols_lower:
            date_col = cols_lower["date"]
            time_col = cols_lower["time"]
            # Zero-pad time for consistent format (e.g. "9:15:00" -> "09:15:00")
            time_str = df[time_col].astype(str).str.zfill(8)
            df["datetime"] = pd.to_datetime(
                df[date_col].astype(str) + " " + time_str,
                format="%d-%m-%Y %H:%M:%S",
            )
            # Drop helper columns that are no longer needed
            drop = [date_col, time_col]
            if "instrument" in cols_lower:
                drop.append(cols_lower["instrument"])
            df = df.drop(columns=drop, errors="ignore")
            df = df.set_index("datetime").sort_index()
            self.raw_data = df
            print(f"[DataLoader] Parsed Date+Time -> DatetimeIndex")
            print(f"[DataLoader] Range: {df.index[0]}  to  {df.index[-1]}")
            return

        # Case 2: user-specified or auto-detected datetime column
        if self.datetime_col and self.datetime_col in df.columns:
            df[self.datetime_col] = pd.to_datetime(df[self.datetime_col])
            df = df.set_index(self.datetime_col).sort_index()
            self.raw_data = df
            return

        # Case 3: common column names
        for name in ("datetime", "Datetime", "timestamp", "Timestamp", "DATE"):
            if name in df.columns:
                df[name] = pd.to_datetime(df[name])
                df = df.set_index(name).sort_index()
                self.raw_data = df
                return

        # Case 4: first-column heuristic
        first = df.columns[0]
        try:
            df[first] = pd.to_datetime(df[first])
            df = df.set_index(first).sort_index()
            self.raw_data = df
            return
        except (ValueError, TypeError):
            pass

        # Case 5: existing index
        try:
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            self.raw_data = df
        except (ValueError, TypeError):
            raise ValueError(
                "Cannot auto-detect datetime column. Set DATETIME_COL in config.py."
            )

    # ----- time alignment ----- #

    def _align_time_series(self) -> None:
        df = self.raw_data
        n_dupes = int(df.index.duplicated().sum())
        if n_dupes:
            print(f"[DataLoader] Removing {n_dupes:,} duplicate timestamps")
            df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()

        diffs = pd.Series(df.index).diff().dropna()
        med = diffs.median()
        print(f"[DataLoader] Median interval : {med}")
        self.report["n_duplicates"] = n_dupes
        self.report["median_interval"] = str(med)
        self.raw_data = df

    # ----- OHLC validation ----- #

    def _validate_ohlc(self) -> None:
        """Fix rows where High < Low or prices outside H-L range."""
        df = self.raw_data
        cols = {c.lower(): c for c in df.columns}
        O, H, L, C = cols["open"], cols["high"], cols["low"], cols["close"]

        bad = df[H] < df[L]
        n_bad = int(bad.sum())
        if n_bad:
            # Swap High and Low where violated
            df.loc[bad, [H, L]] = df.loc[bad, [L, H]].values
            print(f"[DataLoader] Fixed {n_bad:,} OHLC violations (H < L)")

        self.report["ohlc_violations"] = n_bad
        self.raw_data = df

    # ----- missing values ----- #

    def _handle_missing(self) -> None:
        df = self.raw_data
        before = int(df.isnull().sum().sum())
        if before == 0:
            print(f"[DataLoader] Missing values : 0 (clean)")
            self.report["missing_before"] = 0
            self.report["missing_after"] = 0
            return

        max_gap = cfg.MAX_FFILL_GAP
        df = df.ffill(limit=max_gap)
        df = df.interpolate(method="linear", limit=max_gap, limit_direction="both")
        df = df.bfill(limit=max_gap)
        df = df.dropna(how="all")

        pct = df.isnull().sum() / len(df)
        bad = pct[pct > cfg.DROP_MISSING_PCT].index.tolist()
        if bad:
            print(f"[DataLoader] Dropping columns (>{cfg.DROP_MISSING_PCT:.0%} missing): {bad}")
            df = df.drop(columns=bad)
        df = df.ffill().bfill()

        after = int(df.isnull().sum().sum())
        print(f"[DataLoader] Missing values : {before:,} -> {after:,}")
        self.report["missing_before"] = before
        self.report["missing_after"] = after
        self.raw_data = df

    # ----- outlier detection ----- #

    def _detect_outliers(self) -> None:
        """MAD-based outlier detection on log-returns; replace with rolling median."""
        df = self.raw_data.copy()
        method = cfg.OUTLIER_METHOD
        threshold = cfg.OUTLIER_THRESHOLD
        total = 0

        # Only run on numeric price columns
        price_cols = [c for c in df.columns if df[c].dtype in ("float64", "float32", "int64")]

        for col in price_cols:
            prices = df[col].copy()
            log_ret = np.log(prices / prices.shift(1))

            if method == "mad":
                med = np.nanmedian(log_ret.values)
                mad = np.nanmedian(np.abs(log_ret.values - med))
                mz = 0.6745 * (log_ret - med) / (mad + 1e-10)
                mask = np.abs(mz) > threshold
            elif method == "iqr":
                q1, q3 = log_ret.quantile(0.25), log_ret.quantile(0.75)
                iqr = q3 - q1
                mask = (log_ret < q1 - threshold * iqr) | (log_ret > q3 + threshold * iqr)
            else:
                raise ValueError(f"Unknown outlier method: {method}")

            n_out = int(mask.sum())
            total += n_out
            if n_out:
                roll_med = prices.rolling(
                    window=cfg.OUTLIER_REPLACE_WINDOW, center=True, min_periods=1
                ).median()
                df.loc[mask, col] = roll_med.loc[mask]
                print(f"[DataLoader] {col}: {n_out:,} outliers replaced")

        if total == 0:
            print(f"[DataLoader] Outliers       : 0")
        self.report["total_outliers"] = total
        self.raw_data = df
