#!/usr/bin/env python3
"""临时回测度量：在指定窗口用强制重算评估盘后版胜率，不覆盖正式输出。"""
from __future__ import annotations

import argparse
import os
import time

import pandas as pd

os.environ["SHORT_BACKTEST_FORCE_RECOMPUTE"] = "1"
os.environ.setdefault("SHORT_KLINE_SOURCE", "tushare")
os.environ.setdefault("SHORT_KLINE_WORKERS", "1")
os.environ.setdefault("SHORT_QUOTE_ONLY_FALLBACK", "0")

from screen_common import fetch_trade_cal_dates, get_latest_trade_date  # noqa: E402
from short_screen import DEFAULT_KLINE_CANDIDATE_LIMIT  # noqa: E402
from strategy_backtest import run_portfolio_for_strategy  # noqa: E402

os.environ["SHORT_KLINE_CANDIDATE_LIMIT"] = str(DEFAULT_KLINE_CANDIDATE_LIMIT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", default=None)
    args = ap.parse_args()

    market_end = args.end_date or get_latest_trade_date()
    warmup = (pd.to_datetime(args.start_date, format="%Y%m%d") - pd.Timedelta(days=450)).strftime("%Y%m%d")
    all_dates = fetch_trade_cal_dates(warmup, market_end)
    signal_end = all_dates[-6]
    signal_dates = [d for d in all_dates if args.start_date <= d <= signal_end]
    print(f"signal_dates={len(signal_dates)} ({signal_dates[0]}..{signal_dates[-1]})")

    t0 = time.time()
    result = run_portfolio_for_strategy(
        "short", all_dates=all_dates, signal_dates=signal_dates,
        top_n=5, cash_per_stock=100_000.0, max_positions=3, retracement_pct=5.0,
    )
    dt = time.time() - t0
    trades = result.get("trades", pd.DataFrame())
    n = len(trades)
    if n:
        wins = int((trades["win"] == 1).sum())
        avg = pd.to_numeric(trades["ret_pct"], errors="coerce").mean()
        med = pd.to_numeric(trades["ret_pct"], errors="coerce").median()
        wr = wins / n * 100
        print(f"[EVAL] trades={n} winrate={wr:.2f}% avg={avg:+.2f}% median={med:+.2f}% elapsed={dt:.1f}s")
        print(trades["exit_reason"].value_counts().to_dict())
    else:
        print(f"[EVAL] no trades, elapsed={dt:.1f}s")


if __name__ == "__main__":
    main()
