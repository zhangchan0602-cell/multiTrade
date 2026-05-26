#!/usr/bin/env python3
"""
龙头抱团模型筛选脚本

策略定位：
  寻找在所属行业内持续领先（行业龙头），并具备机构抱团特征（
  持续大资金净流入、低波动趋势上涨、均线多头排列）的中期标的。

筛选逻辑：
  1. 行业内相对强势（20/60日收益率行业内百分位排名 ≥ 70%，板块龙头效应）
  2. 趋势质量：MA20/MA60 多头排列，趋势斜率向上，趋势效率高
  3. 抱团强度：5/20日成交额比持续放大，资金流偏向净买入，价量同步
  4. 稳定性：低波动率、低最大回撤、底部筹码收敛

硬过滤（须同时满足）：
  - 非 ST / *ST
  - 上市满 90 自然日（近似 60 个交易日）
  - 收盘价 ≥ 5 元（机构偏好绝对价格门槛）
  - 成交额 ≥ 5000 万元（中期龙头流动性门槛）
  - 不含科创板（688xxx）
  - 20日均线 ≥ 60日均线（中期多头排列，非触底翻转）

综合打分：
  composite_score = 0.30 × 行业领先得分
                  + 0.25 × 趋势质量得分
                  + 0.25 × 抱团强度得分
                  + 0.20 × 稳定性得分

输出文件：
  docs/list/leader_passed.csv / .md   —— 全部通过标的
  docs/list/leader_top5.csv  / .md   —— Top 5
  docs/list/leader_top20.csv / .md   —— Top 20
  docs/list/leader_summary.md        —— 运行统计
  docs/list/history/leader/YYYY-MM-DD/leader_*  —— 历史快照

运行时机：盘后（15:30 后），目标持有 5-15 个交易日（中期趋势持仓）。
"""

import sys
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_common import (
    OUTPUT_DIR,
    code_to_ts_code,
    fetch_a_no_star_quotes,
    fetch_daily_snapshot,
    fetch_kline_frame,
    fetch_stock_basic,
    fetch_trade_cal_dates,
    get_latest_trade_date,
    industry_zscore,
    ts_code_to_code,
    winsorize,
)

# ── 策略配置 ────────────────────────────────────────────────────────────────
MODEL_NAME       = "策略-龙头抱团"
OUTPUT_STEM      = "leader"
TOP_N            = 5

PRICE_MIN        = 5.0           # 最低收盘价（元），机构偏好绝对价格门槛
AMOUNT_MIN       = 50_000_000    # 最低成交额（元）：5000 万
CAL_DAYS_MIN     = 90            # 上市自然日最低要求
INDUSTRY_RANK_MIN = 70.0         # 行业内收益率百分位门槛（%）
INDUSTRY_MIN_MEMBERS = 10        # 行业样本深度门槛，避免小行业分位失真
MA_BULL_MIN      = 0.0           # MA20/MA60 多头排列（MA20 ≥ MA60）
TREND_SCORE_MIN  = 0.0           # 趋势质量最低得分地板
CLUSTER_SCORE_MIN = 0.0          # 抱团强度最低得分地板
STABILITY_SCORE_MIN = 0.0        # 稳定性最低得分地板
MAX_PER_INDUSTRY = 4             # 最终通过名单单行业上限，抑制行业拥挤

# K线候选数量
KLINE_CANDIDATE_LIMIT = 4000      # 最多拉取 K 线的候选数量
KLINE_WORKERS         = 8
KLINE_RETRIES         = 2

SCORE_WEIGHTS = {
    "leadership": 0.30,
    "trend":      0.25,
    "cluster":    0.25,
    "stability":  0.20,
}
# ────────────────────────────────────────────────────────────────────────────


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _get_nth_trading_day_back(trade_dates: List[str], n: int) -> str:
    idx = -(n + 1)
    if abs(idx) > len(trade_dates):
        raise ValueError(
            f"交易日历样本不足：需要往前 {n} 个交易日，但历史只有 {len(trade_dates)} 个交易日"
        )
    return trade_dates[idx]


def _fetch_close(trade_date: str, col_alias: str) -> pd.DataFrame:
    df = fetch_daily_snapshot(trade_date)
    if df.empty:
        return pd.DataFrame(columns=["ts_code", col_alias])
    df = df[["ts_code", "close"]].copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.rename(columns={"close": col_alias})


def pct_change_from(values: np.ndarray, periods: int) -> float:
    if len(values) <= periods or values[-periods - 1] <= 0:
        return np.nan
    return float(values[-1] / values[-periods - 1] - 1.0)


def trailing_mean(values: np.ndarray, window: int) -> float:
    if len(values) < window:
        return np.nan
    return float(np.nanmean(values[-window:]))


