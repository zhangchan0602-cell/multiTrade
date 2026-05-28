#!/usr/bin/env python3
"""
一次性预下载历史数据到本地缓存，供 postclose_t3_history.py 的 T+5 历史回测使用。

下载内容：
  - 交易日历（全量，一个文件）
  - 每交易日：pro.daily / pro.daily_basic / pro.adj_factor（全市场截面）
  - K 线缓存（--include-klines：从已下载截面零 API 调用重建，无需额外请求）

下载完成后，运行盘后/尾盘选股及 T+5 历史回测将不再发起任何 Tushare API 请求。

用法示例：
  # 下载近一年（默认，含往前 90 自然日以保证 ret_60d 所需历史）
  python3 download_history.py

  # 同时预建 K 线缓存（零额外 API 调用，约 1-2 分钟纯本地运算）
  python3 download_history.py --include-klines

  # 指定区间
  python3 download_history.py --start-date 20250101 --end-date 20260515 --include-klines
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# 确保 screen_common 可以直接 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_common import (
    ADJ_FACTOR_CACHE_DIR,
    DAILY_BASIC_CACHE_DIR,
    DAILY_CACHE_DIR,
    KLINE_CACHE_DIR,
    TRADE_CAL_CACHE,
    call_tushare_api,
    fetch_adj_factor_snapshot,
    fetch_daily_basic_snapshot,
    fetch_daily_snapshot,
    fetch_trade_cal_dates,
    get_latest_trade_date,
    get_tushare_pro,
)


def _ensure_trade_cal() -> None:
    """确保全量交易日历已落地到文件。"""
    if TRADE_CAL_CACHE.exists():
        print(f"[trade_cal] 已有缓存 → {TRADE_CAL_CACHE}")
        return
    print("[trade_cal] 下载中 …")
    # fetch_trade_cal_dates 第一次调用时若文件不存在，会自动下载并保存全量
    fetch_trade_cal_dates("20000101", "20991231")
    print(f"[trade_cal] 已保存 → {TRADE_CAL_CACHE}")


def _resolve_dates(start_date: str, end_date: str) -> list[str]:
    """返回 [start_date, end_date] 内的全部交易日。"""
    return fetch_trade_cal_dates(start_date, end_date)


def _download_daily(dates: list[str]) -> None:
    """下载全市场日行情截面，已有文件则跳过。"""
    total = len(dates)
    skip = done = 0
    for i, d in enumerate(dates, 1):
        path = DAILY_CACHE_DIR / f"{d}.csv"
        if path.exists():
            skip += 1
            continue
        fetch_daily_snapshot(d)   # 内部自动写文件
        done += 1
        if done % 20 == 0 or i == total:
            print(f"[daily] {i}/{total}  新增={done}  跳过={skip}")
    print(f"[daily] 完成  新增={done}  跳过={skip}")


def _download_daily_basic(dates: list[str]) -> None:
    """下载每日指标（换手率、量比等），已有文件则跳过。"""
    total = len(dates)
    skip = done = 0
    for i, d in enumerate(dates, 1):
        path = DAILY_BASIC_CACHE_DIR / f"{d}.csv"
        if path.exists():
            skip += 1
            continue
        fetch_daily_basic_snapshot(d)
        done += 1
        if done % 20 == 0 or i == total:
            print(f"[daily_basic] {i}/{total}  新增={done}  跳过={skip}")
    print(f"[daily_basic] 完成  新增={done}  跳过={skip}")


def _download_adj_factor(dates: list[str]) -> None:
    """下载全市场复权因子截面（用于 ret_60d），已有文件则跳过。"""
    total = len(dates)
    skip = done = 0
    for i, d in enumerate(dates, 1):
        path = ADJ_FACTOR_CACHE_DIR / f"{d}.csv"
        if path.exists():
            skip += 1
            continue
        fetch_adj_factor_snapshot(d)
        done += 1
        if done % 20 == 0 or i == total:
            print(f"[adj_factor] {i}/{total}  新增={done}  跳过={skip}")
    print(f"[adj_factor] 完成  新增={done}  跳过={skip}")


def _build_klines_from_snapshots() -> None:
    """从已缓存的全市场截面快照中零 API 调用地重建所有股票 K 线缓存。

    将 .cache/daily/ + .cache/adj_factor/ + .cache/daily_basic/ 三类按日期存储的截面文件
    「转置」聚合成每支股票一个 .cache/kline/{code}.csv，已存在且数据新鲜的文件将跳过。
    """
    daily_files = sorted(DAILY_CACHE_DIR.glob("*.csv"))
    if not daily_files:
        print("[kline-build] 无 daily 缓存文件，请先运行不带 --include-klines 的下载")
        return

    # ── 读取三类截面 ────────────────────────────────────────────────
    print(f"[kline-build] 读取 {len(daily_files)} 个 daily 快照 …")
    daily_frames = []
    for f in daily_files:
        try:
            df = pd.read_csv(f, dtype=str)
            cols = [c for c in ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"] if c in df.columns]
            if not df.empty and len(cols) >= 7:
                daily_frames.append(df[cols])
        except Exception:
            pass
    if not daily_frames:
        print("[kline-build] daily 快照均为空，跳过")
        return
    all_daily = pd.concat(daily_frames, ignore_index=True)

    adj_files = sorted(ADJ_FACTOR_CACHE_DIR.glob("*.csv"))
    print(f"[kline-build] 读取 {len(adj_files)} 个 adj_factor 快照 …")
    adj_frames = []
    for f in adj_files:
        try:
            df = pd.read_csv(f, dtype=str)
            if not df.empty and "adj_factor" in df.columns:
                adj_frames.append(df[["ts_code", "trade_date", "adj_factor"]])
        except Exception:
            pass
    all_adj = pd.concat(adj_frames, ignore_index=True) if adj_frames else pd.DataFrame()

    basic_files = sorted(DAILY_BASIC_CACHE_DIR.glob("*.csv"))
    print(f"[kline-build] 读取 {len(basic_files)} 个 daily_basic 快照 …")
    basic_frames = []
    for f in basic_files:
        try:
            df = pd.read_csv(f, dtype=str)
            if not df.empty and "turnover_rate_f" in df.columns:
                basic_frames.append(df[["ts_code", "trade_date", "turnover_rate_f"]])
        except Exception:
            pass
    all_basic = pd.concat(basic_frames, ignore_index=True) if basic_frames else pd.DataFrame()

    # ── 合并三张表 ───────────────────────────────────────────────────
    print("[kline-build] 合并数据并转换类型 …")
    merged = all_daily.copy()
    if not all_adj.empty:
        merged = merged.merge(all_adj, on=["ts_code", "trade_date"], how="left")
    else:
        merged["adj_factor"] = np.nan
    if not all_basic.empty:
        merged = merged.merge(
            all_basic.rename(columns={"turnover_rate_f": "turnover"}),
            on=["ts_code", "trade_date"], how="left",
        )
    else:
        merged["turnover"] = np.nan

    merged["trade_date"] = pd.to_datetime(merged["trade_date"], format="%Y%m%d", errors="coerce")
    for c in ["open", "high", "low", "close", "vol", "amount", "adj_factor", "turnover"]:
        merged[c] = pd.to_numeric(merged[c], errors="coerce")
    # pro.daily 成交额单位千元，与 fetch_tushare_kline_frame 保持一致（转为元）
    merged["amount"] = merged["amount"] * 1000.0

    # ── 按股票写入 K 线文件 ─────────────────────────────────────────
    KLINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    groups = list(merged.groupby("ts_code", sort=False))
    total = len(groups)
    skip = done = 0
    print(f"[kline-build] 共 {total} 支股票，写入缓存 …")

    for i, (ts_code, grp) in enumerate(groups, 1):
        code = str(ts_code).split(".")[0].zfill(6)
        cache_path = KLINE_CACHE_DIR / f"{code}.csv"

        grp = (
            grp.sort_values("trade_date")
            .drop_duplicates(subset=["trade_date"], keep="last")
            .reset_index(drop=True)
        )

        # 已有缓存且数据新鲜则跳过
        if cache_path.exists():
            try:
                existing = pd.read_csv(cache_path, parse_dates=["trade_date"])
                if not existing.empty:
                    ex_max = existing["trade_date"].max()
                    new_max = grp["trade_date"].max()
                    if pd.notna(ex_max) and pd.notna(new_max) and ex_max >= new_max - pd.Timedelta(days=3):
                        skip += 1
                        if i % 500 == 0 or i == total:
                            print(f"[kline-build] {i}/{total}  新增/更新={done}  跳过={skip}")
                        continue
            except Exception:
                pass

        # 应用后复权修正（与 fetch_tushare_kline_frame 逻辑相同）
        latest_adj = grp["adj_factor"].dropna()
        if latest_adj.empty or float(latest_adj.iloc[-1]) <= 0:
            skip += 1
            continue
        scale = grp["adj_factor"] / float(latest_adj.iloc[-1])
        for c in ["open", "high", "low", "close"]:
            grp = grp.copy()
            grp[c] = grp[c] * scale

        kline = grp[["trade_date", "open", "high", "low", "close", "vol", "amount", "turnover"]].copy()

        # 有旧缓存则合并（保留更早历史）
        if cache_path.exists():
            try:
                existing = pd.read_csv(cache_path, parse_dates=["trade_date"])
                kline = (
                    pd.concat([existing, kline], ignore_index=True)
                    .sort_values("trade_date")
                    .drop_duplicates(subset=["trade_date"], keep="last")
                    .reset_index(drop=True)
                )
            except Exception:
                pass

        kline.to_csv(cache_path, index=False)
        done += 1
        if done % 500 == 0 or i == total:
            print(f"[kline-build] {i}/{total}  新增/更新={done}  跳过={skip}")

    print(f"[kline-build] 完成  新增/更新={done}  跳过={skip}")


def main() -> None:
    parser = argparse.ArgumentParser(description="预下载历史数据到本地缓存")
    parser.add_argument(
        "--start-date", default=None,
        help="数据开始日期 YYYYMMDD（默认：回测起点往前 90 自然日，约保证 ret_60d 所需历史）",
    )
    parser.add_argument(
        "--end-date", default=None,
        help="数据结束日期 YYYYMMDD（默认：最近交易日）",
    )
    parser.add_argument(
        "--backtest-start", default=None,
        help="回测信号起始日 YYYYMMDD（用于推算 start-date，默认一年前）",
    )
    parser.add_argument(
        "--include-klines", action="store_true", default=False,
        help="从已下载的截面快照中零 API 调用地重建全市场 K 线缓存（约 1-2 分钟纯本地运算）",
    )
    args = parser.parse_args()

    # 推算默认日期范围
    end_date = args.end_date or get_latest_trade_date()
    if args.start_date:
        start_date = args.start_date
    else:
        if args.backtest_start:
            backtest_start = pd.to_datetime(args.backtest_start, format="%Y%m%d")
        else:
            backtest_start = pd.to_datetime(end_date, format="%Y%m%d") - pd.Timedelta(days=365)
        # 往前多推 90 自然日，保证 ret_60d（需约 60 个交易日的历史收盘价）能正常计算
        start_date = (backtest_start - pd.Timedelta(days=90)).strftime("%Y%m%d")

    print("=" * 60)
    print(f"预下载区间：{start_date}  →  {end_date}")
    print("=" * 60)

    t0 = time.monotonic()

    # 1. 交易日历（全量，一次搞定）
    _ensure_trade_cal()

    # 2. 获取区间内所有交易日
    dates = _resolve_dates(start_date, end_date)
    print(f"交易日共 {len(dates)} 天")

    # 3. 三类截面数据并行下载（串行，受 TUSHARE_MIN_INTERVAL 限速）
    print("\n--- 下载全市场日行情 (pro.daily) ---")
    _download_daily(dates)

    print("\n--- 下载每日指标 (pro.daily_basic) ---")
    _download_daily_basic(dates)

    print("\n--- 下载复权因子 (pro.adj_factor) ---")
    _download_adj_factor(dates)

    if args.include_klines:
        print("\n--- 从截面快照重建 K 线缓存（零 API 调用）---")
        _build_klines_from_snapshots()

    elapsed = time.monotonic() - t0
    mins, secs = divmod(int(elapsed), 60)

    print("\n" + "=" * 60)
    print(f"全部完成，耗时 {mins}m{secs}s")
    print(f"缓存目录：{DAILY_CACHE_DIR.parent}")
    if not args.include_klines:
        print()
        print("提示：加上 --include-klines 可从已下载快照零 API 调用预建全市场 K 线缓存，")
        print("      此后尾盘/盘后选股及回测均无需再请求 Tushare。")
    print("=" * 60)


if __name__ == "__main__":
    main()
