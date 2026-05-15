#!/usr/bin/env python3
"""
策略-RPS双90 筛选脚本

筛选条件（同时满足）：
  - RPS20 >= 90：近20个交易日收益率在全市场（不含科创板）百分位排名 >= 90
  - RPS90 >= 90：近90个交易日收益率在全市场（不含科创板）百分位排名 >= 90

RPS 排名基于非科创板 A 股全量（参与排名的样本与投资域相同）。

基础过滤（硬过滤，须同时满足）：
  - 非 ST / *ST
  - 上市满 60 个交易日（近似用自然日 90 天）
  - 收盘价 >= 3 元
  - 当日成交额 >= 3000 万元（流动性基本门槛）
  - 不含科创板（688xxx）

综合打分（用于排序）：
  composite_score = 0.40 × RPS20 + 0.60 × RPS90 + 成交额百分位加权（≤ 5 分）

输出文件：
  docs/list/rps90_passed.csv / .md   —— 全部通过双90条件标的
  docs/list/rps90_top5.csv  / .md   —— Top 5
  docs/list/rps90_top20.csv / .md   —— Top 20
  docs/list/rps90_summary.md        —— 运行统计

运行时机：盘后（15:30 后），次日参考持有 5-10 个交易日（中期动量持仓）。
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

# 将 scripts/ 加入 sys.path，支持直接执行或从外部调用
sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_common import (
    OUTPUT_DIR,
    code_to_ts_code,
    fetch_a_no_star_quotes,
    fetch_daily_snapshot,
    fetch_stock_basic,
    fetch_trade_cal_dates,
    get_latest_trade_date,
    ts_code_to_code,
)

# ── 策略配置 ────────────────────────────────────────────────────────────────
MODEL_NAME   = "策略-RPS双90"
OUTPUT_STEM  = "rps90"
TOP_N        = 5

RPS20_MIN    = 90.0           # RPS20 门限
RPS90_MIN    = 90.0           # RPS90 门限
PRICE_MIN    = 3.0            # 最低收盘价（元）
AMOUNT_MIN   = 30_000_000     # 当日成交额最低门槛（元）：3000 万
CAL_DAYS_MIN = 90             # 上市自然日最低要求（近似 60 个交易日）
# ────────────────────────────────────────────────────────────────────────────


def _get_nth_trading_day_back(trade_dates: List[str], n: int) -> str:
    """从交易日列表（升序 YYYYMMDD）末尾倒数第 n 个交易日。"""
    idx = -(n + 1)
    if abs(idx) > len(trade_dates):
        raise ValueError(
            f"交易日历样本不足：需要往前 {n} 个交易日，但历史只有 {len(trade_dates)} 个交易日"
        )
    return trade_dates[idx]


def _fetch_close(trade_date: str, col_alias: str) -> pd.DataFrame:
    """拉取指定交易日收盘价，返回 [ts_code, {col_alias}]。"""
    df = fetch_daily_snapshot(trade_date)
    if df.empty:
        return pd.DataFrame(columns=["ts_code", col_alias])
    df = df[["ts_code", "close"]].copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.rename(columns={"close": col_alias})


def _write_md_table(df: pd.DataFrame, title: str, run_ts: datetime, path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        if df.empty:
            f.write("\n*无符合条件标的*\n")
            return
        f.write(
            "\n| 排名 | 代码 | 名称 | 行业 | RPS20 | RPS90 | 综合分 | 20日涨跌% | 90日涨跌% | 收盘价 |\n"
        )
        f.write("|---:|---:|---|---|---:|---:|---:|---:|---:|---:|\n")
        for _, row in df.iterrows():
            f.write(
                f"| {int(row.get('rank', 0))} "
                f"| {row.get('code', '')} "
                f"| {row.get('name', '')} "
                f"| {row.get('industry', '')} "
                f"| {row.get('rps20', 0):.1f} "
                f"| {row.get('rps90', 0):.1f} "
                f"| {row.get('composite_score', 0):.2f} "
                f"| {row.get('ret_20d_pct', 0):.1f}% "
                f"| {row.get('ret_90d_pct', 0):.1f}% "
                f"| {row.get('close_today', 0):.2f} "
                f"|\n"
            )


def _write_outputs(
    passed: pd.DataFrame,
    run_ts: datetime,
    trade_date: str,
    total_rps_universe: int,
    hard_pass_n: int,
    date_20d: str,
    date_90d: str,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    export_cols = [
        "rank", "code", "name", "industry",
        "score_100", "composite_score", "rps_score",
        "rps20", "rps90",
        "ret_20d_pct", "ret_90d_pct",
        "close_today", "amount_today",
        "trade_date",
    ]
    for c in export_cols:
        if c not in passed.columns:
            passed[c] = np.nan

    top5  = passed.head(TOP_N).copy()
    top20 = passed.head(20).copy()

    # ── CSV 输出 ──
    passed[export_cols].to_csv(
        OUTPUT_DIR / f"{OUTPUT_STEM}_passed.csv",  index=False, encoding="utf-8-sig"
    )
    top5[export_cols].to_csv(
        OUTPUT_DIR / f"{OUTPUT_STEM}_top5.csv",    index=False, encoding="utf-8-sig"
    )
    top20[export_cols].to_csv(
        OUTPUT_DIR / f"{OUTPUT_STEM}_top20.csv",   index=False, encoding="utf-8-sig"
    )

    # ── Markdown 表格 ──
    _write_md_table(top5,   f"{MODEL_NAME} Top 5",         run_ts, OUTPUT_DIR / f"{OUTPUT_STEM}_top5.md")
    _write_md_table(top20,  f"{MODEL_NAME} Top 20",        run_ts, OUTPUT_DIR / f"{OUTPUT_STEM}_top20.md")
    _write_md_table(passed, f"{MODEL_NAME} 全部通过标的",   run_ts, OUTPUT_DIR / f"{OUTPUT_STEM}_passed.md")

    # ── 汇总统计 ──
    with (OUTPUT_DIR / f"{OUTPUT_STEM}_summary.md").open("w", encoding="utf-8") as f:
        f.write(f"# {MODEL_NAME} 筛选统计\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 基准交易日: {trade_date}\n")
        f.write(f"- T-20 交易日: {date_20d}\n")
        f.write(f"- T-90 交易日: {date_90d}\n")
        f.write(f"- 参与 RPS 排名样本（不含科创板）: {total_rps_universe}\n")
        f.write(f"- 硬过滤后样本: {hard_pass_n}\n")
        f.write(
            f"- 通过 RPS双90"
            f"（RPS20≥{RPS20_MIN:.0f} & RPS90≥{RPS90_MIN:.0f}）: {len(passed)}\n"
        )
        f.write(f"\n## 筛选条件\n\n")
        f.write(f"- RPS20 ≥ {RPS20_MIN:.0f}（近 20 个交易日涨幅全市场百分位）\n")
        f.write(f"- RPS90 ≥ {RPS90_MIN:.0f}（近 90 个交易日涨幅全市场百分位）\n")
        f.write(f"- 收盘价 ≥ {PRICE_MIN} 元\n")
        f.write(f"- 当日成交额 ≥ {AMOUNT_MIN / 1e6:.0f} 百万元\n")
        f.write(f"- 上市 ≥ {CAL_DAYS_MIN} 自然日\n")
        f.write(f"- 剔除 ST / *ST，不含科创板\n")
        f.write(f"\n## 综合打分公式\n\n")
        f.write(
            "```\ncomposite_score = 0.40 × RPS20 + 0.60 × RPS90\n"
            "                + 成交额百分位 × 5（最高加 5 分，次要排序因子）\n```\n"
        )
        f.write(f"\n## 输出文件\n\n")
        f.write(f"- `docs/list/{OUTPUT_STEM}_passed.csv/md`\n")
        f.write(f"- `docs/list/{OUTPUT_STEM}_top5.csv/md`\n")
        f.write(f"- `docs/list/{OUTPUT_STEM}_top20.csv/md`\n")

    # ── 历史快照 ──
    snap_dir = OUTPUT_DIR / "history" / OUTPUT_STEM / trade_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    for stem_suffix, src_df in [("top5", top5), ("top20", top20), ("passed", passed), ("summary", None)]:
        src = OUTPUT_DIR / f"{OUTPUT_STEM}_{stem_suffix}.{'md' if stem_suffix == 'summary' else 'csv'}"
        dst = snap_dir / src.name
        if src.exists() and not dst.exists():
            import shutil
            shutil.copy2(src, dst)


def _write_empty_outputs(run_ts: datetime, trade_date: str, total: int, base_n: int) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    header = "rank,code,name,industry,score_100,composite_score,rps_score,rps20,rps90,ret_20d_pct,ret_90d_pct,close_today,amount_today,trade_date\n"
    for suffix in ["passed.csv", "top5.csv", "top20.csv"]:
        (OUTPUT_DIR / f"{OUTPUT_STEM}_{suffix}").write_text(header, encoding="utf-8-sig")
    for suffix in ["passed.md", "top5.md", "top20.md"]:
        (OUTPUT_DIR / f"{OUTPUT_STEM}_{suffix}").write_text(
            f"# {MODEL_NAME}\n\n*无符合条件标的*\n", encoding="utf-8"
        )
    with (OUTPUT_DIR / f"{OUTPUT_STEM}_summary.md").open("w", encoding="utf-8") as f:
        f.write(f"# {MODEL_NAME} 筛选统计\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 基准交易日: {trade_date}\n")
        f.write(f"- 参与 RPS 排名样本: {total}\n")
        f.write(f"- 硬过滤后样本: {base_n}\n")
        f.write(f"- 通过 RPS双90: 0\n")


# ── 主流程 ───────────────────────────────────────────────────────────────────

def run_rps90_screen(trade_date: Optional[str] = None) -> None:
    run_ts = datetime.now()
    print(f"[{MODEL_NAME}] 开始运行: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Step 1：确定基准交易日 & 交易日历 ──────────────────────────────────
    print("[1/4] 获取交易日历...")
    latest_trade_date = trade_date or get_latest_trade_date()
    cal_start = (
        pd.Timestamp(latest_trade_date) - pd.Timedelta(days=160)
    ).strftime("%Y%m%d")
    trade_dates = fetch_trade_cal_dates(cal_start, latest_trade_date)

    if len(trade_dates) < 95:
        print(
            f"[错误] 交易日历样本不足（{len(trade_dates)} 个），"
            "无法计算 RPS90（需至少 95 个交易日），退出"
        )
        return

    date_20d_ago = _get_nth_trading_day_back(trade_dates, 20)
    date_90d_ago = _get_nth_trading_day_back(trade_dates, 90)
    print(
        f"[1/4] 基准日={latest_trade_date}  "
        f"T-20={date_20d_ago}  T-90={date_90d_ago}"
    )

    # ── Step 2：拉取三日收盘价 & 行情快照 ──────────────────────────────────
    print("[2/4] 拉取行情数据（3 个日期快照 + 行情宽表）...")
    close_today = _fetch_close(latest_trade_date, "close_today")
    close_20d   = _fetch_close(date_20d_ago,      "close_20d")
    close_90d   = _fetch_close(date_90d_ago,      "close_90d")

    # 拼接三日价格；只保留三日均有收盘价的股票参与排名
    prices = (
        close_today
        .merge(close_20d, on="ts_code", how="inner")
        .merge(close_90d, on="ts_code", how="inner")
    )
    prices = prices.dropna(subset=["close_today", "close_20d", "close_90d"])
    prices = prices[
        (prices["close_today"] > 0) &
        (prices["close_20d"] > 0) &
        (prices["close_90d"] > 0)
    ].copy()

    # 排除科创板（688xxx）
    prices["code"] = prices["ts_code"].map(ts_code_to_code)
    prices = prices[~prices["code"].str.startswith("688")].copy()

    print(f"[2/4] 三日价格拼接完成，参与 RPS 排名: {len(prices)} 只")

    # ── Step 3：全市场 RPS 计算（在非科创板全量上排名）─────────────────────
    print("[3/4] 计算 RPS20 / RPS90...")
    prices["ret_20d"] = prices["close_today"] / prices["close_20d"] - 1.0
    prices["ret_90d"] = prices["close_today"] / prices["close_90d"] - 1.0

    # 百分位排名（0~100），NaN 置底
    prices["rps20"] = (
        prices["ret_20d"]
        .rank(pct=True, ascending=True, na_option="bottom") * 100.0
    )
    prices["rps90"] = (
        prices["ret_90d"]
        .rank(pct=True, ascending=True, na_option="bottom") * 100.0
    )

    # ── Step 4：拼接基本面 + 行情过滤 ──────────────────────────────────────
    basic = fetch_stock_basic()
    if basic.empty:
        print("[错误] stock_basic 无数据，退出")
        return

    # 只保留主板 + 创业板
    valid_markets = {"主板", "创业板"}
    basic_investable = basic[basic["market"].isin(valid_markets)].copy()

    # 拉取行情（含 deal_amount / name）用于过滤
    quote = fetch_a_no_star_quotes(source="tushare", trade_date=latest_trade_date)
    if quote.empty:
        print("[错误] 无法获取行情快照，退出")
        return

    # 合并：prices（RPS） + basic（industry / listing_date） + quote（name / amount）
    df = prices.merge(
        basic_investable[["secucode", "code", "name", "industry", "listing_date"]].rename(
            columns={"name": "basic_name"}
        ),
        left_on="ts_code",
        right_on="secucode",
        how="inner",
        suffixes=("", "_basic"),
    )
    df = df.rename(columns={"code_basic": "_code_basic"})  # 避免冲突，用 code 列

    # 以 quote 补充当日成交额（deal_amount 单位：元，与 AMOUNT_MIN 一致）
    df = df.merge(
        quote[["secucode", "deal_amount", "name"]].rename(
            columns={"deal_amount": "amount_today", "name": "quote_name"}
        ),
        on="secucode",
        how="left",
    )
    df["name"] = df["basic_name"].fillna(
        df["quote_name"] if "quote_name" in df.columns else ""
    )
    df["amount_today"] = pd.to_numeric(df["amount_today"], errors="coerce").fillna(0)

    # ── 硬过滤 ──────────────────────────────────────────────────────────────
    df["is_st"] = df["name"].str.contains("ST", na=False)
    df["calendar_listed_days"] = (
        pd.Timestamp(latest_trade_date) - pd.to_datetime(df["listing_date"], errors="coerce")
    ).dt.days.fillna(0)

    df["pass_st"]      = ~df["is_st"]
    df["pass_listing"] = df["calendar_listed_days"] >= CAL_DAYS_MIN
    df["pass_price"]   = df["close_today"].fillna(0) >= PRICE_MIN
    df["pass_amount"]  = df["amount_today"] >= AMOUNT_MIN
    df["hard_pass"] = (
        df["pass_st"] & df["pass_listing"] & df["pass_price"] & df["pass_amount"]
    )

    base = df[df["hard_pass"]].copy()
    total_rps_universe = len(prices)
    hard_pass_n = len(base)
    print(
        f"[3/4] 参与排名: {total_rps_universe}  硬过滤后: {hard_pass_n}  "
        f"（非ST {df['pass_st'].sum()}，价格 {df['pass_price'].sum()}，"
        f"成交额 {df['pass_amount'].sum()}，上市期 {df['pass_listing'].sum()}）"
    )

    # ── 双90过滤 ─────────────────────────────────────────────────────────────
    passed = base[
        (base["rps20"] >= RPS20_MIN) & (base["rps90"] >= RPS90_MIN)
    ].copy()
    print(
        f"[4/4] 通过 RPS双90"
        f"（RPS20≥{RPS20_MIN:.0f} & RPS90≥{RPS90_MIN:.0f}）: {len(passed)} 只"
    )

    if passed.empty:
        print("[完成] 无符合条件标的，输出空文件")
        _write_empty_outputs(run_ts, latest_trade_date, total_rps_universe, hard_pass_n)
        return

    # ── 综合打分 ──────────────────────────────────────────────────────────────
    passed["rps_score"] = 0.40 * passed["rps20"] + 0.60 * passed["rps90"]

    # 成交额百分位加权（次要因子，最高 5 分，用于同 RPS 分值时的二次排序）
    amount_rank = passed["amount_today"].rank(pct=True, ascending=True, na_option="bottom")
    passed["amount_bonus"]    = amount_rank * 5.0
    passed["composite_score"] = passed["rps_score"] + passed["amount_bonus"]

    passed = (
        passed.sort_values("composite_score", ascending=False)
        .reset_index(drop=True)
    )
    passed["rank"] = np.arange(1, len(passed) + 1)
    if len(passed) > 1:
        passed["score_100"] = (len(passed) - passed["rank"]) / (len(passed) - 1) * 100.0
    else:
        passed["score_100"] = 100.0

    passed["ret_20d_pct"] = (passed["ret_20d"] * 100).round(2)
    passed["ret_90d_pct"] = (passed["ret_90d"] * 100).round(2)
    passed["trade_date"]  = latest_trade_date

    # ── 输出 ──────────────────────────────────────────────────────────────────
    _write_outputs(
        passed, run_ts, latest_trade_date,
        total_rps_universe, hard_pass_n,
        date_20d_ago, date_90d_ago,
    )

    print(f"[完成] {run_ts.strftime('%H:%M:%S')}  Top{TOP_N}:")
    for _, row in passed.head(TOP_N).iterrows():
        print(
            f"  #{int(row['rank'])}  {row['code']}  {row['name']:<8s}"
            f"  RPS20={row['rps20']:.1f}  RPS90={row['rps90']:.1f}"
            f"  20d={row['ret_20d_pct']:+.1f}%  90d={row['ret_90d_pct']:+.1f}%"
            f"  综合={row['composite_score']:.2f}"
        )


if __name__ == "__main__":
    run_rps90_screen()