def slope_pct(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 5 or np.nanmean(values) <= 0:
        return np.nan
    x = np.arange(len(values), dtype=float)
    slope = np.polyfit(x, values, 1)[0]
    return float(slope / np.nanmean(values))


def safe_ratio(numerator: float, denominator: float) -> float:
    if pd.notna(numerator) and pd.notna(denominator) and denominator > 0:
        return float(numerator / denominator)
    return np.nan


def series_corr(x: np.ndarray, y: np.ndarray, min_obs: int = 5) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    if valid.sum() < min_obs:
        return np.nan
    x, y = x[valid], y[valid]
    if np.nanstd(x) <= 1e-12 or np.nanstd(y) <= 1e-12:
        return np.nan
    return float(np.corrcoef(x, y)[0, 1])


# ── K 线特征提取（龙头抱团版，偏重中期趋势与持续性）────────────────────────

def get_leader_kline_feature(
    code: str,
    retries: int = 3,
    end_trade_date: Optional[str] = None,
) -> Dict:
    """提取龙头抱团模型所需 K 线特征（中期视角，关注60日趋势质量与抱团连续性）。"""
    anchor = (
        pd.to_datetime(end_trade_date, format="%Y%m%d", errors="coerce")
        if end_trade_date else pd.Timestamp.now()
    )
    if pd.isna(anchor):
        anchor = pd.Timestamp.now()
    end_date = anchor.strftime("%Y%m%d")
    start_date = (anchor - pd.Timedelta(days=280)).strftime("%Y%m%d")

    import time
    last_err = None
    for i in range(retries):
        try:
            kline = fetch_kline_frame(code, start_date=start_date, end_date=end_date, source="tushare")
            if kline.empty:
                raise RuntimeError("empty kline")

            opens   = pd.to_numeric(kline["open"],     errors="coerce").to_numpy(dtype=float)
            closes  = pd.to_numeric(kline["close"],    errors="coerce").to_numpy(dtype=float)
            highs   = pd.to_numeric(kline["high"],     errors="coerce").to_numpy(dtype=float)
            lows    = pd.to_numeric(kline["low"],      errors="coerce").to_numpy(dtype=float)
            volumes = pd.to_numeric(kline["vol"],      errors="coerce").to_numpy(dtype=float)
            amounts = pd.to_numeric(kline["amount"],   errors="coerce").to_numpy(dtype=float)
            turns   = pd.to_numeric(kline["turnover"], errors="coerce").to_numpy(dtype=float)

            valid = np.isfinite(closes) & np.isfinite(highs) & np.isfinite(lows) & np.isfinite(opens)
            if valid.sum() < 30:
                raise RuntimeError("insufficient history")

            c = closes[valid]
            h = highs[valid]
            l = lows[valid]
            o = opens[valid]
            a = amounts[valid]
            t = turns[valid]
            rets = c[1:] / c[:-1] - 1.0

            # ── 收益率 ──
            ret_5  = pct_change_from(c, 5)
            ret_10 = pct_change_from(c, 10)
            ret_20 = pct_change_from(c, 20)
            ret_60 = pct_change_from(c, 60)

            # ── 均线与趋势 ──
            ma5  = trailing_mean(c, 5)
            ma10 = trailing_mean(c, 10)
            ma20 = trailing_mean(c, 20)
            ma60 = trailing_mean(c, 60)

            ma_bull_20_60    = safe_ratio(ma20, ma60) - 1.0 if (pd.notna(ma20) and pd.notna(ma60)) else np.nan
            ma_alignment_20  = (
                0.6 * (safe_ratio(ma5, ma20) - 1.0) + 0.4 * (safe_ratio(ma10, ma20) - 1.0)
                if pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20) else np.nan
            )
            trend_slope_60   = slope_pct(c[-60:]) if len(c) >= 60 else np.nan
            trend_slope_20   = slope_pct(c[-20:]) if len(c) >= 20 else np.nan

            # 趋势效率：20日累计涨幅 / 日涨跌幅绝对值之和（越高说明方向越纯）
            abs_path_20 = float(np.nansum(np.abs(rets[-20:]))) if len(rets) >= 20 else np.nan
            trend_eff_20 = safe_ratio(ret_20, abs_path_20) if pd.notna(abs_path_20) and abs_path_20 > 0 else np.nan

            abs_path_60 = float(np.nansum(np.abs(rets[-60:]))) if len(rets) >= 60 else np.nan
            trend_eff_60 = safe_ratio(ret_60, abs_path_60) if pd.notna(abs_path_60) and abs_path_60 > 0 else np.nan

            # ── 成交量/额特征（抱团连续性）──
            avg_amount_5  = trailing_mean(a, 5)
            avg_amount_10 = trailing_mean(a, 10)
            avg_amount_20 = trailing_mean(a, 20)
            avg_amount_60 = trailing_mean(a, 60)

            amount_ratio_5_20  = safe_ratio(avg_amount_5,  avg_amount_20)
            amount_ratio_10_20 = safe_ratio(avg_amount_10, avg_amount_20)
            amount_ratio_20_60 = safe_ratio(avg_amount_20, avg_amount_60)

            # 资金流偏向（20日，上涨日成交额 - 下跌日成交额）/ 总成交额
            flow_rets_20   = rets[-20:] if len(rets) >= 20 else np.array([])
            flow_amounts_20 = a[1:][-20:] if len(a) >= 21 else np.array([])
            if len(flow_rets_20) == len(flow_amounts_20) and len(flow_rets_20) > 0:
                up_a   = float(np.nansum(flow_amounts_20[flow_rets_20 > 0]))
                down_a = float(np.nansum(flow_amounts_20[flow_rets_20 < 0]))
                money_flow_bias_20 = safe_ratio(up_a - down_a, up_a + down_a)
            else:
                money_flow_bias_20 = np.nan

            # 资金流偏向（60日）
            flow_rets_60   = rets[-60:] if len(rets) >= 60 else np.array([])
            flow_amounts_60 = a[1:][-60:] if len(a) >= 61 else np.array([])
            if len(flow_rets_60) == len(flow_amounts_60) and len(flow_rets_60) > 0:
                up_a60   = float(np.nansum(flow_amounts_60[flow_rets_60 > 0]))
                down_a60 = float(np.nansum(flow_amounts_60[flow_rets_60 < 0]))
                money_flow_bias_60 = safe_ratio(up_a60 - down_a60, up_a60 + down_a60)
            else:
                money_flow_bias_60 = np.nan

            # 价量同步（10日，涨幅与成交额变化率相关性）
            amount_rets = a[1:] / a[:-1] - 1.0 if len(a) >= 2 else np.array([])
            pv_sync_10 = (
                series_corr(rets[-10:], amount_rets[-10:], min_obs=6)
                if len(rets) >= 10 and len(amount_rets) >= 10 else np.nan
            )
            pv_sync_20 = (
                series_corr(rets[-20:], amount_rets[-20:], min_obs=10)
                if len(rets) >= 20 and len(amount_rets) >= 20 else np.nan
            )

            # 换手率
            def _nanmean_safe(arr):
                a = arr[~np.isnan(arr)]
                return float(np.mean(a)) if len(a) > 0 else np.nan

            turnover_5  = _nanmean_safe(t[-5:])  if len(t) >= 5  else np.nan
            turnover_20 = _nanmean_safe(t[-20:]) if len(t) >= 20 else np.nan
            turnover_60 = _nanmean_safe(t[-60:]) if len(t) >= 60 else np.nan

            # ── 稳定性 ──
            vol_20 = float(np.std(rets[-20:], ddof=0)) if len(rets) >= 20 else np.nan
            vol_60 = float(np.std(rets[-60:], ddof=0)) if len(rets) >= 60 else np.nan

            downside_20 = rets[-20:][rets[-20:] < 0] if len(rets) >= 20 else np.array([])
            downside_vol_20 = float(np.std(downside_20, ddof=0)) if len(downside_20) >= 3 else 0.0

            max_drawdown_20 = np.nan
            if len(c) >= 20:
                trail = c[-20:]
                run_max = np.maximum.accumulate(trail)
                max_drawdown_20 = float(np.nanmin(trail / run_max - 1.0))

            max_drawdown_60 = np.nan
            if len(c) >= 60:
                trail = c[-60:]
                run_max = np.maximum.accumulate(trail)
                max_drawdown_60 = float(np.nanmin(trail / run_max - 1.0))

            # 底部收敛度：前15日价格波动收窄程度（越低越紧密）
            vol_base = float(np.std(rets[-20:-5], ddof=0)) if len(rets) >= 20 else np.nan
            daily_range = np.where(l > 0, h / l - 1.0, np.nan)
            range_base  = float(np.nanmean(daily_range[-20:-5])) if len(daily_range) >= 20 else np.nan
            chip_tightness = -(0.6 * vol_base + 0.4 * range_base) if (pd.notna(vol_base) and pd.notna(range_base)) else np.nan

            # 收盘强度
            day_range = h - l
            close_strength = np.full(len(day_range), np.nan, dtype=float)
            np.divide(c - l, day_range, out=close_strength, where=day_range > 0)
            close_strength_5  = float(np.nanmean(close_strength[-5:]))  if len(close_strength) >= 5  else np.nan
            close_strength_20 = float(np.nanmean(close_strength[-20:])) if len(close_strength) >= 20 else np.nan

            win_rate_20 = float(np.mean(rets[-20:] > 0)) if len(rets) >= 20 else np.nan
            win_rate_60 = float(np.mean(rets[-60:] > 0)) if len(rets) >= 60 else np.nan

            return {
                "code": code,
                "kline_ok": 1,
                "listed_days_kline": int(len(c)),
                # 收益率
                "ret_5":  ret_5,
                "ret_10": ret_10,
                "ret_20": ret_20,
                "ret_60": ret_60,
                # 趋势
                "ma_bull_20_60":   ma_bull_20_60,
                "ma_alignment_20": ma_alignment_20,
                "trend_slope_60":  trend_slope_60,
                "trend_slope_20":  trend_slope_20,
                "trend_eff_20":    trend_eff_20,
                "trend_eff_60":    trend_eff_60,
                # 成交/资金（抱团）
                "avg_amount_20":      avg_amount_20,
                "avg_amount_60":      avg_amount_60,
                "amount_ratio_5_20":  amount_ratio_5_20,
                "amount_ratio_10_20": amount_ratio_10_20,
                "amount_ratio_20_60": amount_ratio_20_60,
                "money_flow_bias_20": money_flow_bias_20,
                "money_flow_bias_60": money_flow_bias_60,
                "pv_sync_10":         pv_sync_10,
                "pv_sync_20":         pv_sync_20,
                "turnover_5":         turnover_5,
                "turnover_20":        turnover_20,
                "turnover_60":        turnover_60,
                # 稳定性
                "vol_20":           vol_20,
                "vol_60":           vol_60,
                "downside_vol_20":  downside_vol_20,
                "max_drawdown_20":  max_drawdown_20,
                "max_drawdown_60":  max_drawdown_60,
                "chip_tightness":   chip_tightness,
                "close_strength_5": close_strength_5,
                "close_strength_20": close_strength_20,
                "win_rate_20":      win_rate_20,
                "win_rate_60":      win_rate_60,
            }
        except Exception as e:
            last_err = e
            time.sleep(0.8 * (1.5 ** i))

    return {
        "code": code,
        "kline_ok": 0,
        "listed_days_kline": np.nan,
        "ret_5": np.nan, "ret_10": np.nan, "ret_20": np.nan, "ret_60": np.nan,
        "ma_bull_20_60": np.nan, "ma_alignment_20": np.nan,
        "trend_slope_60": np.nan, "trend_slope_20": np.nan,
        "trend_eff_20": np.nan, "trend_eff_60": np.nan,
        "avg_amount_20": np.nan, "avg_amount_60": np.nan,
        "amount_ratio_5_20": np.nan, "amount_ratio_10_20": np.nan, "amount_ratio_20_60": np.nan,
        "money_flow_bias_20": np.nan, "money_flow_bias_60": np.nan,
        "pv_sync_10": np.nan, "pv_sync_20": np.nan,
        "turnover_5": np.nan, "turnover_20": np.nan, "turnover_60": np.nan,
        "vol_20": np.nan, "vol_60": np.nan, "downside_vol_20": np.nan,
        "max_drawdown_20": np.nan, "max_drawdown_60": np.nan,
        "chip_tightness": np.nan, "close_strength_5": np.nan, "close_strength_20": np.nan,
        "win_rate_20": np.nan, "win_rate_60": np.nan,
        "kline_err": str(last_err)[:120] if last_err else "",
    }


