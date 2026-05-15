#!/usr/bin/env python3
"""
T+3 历史回测盈亏分析
每支股票 10000 元额度，整百股买入，统计每笔和总盈亏。

用法：
  python3 scripts/pnl_analysis.py
  python3 scripts/pnl_analysis.py --budget 20000
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screen_common import DAILY_CACHE_DIR

HISTORY_CSV = Path(__file__).resolve().parent.parent / "docs" / "list" / "short_t3_history.csv"

LOT = 100   # A 股最小买入单位（手 = 100 股）


def _load_close_map(trade_date: str) -> dict[str, float]:
    """从本地 daily 缓存读取某交易日全市场收盘价。"""
    date_str = str(trade_date).replace("-", "")
    path = DAILY_CACHE_DIR / f"{date_str}.csv"
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype=str)
        df["code"] = df["ts_code"].str.split(".").str[0].str.zfill(6)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return dict(zip(df["code"], df["close"]))
    except Exception:
        return {}


def _parse_pick_returns(s: str) -> list[tuple[str, float]]:
    """解析 '002655(+7.01%) / 600867(-1.03%)' 格式，返回 [(code, ret_pct), ...]。"""
    if not isinstance(s, str):
        return []
    return [
        (m.group(1), float(m.group(2)))
        for m in re.finditer(r"(\d{6})\(([+-]?\d+\.?\d*)%\)", s)
    ]


def run(budget: float) -> None:
    if not HISTORY_CSV.exists():
        sys.exit(f"[error] 找不到 {HISTORY_CSV}，请先运行 postclose_t3_history.py")

    history = pd.read_csv(HISTORY_CSV)
    if history.empty:
        sys.exit("[error] 回测记录为空")

    rows = []
    for _, hrow in history.iterrows():
        signal_date = str(hrow["signal_date"])
        buy_date    = str(hrow["buy_date"])
        settle_date = str(hrow["settle_date"])
        picks = _parse_pick_returns(hrow.get("pick_returns", ""))
        if not picks:
            continue

        close_map = _load_close_map(signal_date)

        for code, ret_pct in picks:
            price = close_map.get(code)
            if not price or price <= 0:
                continue

            # 整百股买入（不足 1 手则跳过）
            shares = int(budget / price / LOT) * LOT
            if shares == 0:
                continue

            cost   = shares * price
            profit = cost * ret_pct / 100.0
            rows.append({
                "signal_date": signal_date,
                "buy_date":    buy_date,
                "settle_date": settle_date,
                "code":        code,
                "buy_price":   round(price, 2),
                "shares":      shares,
                "cost":        round(cost, 2),
                "ret_pct":     ret_pct,
                "profit":      round(profit, 2),
            })

    if not rows:
        sys.exit("[error] 无有效交易记录（可能缺少 daily 缓存）")

    df = pd.DataFrame(rows)

    # ── 汇总 ────────────────────────────────────────────────────────
    total_trades  = len(df)
    win_trades    = (df["profit"] > 0).sum()
    loss_trades   = (df["profit"] < 0).sum()
    total_profit  = df["profit"].sum()
    total_cost    = df["cost"].sum()        # 累计资金占用（非实际同时持仓）
    signal_days   = df["signal_date"].nunique()
    avg_per_day   = df.groupby("signal_date")["profit"].sum().mean()
    avg_per_trade = df["profit"].mean()

    print("=" * 58)
    print(f"  T+3 历史回测盈亏分析  （每股 {budget:,.0f} 元，整百股）")
    print("=" * 58)
    print(f"  信号交易日       : {signal_days} 天")
    print(f"  总交易笔数       : {total_trades} 笔（每日 Top5 各算一笔）")
    print(f"  胜/负           : {win_trades} 胜 / {loss_trades} 负  (胜率 {win_trades/total_trades*100:.1f}%)")
    print(f"  每笔平均盈亏     : {avg_per_trade:+,.2f} 元")
    print(f"  每信号日平均盈亏  : {avg_per_day:+,.2f} 元")
    print(f"  总盈亏           : {total_profit:+,.2f} 元")
    print(f"  累计资金占用      : {total_cost:,.0f} 元（非同时持仓）")
    print("=" * 58)

    # 按信号日汇总
    daily = (
        df.groupby("signal_date")
        .agg(笔数=("profit", "count"), 当日盈亏=("profit", "sum"))
        .reset_index()
    )
    daily["当日盈亏"] = daily["当日盈亏"].map(lambda x: f"{x:+,.2f}")
    daily.columns = ["信号日", "笔数", "当日盈亏(元)"]

    print("\n── 按信号日明细 ──")
    print(daily.to_string(index=False))

    print("\n── 最大单笔盈利 Top5 ──")
    top_win = df.nlargest(5, "profit")[["signal_date", "code", "buy_price", "shares", "ret_pct", "profit"]]
    top_win.columns = ["信号日", "代码", "买入价", "股数", "收益%", "盈利(元)"]
    print(top_win.to_string(index=False))

    print("\n── 最大单笔亏损 Top5 ──")
    top_loss = df.nsmallest(5, "profit")[["signal_date", "code", "buy_price", "shares", "ret_pct", "profit"]]
    top_loss.columns = ["信号日", "代码", "买入价", "股数", "收益%", "盈亏(元)"]
    print(top_loss.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="T+3 历史回测盈亏分析")
    parser.add_argument("--budget", type=float, default=10000, help="每支股票额度（元），默认 10000")
    args = parser.parse_args()
    run(args.budget)


if __name__ == "__main__":
    main()
