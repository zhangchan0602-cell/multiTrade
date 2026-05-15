#!/usr/bin/env python3
"""
RPS双90 策略 T+N 历史回测

对历史交易日重新计算 RPS双90 Top5，
逐日检查止损/止盈，计算真实收益率、胜率、平均收益。

用法：
    python3 scripts/rps90_backtest.py [--hold N] [--lookback D] [--interval K]
                                      [--stop-loss P] [--take-profit P]
      --hold        N  持有最大交易日数（默认 3）
      --lookback    D  回测交易日回溯窗口（默认 90，约 4 个月）
      --interval    K  取样间隔交易日数（默认 5，即每周）
      --stop-loss   P  止损阈值百分比（默认 8，即 -8% 出局）
      --take-profit P  止盈阈值百分比（默认 20，即 +20% 出局）
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_common import (
    OUTPUT_DIR,
    code_to_ts_code,
    fetch_daily_snapshot,
    fetch_stock_basic,
    fetch_trade_cal_dates,
    get_latest_trade_date,
    ts_code_to_code,
)

# ── 策略参数（与 rps90_screen.py 保持一致）──────────────────────────────────
RPS20_MIN   = 90.0
RPS90_MIN   = 90.0
PRICE_MIN   = 3.0
AMOUNT_MIN  = 30_000_000   # 元；daily.amount 单位千元，使用时需 ×1000
CAL_DAYS_MIN = 90
TOP_N       = 5
# ────────────────────────────────────────────────────────────────────────────

# 磁盘缓存：fetch_daily_snapshot 内置 csv 缓存，无需重复拉取
_snapshot_cache: Dict[str, pd.DataFrame] = {}


def _get_snapshot(trade_date: str) -> pd.DataFrame:
    """带内存二级缓存的快照（磁盘缓存已由 fetch_daily_snapshot 处理）。"""
    if trade_date not in _snapshot_cache:
        df = fetch_daily_snapshot(trade_date)
        if df is None or df.empty:
            _snapshot_cache[trade_date] = pd.DataFrame(columns=["ts_code", "close", "amount"])
        else:
            df = df[["ts_code", "close", "amount"]].copy()
            df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            _snapshot_cache[trade_date] = df
    return _snapshot_cache[trade_date]


def _nth_day_back(dates: List[str], n: int) -> Optional[str]:
    """从列表末尾倒数第 n 个日期；不足返回 None。"""
    idx = -(n + 1)
    return dates[idx] if abs(idx) <= len(dates) else None


def _compute_top5(
    signal_date: str,
    window: List[str],
    basic_inv: pd.DataFrame,
) -> pd.DataFrame:
    """
    对 signal_date 计算 RPS双90 Top5。
    window：包含 signal_date 作为最后元素、长度 >= 91 的交易日列表。
    返回含 rank/code/name/industry/rps20/rps90/entry_close 的 DataFrame。
    """
    date_20d = _nth_day_back(window, 20)
    date_90d = _nth_day_back(window, 90)
    if not date_20d or not date_90d:
        return pd.DataFrame()

    snap_t   = _get_snapshot(signal_date)
    snap_20d = _get_snapshot(date_20d)
    snap_90d = _get_snapshot(date_90d)

    if snap_t.empty or snap_20d.empty or snap_90d.empty:
        return pd.DataFrame()

    # 三日价格合并（inner → 三日均有收盘价）
    prices = (
        snap_t.rename(columns={"close": "close_t", "amount": "amount_t"})
        .merge(snap_20d[["ts_code","close"]].rename(columns={"close":"close_20d"}), on="ts_code", how="inner")
        .merge(snap_90d[["ts_code","close"]].rename(columns={"close":"close_90d"}), on="ts_code", how="inner")
    )
    prices = prices.dropna(subset=["close_t","close_20d","close_90d"])
    prices = prices[
        (prices["close_t"] > 0) & (prices["close_20d"] > 0) & (prices["close_90d"] > 0)
    ].copy()

    # 排除科创板
    prices["code6"] = prices["ts_code"].map(ts_code_to_code)
    prices = prices[~prices["code6"].str.startswith("688")].copy()

    if prices.empty:
        return pd.DataFrame()

    # RPS 全市场百分位排名（不含科创板）
    prices["ret_20d"] = prices["close_t"] / prices["close_20d"] - 1.0
    prices["ret_90d"] = prices["close_t"] / prices["close_90d"] - 1.0
    prices["rps20"] = prices["ret_20d"].rank(pct=True, ascending=True, na_option="bottom") * 100.0
    prices["rps90"] = prices["ret_90d"].rank(pct=True, ascending=True, na_option="bottom") * 100.0

    # 拼接基本面（name / industry / listing_date）
    df = prices.merge(
        basic_inv[["secucode","name","industry","listing_date"]],
        left_on="ts_code", right_on="secucode", how="inner"
    )

    # 硬过滤
    df["is_st"] = df["name"].str.contains("ST", na=False)
    df["listed_days"] = (
        pd.Timestamp(signal_date) - pd.to_datetime(df["listing_date"], errors="coerce")
    ).dt.days.fillna(0)
    df["amount_yuan"] = df["amount_t"] * 1_000.0   # 千元 → 元

    df = df[
        (~df["is_st"]) &
        (df["listed_days"] >= CAL_DAYS_MIN) &
        (df["close_t"] >= PRICE_MIN) &
        (df["amount_yuan"] >= AMOUNT_MIN)
    ].copy()

    # 双90过滤
    df = df[(df["rps20"] >= RPS20_MIN) & (df["rps90"] >= RPS90_MIN)].copy()
    if df.empty:
        return pd.DataFrame()

    # 打分（与 rps90_screen.py 一致）
    df["rps_score"] = 0.40 * df["rps20"] + 0.60 * df["rps90"]
    amount_rank = df["amount_yuan"].rank(pct=True, ascending=True, na_option="bottom")
    df["composite_score"] = df["rps_score"] + amount_rank * 5.0
    df = df.sort_values("composite_score", ascending=False).head(TOP_N).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)

    return df[["rank","ts_code","code6","name","industry","rps20","rps90","composite_score","close_t"]].rename(
        columns={"code6": "code", "close_t": "entry_close"}
    )


def run_backtest(hold_days: int = 3, lookback_days: int = 90, interval: int = 5,
                 stop_loss_pct: float = 8.0, take_profit_pct: float = 20.0) -> None:
    run_ts = datetime.now()
    print(f"[RPS双90 回测] {run_ts.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"参数：持有={hold_days}交易日  回溯={lookback_days}交易日  取样间隔={interval}交易日")
    print(f"      止损={stop_loss_pct:.0f}%  止盈={take_profit_pct:.0f}%\n")

    stop_loss_ratio   = 1.0 - stop_loss_pct   / 100.0   # e.g. 0.92
    take_profit_ratio = 1.0 + take_profit_pct / 100.0   # e.g. 1.20

    # ── 交易日历 ──────────────────────────────────────────────────────────────
    today_td = get_latest_trade_date()
    cal_start = (pd.Timestamp(today_td) - pd.Timedelta(days=450)).strftime("%Y%m%d")
    all_dates = fetch_trade_cal_dates(cal_start, today_td)
    print(f"交易日历: {all_dates[0]} ~ {all_dates[-1]}  共 {len(all_dates)} 个交易日")

    if len(all_dates) < 95 + hold_days:
        print("交易日历样本不足，退出")
        return

    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    # ── 确定回测信号日列表 ────────────────────────────────────────────────────
    # 最晚信号日：保证 T+hold 有数据（all_dates[-(hold_days+1)] = 倒数第 hold+1 个）
    last_sig_idx  = len(all_dates) - 1 - hold_days
    # 最早信号日：至少 95 个历史交易日（保证 T-90 计算可行）
    first_sig_idx = max(95, last_sig_idx - lookback_days)

    test_indices = range(first_sig_idx, last_sig_idx + 1, interval)
    test_dates   = [all_dates[i] for i in test_indices]
    print(f"回测信号日: {test_dates[0]} ~ {test_dates[-1]}  共 {len(test_dates)} 个\n")

    # ── 预加载 stock_basic ────────────────────────────────────────────────────
    basic = fetch_stock_basic()
    valid_markets = {"主板", "创业板"}
    basic_inv = basic[basic["market"].isin(valid_markets)][
        ["secucode", "name", "industry", "listing_date"]
    ].copy()

    # ── 逐日回测 ──────────────────────────────────────────────────────────────
    records = []
    for td in test_dates:
        td_idx = date_to_idx[td]
        # 取 signal_date 对应的 120 个交易日窗口（T-120 ~ T），足够覆盖 T-90
        window_start = max(0, td_idx - 120)
        window = all_dates[window_start: td_idx + 1]   # 末尾元素 = td

        exit_idx  = td_idx + hold_days
        exit_date = all_dates[exit_idx]

        print(f"  {td} → T+{hold_days}={exit_date}", end="  ")
        top5 = _compute_top5(td, window, basic_inv)

        if top5.empty:
            print("无通过标的")
            continue

        # 预取 T+1 ~ T+hold 每日收盘（用于逐日止损/止盈检查）
        daily_closes: List[Dict] = []
        for k in range(1, hold_days + 1):
            dk_idx = td_idx + k
            if dk_idx < len(all_dates):
                snap_k = _get_snapshot(all_dates[dk_idx])
                daily_closes.append(
                    snap_k.set_index("ts_code")["close"].to_dict()
                    if not snap_k.empty else {}
                )
            else:
                daily_closes.append({})

        date_rets = []
        for _, row in top5.iterrows():
            ts_code  = row["ts_code"]
            entry_px = float(row["entry_close"])
            if entry_px <= 0:
                continue

            # 逐日检查止损 / 止盈
            exit_px     = None
            exit_reason = "normal"
            exit_day    = hold_days
            for k, close_map in enumerate(daily_closes, start=1):
                px = close_map.get(ts_code)
                if px is None or pd.isna(px):
                    continue
                px = float(px)
                if px <= entry_px * stop_loss_ratio:
                    exit_px, exit_reason, exit_day = px, "stop_loss", k
                    break
                if px >= entry_px * take_profit_ratio:
                    exit_px, exit_reason, exit_day = px, "take_profit", k
                    break
                exit_px = px  # 未触发则记录最新收盘（最终 = T+hold 收盘）

            ret = (exit_px / entry_px - 1.0) if (exit_px and not pd.isna(exit_px)) else np.nan
            actual_exit_date = (
                all_dates[td_idx + exit_day]
                if (td_idx + exit_day) < len(all_dates) else exit_date
            )

            records.append({
                "signal_date" : td,
                "exit_date"   : actual_exit_date,
                "exit_day"    : exit_day,
                "exit_reason" : exit_reason,
                "rank"        : int(row["rank"]),
                "code"        : row["code"],
                "name"        : row["name"],
                "industry"    : row["industry"],
                "rps20"       : round(float(row["rps20"]), 1),
                "rps90"       : round(float(row["rps90"]), 1),
                "entry_close" : round(entry_px, 2),
                "exit_close"  : round(float(exit_px), 2) if exit_px and not pd.isna(exit_px) else None,
                "ret_pct"     : round(ret * 100, 2) if not np.isnan(ret) else None,
                "win"         : (1 if ret > 0 else 0) if not np.isnan(ret) else None,
            })
            if not np.isnan(ret):
                date_rets.append(ret * 100)

        if date_rets:
            print(f"{len(top5)} 只  均收益={np.mean(date_rets):+.1f}%  胜率={np.mean([r>0 for r in date_rets]):.0%}")
        else:
            print(f"{len(top5)} 只  T+{hold_days} 数据不足")

    if not records:
        print("\n无有效回测记录")
        return

    # ── 汇总统计 ──────────────────────────────────────────────────────────────
    df = pd.DataFrame(records)
    valid = df.dropna(subset=["ret_pct"])

    n_signals  = df["signal_date"].nunique()
    n_valid    = len(valid)
    win_rate   = valid["win"].mean()
    avg_ret    = valid["ret_pct"].mean()
    median_ret = valid["ret_pct"].median()
    std_ret    = valid["ret_pct"].std()

    print(f"\n{'='*60}")
    print(f"RPS双90 T+{hold_days} 回测汇总（止损={stop_loss_pct:.0f}% 止盈={take_profit_pct:.0f}%）")
    print(f"{'='*60}")
    print(f"信号日期数    : {n_signals}")
    print(f"有效信号总数  : {n_valid}（含全部 Top{TOP_N}）")
    print(f"胜率(收益>0)  : {win_rate:.1%}")
    print(f"平均收益      : {avg_ret:+.2f}%")
    print(f"中位数收益    : {median_ret:+.2f}%")
    print(f"收益标准差    : {std_ret:.2f}%")
    print(f"最大单笔收益  : {valid['ret_pct'].max():+.2f}%")
    print(f"最大单笔亏损  : {valid['ret_pct'].min():+.2f}%")

    # 出局原因分布
    if "exit_reason" in valid.columns:
        reason_stats = (
            valid.groupby("exit_reason")["ret_pct"]
            .agg(n="count", mean="mean", win=lambda x: (x > 0).mean())
        )
        print(f"\n出局原因分布:")
        reason_labels = {"stop_loss": f"止损(-{stop_loss_pct:.0f}%)",
                         "take_profit": f"止盈(+{take_profit_pct:.0f}%)",
                         "normal": f"正常T+{hold_days}"}
        for reason, row in reason_stats.iterrows():
            label = reason_labels.get(reason, reason)
            print(f"  {label:<14}: n={int(row['n']):>3}  胜率={row['win']:>5.1%}  均收益={row['mean']:>+7.2f}%")

    # 按排名分组
    print(f"\n按排名分组（均为 T+{hold_days} 收益）:")
    print(f"  {'Rank':>4}  {'n':>4}  {'胜率':>6}  {'均收益':>8}  {'中位数':>8}")
    for rk in range(1, TOP_N + 1):
        sub = valid[valid["rank"] == rk]
        if len(sub) > 0:
            print(
                f"  {rk:>4}  {len(sub):>4}  "
                f"{sub['win'].mean():>6.1%}  "
                f"{sub['ret_pct'].mean():>+8.2f}%  "
                f"{sub['ret_pct'].median():>+8.2f}%"
            )

    # 按行业分组
    ind_stats = (
        valid.groupby("industry")["ret_pct"]
        .agg(n="count", mean="mean", win=lambda x: (x > 0).mean())
        .sort_values("mean", ascending=False)
        .head(10)
    )
    if not ind_stats.empty:
        print(f"\n行业分布 Top10（按均收益）:")
        print(f"  {'行业':<12}  {'n':>4}  {'胜率':>6}  {'均收益':>8}")
        for ind, row in ind_stats.iterrows():
            print(f"  {ind:<12}  {int(row['n']):>4}  {row['win']:>6.1%}  {row['mean']:>+8.2f}%")

    # 最佳 / 最差 5 笔
    print(f"\n最佳 5 笔:")
    cols = ["signal_date","code","name","rps20","rps90","ret_pct"]
    print(valid.nlargest(5, "ret_pct")[cols].to_string(index=False))
    print(f"\n最差 5 笔:")
    print(valid.nsmallest(5, "ret_pct")[cols].to_string(index=False))

    # ── 保存 CSV ──────────────────────────────────────────────────────────────
    out_path = OUTPUT_DIR / "rps90_backtest.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n回测详情已保存: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RPS双90 历史回测")
    parser.add_argument("--hold",        type=int,   default=3,   help="持有最大交易日数（默认3）")
    parser.add_argument("--lookback",    type=int,   default=90,  help="回溯交易日窗口（默认90）")
    parser.add_argument("--interval",   type=int,   default=5,   help="取样间隔交易日（默认5=每周）")
    parser.add_argument("--stop-loss",  type=float, default=8.0, help="止损百分比（默认8）")
    parser.add_argument("--take-profit",type=float, default=20.0,help="止盈百分比（默认20）")
    args = parser.parse_args()
    run_backtest(
        hold_days=args.hold,
        lookback_days=args.lookback,
        interval=args.interval,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
    )