def fetch_leader_kline_features(
    codes: List[str],
    max_workers: int = KLINE_WORKERS,
    retries: int = KLINE_RETRIES,
    end_trade_date: Optional[str] = None,
) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()

    out = []
    total = len(codes)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {
            ex.submit(get_leader_kline_feature, c, retries, end_trade_date): c
            for c in codes
        }
        for fut in as_completed(futs):
            done += 1
            out.append(fut.result())
            if done % 100 == 0 or done == total:
                ok = sum(1 for r in out if r.get("kline_ok") == 1)
                print(f"[kline-leader] {done}/{total}, success={ok}")
    return pd.DataFrame(out)


# ── 打分 ──────────────────────────────────────────────────────────────────────

LEADER_RAW_FACTORS = [
    # 收益率
    "ret_20", "ret_60",
    # 行业内排名（在 run_leader_screen 中计算后填入）
    "sector_rank_20", "sector_rank_60",
    # 趋势
    "ma_bull_20_60", "ma_alignment_20",
    "trend_slope_60", "trend_slope_20",
    "trend_eff_20", "trend_eff_60",
    # 抱团（成交/资金）
    "amount_ratio_5_20", "amount_ratio_10_20", "amount_ratio_20_60",
    "money_flow_bias_20", "money_flow_bias_60",
    "pv_sync_10", "pv_sync_20",
    # 稳定性（负向因子取反后传入）
    "neg_vol_20", "neg_vol_60",
    "neg_downside_vol_20",
    "neg_max_drawdown_20", "neg_max_drawdown_60",
    "chip_tightness",
    "win_rate_20", "win_rate_60",
    "close_strength_5",
    # 流动性
    "avg_amount_20",
]


