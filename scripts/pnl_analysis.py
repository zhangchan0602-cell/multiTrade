#!/usr/bin/env python3
"""
T+5 历史回测盈亏分析。

读取 `short_t5_history_trades.csv`，基于实际成交金额与卖出结果汇总每笔和整体盈亏。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HISTORY_CSV = Path(__file__).resolve().parent.parent / "docs" / "list" / "short_t5_history_trades.csv"


def run(_: float) -> None:
    if not HISTORY_CSV.exists():
        sys.exit(f"[error] 找不到 {HISTORY_CSV}，请先运行 postclose_t3_history.py")

    history = pd.read_csv(HISTORY_CSV)
    if history.empty:
        sys.exit("[error] 回测记录为空")

    df = history.copy()
    for col in ("buy_amount", "sell_amount", "ret_pct", "shares", "entry_price"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["buy_amount", "sell_amount", "ret_pct"])
    if df.empty:
        sys.exit("[error] 无有效交易记录")
    df["profit"] = df["sell_amount"] - df["buy_amount"]

    # ── 汇总 ────────────────────────────────────────────────────────
    total_trades  = len(df)
    win_trades    = (df["profit"] > 0).sum()
    loss_trades   = (df["profit"] < 0).sum()
    total_profit  = df["profit"].sum()
    total_cost    = df["buy_amount"].sum()        # 累计资金占用（非实际同时持仓）
    signal_days   = df["entry_date"].nunique()
    avg_per_day   = df.groupby("entry_date")["profit"].sum().mean()
    avg_per_trade = df["profit"].mean()

    print("=" * 58)
    print("  T+5 历史回测盈亏分析  （基于实际成交金额）")
    print("=" * 58)
    print(f"  信号交易日       : {signal_days} 天")
    print(f"  总交易笔数       : {total_trades} 笔")
    print(f"  胜/负           : {win_trades} 胜 / {loss_trades} 负  (胜率 {win_trades/total_trades*100:.1f}%)")
    print(f"  每笔平均盈亏     : {avg_per_trade:+,.2f} 元")
    print(f"  每信号日平均盈亏  : {avg_per_day:+,.2f} 元")
    print(f"  总盈亏           : {total_profit:+,.2f} 元")
    print(f"  累计资金占用      : {total_cost:,.0f} 元（非同时持仓）")
    print("=" * 58)

    # 按信号日汇总
    daily = (
        df.groupby("entry_date")
        .agg(笔数=("profit", "count"), 当日盈亏=("profit", "sum"))
        .reset_index()
    )
    daily["当日盈亏"] = daily["当日盈亏"].map(lambda x: f"{x:+,.2f}")
    daily.columns = ["信号日", "笔数", "当日盈亏(元)"]

    print("\n── 按信号日明细 ──")
    print(daily.to_string(index=False))

    print("\n── 最大单笔盈利 Top5 ──")
    top_win = df.nlargest(5, "profit")[["entry_date", "code", "entry_price", "shares", "ret_pct", "profit"]]
    top_win.columns = ["信号日", "代码", "买入价", "股数", "收益%", "盈利(元)"]
    print(top_win.to_string(index=False))

    print("\n── 最大单笔亏损 Top5 ──")
    top_loss = df.nsmallest(5, "profit")[["entry_date", "code", "entry_price", "shares", "ret_pct", "profit"]]
    top_loss.columns = ["信号日", "代码", "买入价", "股数", "收益%", "盈亏(元)"]
    print(top_loss.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="T+5 历史回测盈亏分析")
    parser.add_argument("--budget", type=float, default=0, help="保留参数，当前分析直接使用交易明细中的实际成交金额")
    args = parser.parse_args()
    run(args.budget)


if __name__ == "__main__":
    main()
