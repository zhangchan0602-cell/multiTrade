#!/usr/bin/env python3
"""Download the STAR 50 index daily bars used by the Kechuang page."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_common import call_tushare_api, get_latest_trade_date, get_tushare_pro


DEFAULT_INDEX_CODE = "000688.SH"
DEFAULT_START_DATE = "20190722"
INDEX_CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "index"


def download_index(index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    pro = get_tushare_pro()
    df = call_tushare_api(
        pro.index_daily,
        ts_code=index_code,
        start_date=start_date,
        end_date=end_date,
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "vol", "amount"])

    keep_cols = [col for col in ["trade_date", "open", "high", "low", "close", "vol", "amount"] if col in df.columns]
    out = df[keep_cols].copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], format="%Y%m%d", errors="coerce")
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = (
        out.dropna(subset=["trade_date", "open", "high", "low", "close"])
        .sort_values("trade_date")
        .drop_duplicates(subset=["trade_date"], keep="last")
        .reset_index(drop=True)
    )
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Kechuang index daily bars")
    parser.add_argument("--index-code", default=DEFAULT_INDEX_CODE, help="Tushare index code, default 000688.SH")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE, help="YYYYMMDD")
    parser.add_argument("--end-date", default=None, help="YYYYMMDD, defaults to latest trade date")
    args = parser.parse_args()

    end_date = args.end_date or get_latest_trade_date()
    df = download_index(args.index_code, args.start_date, end_date)
    if df.empty:
        raise SystemExit(f"no index data returned for {args.index_code} {args.start_date}..{end_date}")

    INDEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INDEX_CACHE_DIR / f"{args.index_code.split('.')[0]}.csv"
    df.to_csv(out_path, index=False)
    print(f"[kechuang-index] {args.index_code} {df['trade_date'].iloc[0]} -> {df['trade_date'].iloc[-1]} rows={len(df)}")
    print(f"[kechuang-index] saved {out_path}")


if __name__ == "__main__":
    main()