def score_leaders(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 构造负向因子（越低越好 → 取反后越高越好）
    df["neg_vol_20"]          = -df["vol_20"]
    df["neg_vol_60"]          = -df["vol_60"]
    df["neg_downside_vol_20"] = -df["downside_vol_20"]
    df["neg_max_drawdown_20"] = df["max_drawdown_20"]   # drawdown 已是负数，直接取值越大越好
    df["neg_max_drawdown_60"] = df["max_drawdown_60"]

    # Winsorize + 行业 Z-score
    for col in LEADER_RAW_FACTORS:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = winsorize(pd.to_numeric(df[col], errors="coerce"), p=0.025)
        df[f"{col}_z"] = (
            industry_zscore(df[col], df["industry"])
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )

    # ── 行业领先得分（sector_leadership_score）──
    # 衡量个股相对本行业的超额收益与排名，是"龙头"的核心指标
    df["leadership_score"] = (
        0.40 * df["sector_rank_20_z"]
        + 0.40 * df["sector_rank_60_z"]
        + 0.12 * df["ret_20_z"]
        + 0.08 * df["ret_60_z"]
    )

    # ── 趋势质量得分（trend_score）──
    # 评估中期趋势的方向性与持续性，MA多头排列是核心
    df["trend_score"] = (
        0.30 * df["ma_bull_20_60_z"]
        + 0.20 * df["ma_alignment_20_z"]
        + 0.16 * df["trend_slope_60_z"]
        + 0.12 * df["trend_slope_20_z"]
        + 0.12 * df["trend_eff_60_z"]
        + 0.10 * df["trend_eff_20_z"]
    )

    # ── 抱团强度得分（cluster_score）──
    # 衡量资金的持续性与方向一致性，是"抱团"的核心指标
    df["cluster_score"] = (
        0.22 * df["money_flow_bias_60_z"]
        + 0.18 * df["money_flow_bias_20_z"]
        + 0.16 * df["pv_sync_20_z"]
        + 0.12 * df["pv_sync_10_z"]
        + 0.14 * df["amount_ratio_20_60_z"]
        + 0.10 * df["amount_ratio_5_20_z"]
        + 0.08 * df["amount_ratio_10_20_z"]
    )

    # ── 稳定性得分（stability_score）──
    # 低波动 + 低回撤 + 筹码收敛，确认是机构有序运作而非散户炒作
    df["stability_score"] = (
        0.20 * df["neg_max_drawdown_60_z"]
        + 0.16 * df["neg_max_drawdown_20_z"]
        + 0.16 * df["neg_vol_60_z"]
        + 0.12 * df["neg_vol_20_z"]
        + 0.12 * df["chip_tightness_z"]
        + 0.10 * df["win_rate_60_z"]
        + 0.08 * df["win_rate_20_z"]
        + 0.06 * df["close_strength_5_z"]
    )

    # ── 流动性得分 ──
    df["liquidity_score"] = df["avg_amount_20_z"]

    # ── 综合打分 ──
    w = SCORE_WEIGHTS
    df["score"] = (
        w["leadership"] * df["leadership_score"]
        + w["trend"]      * df["trend_score"]
        + w["cluster"]    * df["cluster_score"]
        + w["stability"]  * df["stability_score"]
    )

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    df["score_raw"] = df["score"]
    if len(df) > 1:
        df["score_100"] = (len(df) - df["rank"]) / (len(df) - 1) * 100.0
    elif len(df) == 1:
        df["score_100"] = 100.0
    else:
        df["score_100"] = np.nan
    return df


def apply_leader_selection_guards(df: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, float]]:
    df = df.copy()
    trend_score_min = float(os.environ.get("LEADER_TREND_SCORE_MIN", str(TREND_SCORE_MIN)))
    cluster_score_min = float(os.environ.get("LEADER_CLUSTER_SCORE_MIN", str(CLUSTER_SCORE_MIN)))
    stability_score_min = float(os.environ.get("LEADER_STABILITY_SCORE_MIN", str(STABILITY_SCORE_MIN)))
    max_per_industry = max(1, int(os.environ.get("LEADER_MAX_PER_INDUSTRY", str(MAX_PER_INDUSTRY))))

    df["pass_trend_score"] = pd.to_numeric(df["trend_score"], errors="coerce").ge(trend_score_min)
    df["pass_cluster_score"] = pd.to_numeric(df["cluster_score"], errors="coerce").ge(cluster_score_min)
    df["pass_stability_score"] = pd.to_numeric(df["stability_score"], errors="coerce").ge(stability_score_min)
    df["pass_quality_floor"] = (
        df["pass_trend_score"]
        & df["pass_cluster_score"]
        & df["pass_stability_score"]
    )

    quality_pass_n = int(df["pass_quality_floor"].sum())
    df = df[df["pass_quality_floor"]].copy()
    if df.empty:
        return df, {
            "trend_score_min": trend_score_min,
            "cluster_score_min": cluster_score_min,
            "stability_score_min": stability_score_min,
            "max_per_industry": float(max_per_industry),
            "quality_pass_n": float(quality_pass_n),
            "industry_cap_pass_n": 0.0,
        }

    df = df.sort_values(["score", "leadership_score", "amount_today"], ascending=False).reset_index(drop=True)
    df["industry_rank"] = df.groupby("industry").cumcount() + 1
    df["pass_industry_cap"] = df["industry_rank"] <= max_per_industry
    industry_cap_pass_n = int(df["pass_industry_cap"].sum())
    df = df[df["pass_industry_cap"]].copy().reset_index(drop=True)

    df["rank"] = np.arange(1, len(df) + 1)
    if len(df) > 1:
        df["score_100"] = (len(df) - df["rank"]) / (len(df) - 1) * 100.0
    elif len(df) == 1:
        df["score_100"] = 100.0
    else:
        df["score_100"] = np.nan

    return df, {
        "trend_score_min": trend_score_min,
        "cluster_score_min": cluster_score_min,
        "stability_score_min": stability_score_min,
        "max_per_industry": float(max_per_industry),
        "quality_pass_n": float(quality_pass_n),
        "industry_cap_pass_n": float(industry_cap_pass_n),
    }


# ── 输出 ──────────────────────────────────────────────────────────────────────

def _write_md_table(df: pd.DataFrame, title: str, run_ts: datetime, path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        if df.empty:
            f.write("\n*无符合条件标的*\n")
            return
        f.write(
            "\n| 排名 | 代码 | 名称 | 行业 | 综合分 | 领先分 | 趋势分 | 抱团分 | 稳定分 |"
            " 20日涨% | 60日涨% | MA多头 | 收盘价 |\n"
        )
        f.write("|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for _, row in df.iterrows():
            f.write(
                f"| {int(row.get('rank', 0))} "
                f"| {row.get('code', '')} "
                f"| {row.get('name', '')} "
                f"| {row.get('industry', '')} "
                f"| {float(row.get('score_100', 0)):.2f} "
                f"| {float(row.get('leadership_score', 0)):.4f} "
                f"| {float(row.get('trend_score', 0)):.4f} "
                f"| {float(row.get('cluster_score', 0)):.4f} "
                f"| {float(row.get('stability_score', 0)):.4f} "
                f"| {float(row.get('ret_20', 0)) * 100:.1f}% "
                f"| {float(row.get('ret_60', 0)) * 100:.1f}% "
                f"| {float(row.get('ma_bull_20_60', 0)) * 100:.1f}% "
                f"| {float(row.get('close', 0)):.2f} "
                f"|\n"
            )


def _write_outputs(
    passed: pd.DataFrame,
    run_ts: datetime,
    trade_date: str,
    total_universe: int,
    hard_pass_n: int,
    kline_ok_n: int,
    filter_stats: Optional[Dict[str, float]] = None,
    copy_history: bool = True,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    export_cols = [
        "rank", "code", "name", "industry",
        "score_100", "score_raw",
        "leadership_score", "trend_score", "cluster_score", "stability_score", "liquidity_score",
        "sector_rank_20", "sector_rank_60",
        "ret_20", "ret_60",
        "ma_bull_20_60", "ma_alignment_20",
        "trend_slope_60", "trend_slope_20",
        "trend_eff_20", "trend_eff_60",
        "amount_ratio_5_20", "amount_ratio_20_60",
        "money_flow_bias_20", "money_flow_bias_60",
        "pv_sync_20",
        "vol_20", "vol_60",
        "max_drawdown_20", "max_drawdown_60",
        "chip_tightness",
        "win_rate_20", "win_rate_60",
        "avg_amount_20",
        "close",
        "trade_date",
    ]
    for col in export_cols:
        if col not in passed.columns:
            passed[col] = np.nan

    top5  = passed.head(TOP_N).copy()
    top20 = passed.head(20).copy()

    passed[export_cols].to_csv(OUTPUT_DIR / f"{OUTPUT_STEM}_passed.csv",  index=False, encoding="utf-8-sig")
    top5[export_cols].to_csv(  OUTPUT_DIR / f"{OUTPUT_STEM}_top5.csv",    index=False, encoding="utf-8-sig")
    top20[export_cols].to_csv( OUTPUT_DIR / f"{OUTPUT_STEM}_top20.csv",   index=False, encoding="utf-8-sig")

    _write_md_table(top5,   f"{MODEL_NAME} Top 5",         run_ts, OUTPUT_DIR / f"{OUTPUT_STEM}_top5.md")
    _write_md_table(top20,  f"{MODEL_NAME} Top 20",        run_ts, OUTPUT_DIR / f"{OUTPUT_STEM}_top20.md")
    _write_md_table(passed, f"{MODEL_NAME} 全部通过标的",   run_ts, OUTPUT_DIR / f"{OUTPUT_STEM}_passed.md")

    with (OUTPUT_DIR / f"{OUTPUT_STEM}_summary.md").open("w", encoding="utf-8") as f:
        f.write(f"# {MODEL_NAME} 筛选统计\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 基准交易日: {trade_date}\n")
        f.write(f"- 全A（不含科创板）宇宙: {total_universe}\n")
        f.write(f"- 硬过滤后样本: {hard_pass_n}\n")
        f.write(f"- 成功拉取 K 线: {kline_ok_n}\n")
        if filter_stats:
            f.write(f"- 分数地板通过样本: {int(filter_stats.get('quality_pass_n', 0))}\n")
            f.write(f"- 行业拥挤约束后样本: {int(filter_stats.get('industry_cap_pass_n', 0))}\n")
        f.write(f"- 最终通过标的: {len(passed)}\n")
        f.write(f"\n## 策略特征\n\n")
        f.write(f"- 策略类型: 龙头抱团（行业龙头 + 机构抱团特征）\n")
        f.write(f"- 运行时机: 盘后 15:30 后\n")
        f.write(f"- 目标持仓: 5-15 个交易日（中期趋势持仓）\n")
        f.write(f"\n## 硬过滤条件\n\n")
        f.write(f"- 收盘价 ≥ {PRICE_MIN} 元\n")
        f.write(f"- 成交额 ≥ {AMOUNT_MIN / 1e6:.0f} 百万元\n")
        f.write(f"- 上市 ≥ {CAL_DAYS_MIN} 自然日\n")
        f.write(f"- 行业样本数 ≥ {INDUSTRY_MIN_MEMBERS}（避免小行业分位失真）\n")
        f.write(f"- 行业内 20/60 日涨幅百分位 ≥ {INDUSTRY_RANK_MIN:.0f}%（行业龙头）\n")
        f.write(f"- MA20/MA60 多头排列（MA20 ≥ MA60）\n")
        f.write(f"- 剔除 ST/*ST，不含科创板\n")
        if filter_stats:
            f.write(f"- 趋势分 ≥ {filter_stats.get('trend_score_min', TREND_SCORE_MIN):.2f}\n")
            f.write(f"- 抱团分 ≥ {filter_stats.get('cluster_score_min', CLUSTER_SCORE_MIN):.2f}\n")
            f.write(f"- 稳定分 ≥ {filter_stats.get('stability_score_min', STABILITY_SCORE_MIN):.2f}\n")
            f.write(f"- 最终名单单行业 ≤ {int(filter_stats.get('max_per_industry', MAX_PER_INDUSTRY))} 只\n")
        f.write(f"\n## 综合打分公式\n\n")
        f.write(
            "```\ncomposite_score = 0.30 × 行业领先得分（sector_leadership_score）\n"
            "                + 0.25 × 趋势质量得分（trend_score）\n"
            "                + 0.25 × 抱团强度得分（cluster_score）\n"
            "                + 0.20 × 稳定性得分（stability_score）\n```\n"
        )
        f.write(f"\n## 输出文件\n\n")
        f.write(f"- `docs/list/{OUTPUT_STEM}_passed.csv/md`\n")
        f.write(f"- `docs/list/{OUTPUT_STEM}_top5.csv/md`\n")
        f.write(f"- `docs/list/{OUTPUT_STEM}_top20.csv/md`\n")

    if not copy_history:
        return

    # 历史快照
    snap_dir = OUTPUT_DIR / "history" / OUTPUT_STEM / trade_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    for stem_suffix, is_md in [("top5", False), ("top20", False), ("passed", False), ("summary", True)]:
        ext = "md" if is_md else "csv"
        if stem_suffix == "summary":
            ext = "md"
        src = OUTPUT_DIR / f"{OUTPUT_STEM}_{stem_suffix}.{'md' if stem_suffix == 'summary' else 'csv'}"
        dst = snap_dir / src.name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    # 同时保存 top5.md 快照
    for md_stem in ["top5", "top20", "passed"]:
        src = OUTPUT_DIR / f"{OUTPUT_STEM}_{md_stem}.md"
        dst = snap_dir / src.name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


def _write_empty_outputs(run_ts: datetime, trade_date: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        "rank,code,name,industry,score_100,score_raw,"
        "leadership_score,trend_score,cluster_score,stability_score,liquidity_score,"
        "sector_rank_20,sector_rank_60,ret_20,ret_60,"
        "ma_bull_20_60,ma_alignment_20,trend_slope_60,trend_slope_20,"
        "trend_eff_20,trend_eff_60,amount_ratio_5_20,amount_ratio_20_60,"
        "money_flow_bias_20,money_flow_bias_60,pv_sync_20,"
        "vol_20,vol_60,max_drawdown_20,max_drawdown_60,"
        "chip_tightness,win_rate_20,win_rate_60,avg_amount_20,close,trade_date\n"
    )
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
        f.write(f"- 最终通过标的: 0\n")


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run_leader_screen(
    trade_date: Optional[str] = None,
    run_ts: Optional[datetime] = None,
    persist_outputs: bool = True,
    copy_history: bool = True,
) -> dict:
    run_ts = run_ts or datetime.now()
    print(f"[{MODEL_NAME}] 开始运行: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Step 1：确定基准交易日 & 交易日历 ──────────────────────────────────
    print("[1/4] 获取交易日历 & 三日价格快照...")
    latest_trade_date = trade_date or get_latest_trade_date()
    cal_start = (pd.Timestamp(latest_trade_date) - pd.Timedelta(days=200)).strftime("%Y%m%d")
    trade_dates = fetch_trade_cal_dates(cal_start, latest_trade_date)

    if len(trade_dates) < 65:
        print(f"[错误] 交易日历样本不足（{len(trade_dates)} 个），无法计算 60 日涨幅，退出")
        return

    date_20d_ago = _get_nth_trading_day_back(trade_dates, 20)
    date_60d_ago = _get_nth_trading_day_back(trade_dates, 60)
    print(f"[1/4] 基准日={latest_trade_date}  T-20={date_20d_ago}  T-60={date_60d_ago}")

    # ── Step 2：拉取收盘价 & 行情快照 ─────────────────────────────────────
    close_today = _fetch_close(latest_trade_date, "close_today")
    close_20d   = _fetch_close(date_20d_ago,      "close_20d")
    close_60d   = _fetch_close(date_60d_ago,      "close_60d")

    prices = (
        close_today
        .merge(close_20d, on="ts_code", how="inner")
        .merge(close_60d, on="ts_code", how="inner")
    )
    prices = prices.dropna(subset=["close_today", "close_20d", "close_60d"])
    prices = prices[
        (prices["close_today"] > 0) & (prices["close_20d"] > 0) & (prices["close_60d"] > 0)
    ].copy()
    prices["code"] = prices["ts_code"].map(ts_code_to_code)
    prices = prices[~prices["code"].str.startswith("688")].copy()
    print(f"[1/4] 价格拼接完成，全A（不含科创板）: {len(prices)} 只")

    # ── Step 3：拼接基本面 & 硬过滤 ────────────────────────────────────────
    print("[2/4] 拼接基本面，计算行业内排名，执行硬过滤...")
    basic = fetch_stock_basic()
    if basic.empty:
        print("[错误] stock_basic 无数据，退出")
        return

    quote = fetch_a_no_star_quotes(source="tushare", trade_date=latest_trade_date)
    if quote.empty:
        print("[错误] 无法获取行情快照，退出")
        return

    df = prices.merge(
        basic[["secucode", "code", "name", "industry", "listing_date"]].rename(columns={"name": "basic_name"}),
        left_on="ts_code", right_on="secucode", how="inner", suffixes=("", "_basic"),
    )
    df = df.rename(columns={"code_basic": "_code_basic"})
    df = df.merge(
        quote[["secucode", "deal_amount", "name", "close"]].rename(
            columns={"deal_amount": "amount_today", "name": "quote_name", "close": "close_quote"}
        ),
        on="secucode", how="left",
    )
    df["name"]         = df["basic_name"].fillna(df.get("quote_name", ""))
    df["close"]        = pd.to_numeric(df.get("close_quote", np.nan), errors="coerce")
    df["amount_today"] = pd.to_numeric(df["amount_today"], errors="coerce").fillna(0)
    df["listing_date"] = pd.to_datetime(df["listing_date"], errors="coerce")

    total_universe = len(df)
    industry_min_members = max(
        1,
        int(os.environ.get("LEADER_INDUSTRY_MIN_MEMBERS", str(INDUSTRY_MIN_MEMBERS))),
    )

    # 计算涨幅
    df["ret_20_raw"] = df["close_today"] / df["close_20d"] - 1.0
    df["ret_60_raw"] = df["close_today"] / df["close_60d"] - 1.0

    # 行业内百分位排名（0-100）
    df["industry"] = df["industry"].fillna("未知行业")
    df["industry_member_count"] = df.groupby("industry")["ts_code"].transform("size")
    df["sector_rank_20"] = (
        df.groupby("industry")["ret_20_raw"]
        .rank(pct=True, ascending=True, na_option="bottom") * 100.0
    )
    df["sector_rank_60"] = (
        df.groupby("industry")["ret_60_raw"]
        .rank(pct=True, ascending=True, na_option="bottom") * 100.0
    )

    # 硬过滤
    df["is_st"]             = df["name"].astype(str).str.upper().str.contains("ST", na=False)
    df["calendar_days"]     = (pd.Timestamp(latest_trade_date) - df["listing_date"]).dt.days.fillna(0)
    df["pass_st"]           = ~df["is_st"]
    df["pass_listing"]      = df["calendar_days"] >= CAL_DAYS_MIN
    df["pass_price"]        = df["close"].fillna(0) >= PRICE_MIN
    df["pass_amount"]       = df["amount_today"] >= AMOUNT_MIN
    df["pass_industry_depth"] = df["industry_member_count"] >= industry_min_members
    df["pass_sector_rank"]  = (df["sector_rank_20"] >= INDUSTRY_RANK_MIN) & (df["sector_rank_60"] >= INDUSTRY_RANK_MIN)
    df["hard_pass"]         = (
        df["pass_st"] & df["pass_listing"] & df["pass_price"]
        & df["pass_amount"] & df["pass_industry_depth"] & df["pass_sector_rank"]
    )

    base = df[df["hard_pass"]].copy()
    hard_pass_n = len(base)
    print(
        f"[2/4] 全A={total_universe}  硬过滤后={hard_pass_n}  "
        f"（价格={df['pass_price'].sum()}, 成交额={df['pass_amount'].sum()}, "
        f"行业样本深度={df['pass_industry_depth'].sum()}, 行业排名≥{INDUSTRY_RANK_MIN:.0f}%={df['pass_sector_rank'].sum()}）"
    )

    if base.empty:
        print("[完成] 无符合条件标的，输出空文件")
        if persist_outputs:
            _write_empty_outputs(run_ts, latest_trade_date)
        return {"trade_date": latest_trade_date, "scored": pd.DataFrame(), "merged": df}

    # ── Step 4：K 线特征 & 打分 ────────────────────────────────────────────
    # 按行业领先度预排序，取前 KLINE_CANDIDATE_LIMIT 只拉取 K 线
    base = base.sort_values(
        ["sector_rank_20", "sector_rank_60", "amount_today"],
        ascending=False,
    ).reset_index(drop=True)
    kline_candidate_limit = max(
        1,
        int(os.environ.get("LEADER_KLINE_CANDIDATE_LIMIT", str(KLINE_CANDIDATE_LIMIT))),
    )
    kline_codes = base["code"].head(kline_candidate_limit).tolist()
    print(f"[3/4] 拉取 K 线特征，候选={len(kline_codes)} 只...")

    kf = fetch_leader_kline_features(kline_codes, end_trade_date=latest_trade_date)
    kline_ok_n = int(kf["kline_ok"].sum()) if not kf.empty else 0
    print(f"[3/4] K 线拉取完成，成功={kline_ok_n}/{len(kline_codes)}")

    if kf.empty or kline_ok_n == 0:
        print("[警告] 无有效 K 线数据，使用行情数据降级打分")
        # 降级：只用行情数据打分（sector_rank + amount）
        base = base.copy()
        base["ret_20"]  = base["ret_20_raw"]
        base["ret_60"]  = base["ret_60_raw"]
        # 行业领先得分（纯行情降级）
        base["leadership_score"] = 0.5 * base["sector_rank_20"] / 100.0 + 0.5 * base["sector_rank_60"] / 100.0
        base["trend_score"]      = 0.0
        base["cluster_score"]    = 0.0
        base["stability_score"]  = 0.0
        base["liquidity_score"]  = np.log1p(base["amount_today"].fillna(0))
        base["score"]            = base["leadership_score"] + 0.001 * base["liquidity_score"]
        base["ma_bull_20_60"]    = np.nan
        base = base.sort_values("score", ascending=False).reset_index(drop=True)
        base["rank"] = np.arange(1, len(base) + 1)
        base["score_raw"] = base["score"]
        if len(base) > 1:
            base["score_100"] = (len(base) - base["rank"]) / (len(base) - 1) * 100.0
        else:
            base["score_100"] = 100.0
        scored = base
        filter_stats = None
    else:
        merged = base.merge(kf, on="code", how="left")
        merged["ret_20"] = merged["ret_20"].where(merged["ret_20"].notna(), merged["ret_20_raw"])
        merged["ret_60"] = merged["ret_60"].where(merged["ret_60"].notna(), merged["ret_60_raw"])
        merged.rename(columns={"close_today": "close_today_price"}, inplace=True)
        if "close" not in merged.columns or merged["close"].isna().all():
            merged["close"] = merged.get("close_quote", np.nan)

        # 仅对 K 线成功的标的打分，K 线失败的附低分保留
        valid = merged[merged["kline_ok"].fillna(0).astype(int) == 1].copy()
        # 追加 MA 多头排列硬过滤（需要 K 线数据）
        valid = valid[valid["ma_bull_20_60"].fillna(-1) >= MA_BULL_MIN].copy()
        print(f"[3/4] 通过 MA 多头排列过滤: {len(valid)} 只")

        if valid.empty:
            print("[完成] 无通过 MA 多头排列条件的标的，输出空文件")
            if persist_outputs:
                _write_empty_outputs(run_ts, latest_trade_date)
            return {"trade_date": latest_trade_date, "scored": pd.DataFrame(), "merged": merged}

        print(f"[4/4] 打分排名...")
        scored = score_leaders(valid)
        scored, filter_stats = apply_leader_selection_guards(scored)
        print(
            f"[4/4] 分数地板后={int(filter_stats.get('quality_pass_n', 0))}  "
            f"行业上限后={int(filter_stats.get('industry_cap_pass_n', 0))}"
        )

        if scored.empty:
            print("[完成] 无通过质量地板与行业拥挤约束的标的，输出空文件")
            if persist_outputs:
                _write_empty_outputs(run_ts, latest_trade_date)
            return {"trade_date": latest_trade_date, "scored": pd.DataFrame(), "merged": merged}

    scored["trade_date"] = latest_trade_date
    # 确保 close 列使用正确收盘价
    if "close" not in scored.columns or scored["close"].isna().all():
        scored["close"] = scored.get("close_today", np.nan)

    if persist_outputs:
        _write_outputs(
            scored,
            run_ts,
            latest_trade_date,
            total_universe,
            hard_pass_n,
            kline_ok_n,
            filter_stats=filter_stats,
            copy_history=copy_history,
        )

    print(f"[完成] {run_ts.strftime('%H:%M:%S')}  最终通过={len(scored)}  Top{TOP_N}:")
    for _, row in scored.head(TOP_N).iterrows():
        print(
            f"  #{int(row['rank'])}  {row['code']}  {row.get('name', ''):<8s}"
            f"  领先={row.get('leadership_score', 0):.4f}"
            f"  趋势={row.get('trend_score', 0):.4f}"
            f"  抱团={row.get('cluster_score', 0):.4f}"
            f"  综合={row.get('score_100', 0):.2f}"
        )
    return {"trade_date": latest_trade_date, "scored": scored, "merged": df}


if __name__ == "__main__":
    run_leader_screen()
