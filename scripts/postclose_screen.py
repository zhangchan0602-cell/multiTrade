#!/usr/bin/env python3
"""
全A（不含科创板）短线多因子-盘后版筛选（基于 Tushare 数据）

股票池：
- 沪A主板
- 深A主板
- 创业板

输出文件：
- docs/list/postclose_passed.csv
- docs/list/postclose_passed.md
- docs/list/postclose_top5.csv
- docs/list/postclose_top5.md
- docs/list/postclose_top20.csv
- docs/list/postclose_top20.md
- docs/list/postclose_summary.md
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd

from screen_common import (
    OUTPUT_DIR,
    fetch_a_no_star_quotes,
    fetch_org_info,
    fetch_tushare_kline_frame,
    industry_zscore,
    winsorize,
)


SHORT_TOP_N = 5
DEFAULT_KLINE_CANDIDATE_LIMIT = 0
DEFAULT_KLINE_CANDIDATE_MIN = 600
DEFAULT_KLINE_CANDIDATE_MAX = 900
DEFAULT_KLINE_CANDIDATE_RATIO = 0.20
DEFAULT_MOMENTUM_SCORE_FLOOR = 0.0
DEFAULT_LAUNCH_SCORE_FLOOR = -0.10
DEFAULT_MODEL_NAME = "短线多因子-盘后版"
DEFAULT_OUTPUT_STEM = "postclose"
DEFAULT_TRADE_TARGET_TEXT = "盘后运行，次日择机买入Top5，目标持有后续2-3个交易日"


def pct_change_from(values: np.ndarray, periods: int) -> float:
    if len(values) <= periods or values[-periods - 1] <= 0:
        return np.nan
    return float(values[-1] / values[-periods - 1] - 1.0)


def safe_ratio(numerator: float, denominator: float) -> float:
    if pd.notna(numerator) and pd.notna(denominator) and denominator > 0:
        return float(numerator / denominator)
    return np.nan


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


def get_short_kline_feature(code: str, retries: int = 6) -> Dict:
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (pd.Timestamp(datetime.now()) - pd.Timedelta(days=220)).strftime("%Y%m%d")
    last_err = None
    for i in range(retries):
        try:
            kline = fetch_tushare_kline_frame(code, start_date=start_date, end_date=end_date)
            if kline.empty:
                raise RuntimeError("empty kline")

            opens = pd.to_numeric(kline["open"], errors="coerce").to_numpy(dtype=float)
            closes = pd.to_numeric(kline["close"], errors="coerce").to_numpy(dtype=float)
            highs = pd.to_numeric(kline["high"], errors="coerce").to_numpy(dtype=float)
            lows = pd.to_numeric(kline["low"], errors="coerce").to_numpy(dtype=float)
            volumes = pd.to_numeric(kline["vol"], errors="coerce").to_numpy(dtype=float)
            amounts = pd.to_numeric(kline["amount"], errors="coerce").to_numpy(dtype=float)
            turnovers = pd.to_numeric(kline["turnover"], errors="coerce").to_numpy(dtype=float)

            valid = np.isfinite(opens) & np.isfinite(closes) & np.isfinite(highs) & np.isfinite(lows)
            if valid.sum() < 25:
                raise RuntimeError("insufficient history")

            o = np.array(opens[valid], dtype=float)
            c = np.array(closes[valid], dtype=float)
            h = np.array(highs[valid], dtype=float)
            l = np.array(lows[valid], dtype=float)
            v = np.array(volumes[valid], dtype=float)
            a = np.array(amounts[valid], dtype=float)
            t = np.array(turnovers[valid], dtype=float)
            rets = c[1:] / c[:-1] - 1.0

            ret_3 = pct_change_from(c, 3)
            ret_5 = pct_change_from(c, 5)
            ret_10 = pct_change_from(c, 10)
            ret_20 = pct_change_from(c, 20)

            ma5 = trailing_mean(c, 5)
            ma10 = trailing_mean(c, 10)
            ma20 = trailing_mean(c, 20)
            breakout_20 = safe_ratio(c[-1], ma20) - 1.0 if pd.notna(ma20) else np.nan
            price_vs_ma20 = breakout_20
            ma_alignment_20 = (
                0.6 * (safe_ratio(ma5, ma20) - 1.0) + 0.4 * (safe_ratio(ma10, ma20) - 1.0)
                if pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20)
                else np.nan
            )
            trend_slope_20 = slope_pct(c[-20:]) if len(c) >= 20 else np.nan

            high_20 = float(np.nanmax(h[-20:])) if len(h) >= 20 else np.nan
            low_20 = float(np.nanmin(l[-20:])) if len(l) >= 20 else np.nan
            prev_high_20 = float(np.nanmax(h[-21:-1])) if len(h) >= 21 else high_20
            high_breakout_20 = safe_ratio(c[-1], prev_high_20) - 1.0 if pd.notna(prev_high_20) else np.nan
            close_position_20 = safe_ratio(c[-1] - low_20, high_20 - low_20) if pd.notna(high_20) else np.nan

            avg_amount_1 = float(a[-1]) if len(a) >= 1 else np.nan
            avg_amount_3 = trailing_mean(a, 3)
            avg_amount_5 = float(np.nanmean(a[-5:])) if len(a) >= 5 else np.nan
            avg_amount_20 = float(np.nanmean(a[-20:])) if len(a) >= 20 else np.nan
            amount_ratio_1_20 = safe_ratio(avg_amount_1, avg_amount_20)
            amount_ratio_3_20 = safe_ratio(avg_amount_3, avg_amount_20)
            amount_ratio_5_20 = safe_ratio(avg_amount_5, avg_amount_20)

            turnover_5 = float(np.nanmean(t[-5:])) if len(t) >= 5 else np.nan
            turnover_20 = float(np.nanmean(t[-20:])) if len(t) >= 20 else np.nan
            turnover_accel_5_20 = safe_ratio(turnover_5, turnover_20) - 1.0 if pd.notna(turnover_20) else np.nan
            vol_10 = float(np.std(rets[-10:], ddof=0)) if len(rets) >= 10 else np.nan
            vol_20 = float(np.std(rets[-20:], ddof=0)) if len(rets) >= 20 else np.nan
            downside = rets[-20:][rets[-20:] < 0] if len(rets) >= 20 else np.array([])
            downside_vol_20 = float(np.std(downside, ddof=0)) if len(downside) >= 3 else 0.0
            win_rate_20 = float(np.mean(rets[-20:] > 0)) if len(rets) >= 20 else np.nan

            max_drawdown_20 = np.nan
            if len(c) >= 20:
                trailing = c[-20:]
                running_max = np.maximum.accumulate(trailing)
                drawdowns = trailing / running_max - 1.0
                max_drawdown_20 = float(np.nanmin(drawdowns))

            # 底部收敛度: 前15日(day-20到day-6)日收益波动率，越低说明横盘整理越紧
            vol_base = float(np.std(rets[-20:-5], ddof=0)) if len(rets) >= 20 else np.nan
            daily_range = np.where(l > 0, h / l - 1.0, np.nan)
            range_base = float(np.nanmean(daily_range[-20:-5])) if len(daily_range) >= 20 else np.nan

            # 启动加速度: 近5日涨幅 - 前15日涨幅，正值说明刚启动
            if len(c) >= 21 and pd.notna(ret_5):
                ret_early = float(c[-6] / c[-21] - 1.0)
                accel = float(ret_5 - ret_early)
            else:
                accel = np.nan

            abs_path = float(np.nansum(np.abs(rets[-20:]))) if len(rets) >= 20 else np.nan
            trend_efficiency_20 = safe_ratio(ret_20, abs_path) if pd.notna(abs_path) else np.nan

            day_range = h - l
            close_strength = np.full(len(day_range), np.nan, dtype=float)
            np.divide(c - l, day_range, out=close_strength, where=day_range > 0)
            close_strength_5 = float(np.nanmean(close_strength[-5:])) if len(close_strength) >= 5 else np.nan
            upper_shadow = np.full(len(day_range), np.nan, dtype=float)
            np.divide(h - np.maximum(o, c), day_range, out=upper_shadow, where=day_range > 0)
            upper_shadow_5 = float(np.nanmean(upper_shadow[-5:])) if len(upper_shadow) >= 5 else np.nan

            return {
                "code": code,
                "kline_ok": 1,
                "listed_days_kline": int(len(c)),
                "ret_3": ret_3,
                "ret_5": ret_5,
                "ret_10": ret_10,
                "ret_20": ret_20,
                "breakout_20": breakout_20,
                "high_breakout_20": high_breakout_20,
                "close_position_20": close_position_20,
                "price_vs_ma20": price_vs_ma20,
                "ma_alignment_20": ma_alignment_20,
                "trend_slope_20": trend_slope_20,
                "avg_amount_20": avg_amount_20,
                "amount_ratio_1_20": amount_ratio_1_20,
                "amount_ratio_3_20": amount_ratio_3_20,
                "amount_ratio_5_20": amount_ratio_5_20,
                "turnover_5": turnover_5,
                "turnover_20": turnover_20,
                "turnover_accel_5_20": turnover_accel_5_20,
                "vol_10": vol_10,
                "vol_20": vol_20,
                "downside_vol_20": downside_vol_20,
                "win_rate_20": win_rate_20,
                "max_drawdown_20": max_drawdown_20,
                "vol_base": vol_base,
                "range_base": range_base,
                "accel": accel,
                "trend_efficiency_20": trend_efficiency_20,
                "close_strength_5": close_strength_5,
                "upper_shadow_5": upper_shadow_5,
            }
        except Exception as e:  # pragma: no cover
            last_err = e
            time.sleep(0.8 * (1.5 ** i))

    return {
        "code": code,
        "kline_ok": 0,
        "listed_days_kline": np.nan,
        "ret_3": np.nan,
        "ret_5": np.nan,
        "ret_10": np.nan,
        "ret_20": np.nan,
        "breakout_20": np.nan,
        "high_breakout_20": np.nan,
        "close_position_20": np.nan,
        "price_vs_ma20": np.nan,
        "ma_alignment_20": np.nan,
        "trend_slope_20": np.nan,
        "avg_amount_20": np.nan,
        "amount_ratio_1_20": np.nan,
        "amount_ratio_3_20": np.nan,
        "amount_ratio_5_20": np.nan,
        "turnover_5": np.nan,
        "turnover_20": np.nan,
        "turnover_accel_5_20": np.nan,
        "vol_10": np.nan,
        "vol_20": np.nan,
        "downside_vol_20": np.nan,
        "win_rate_20": np.nan,
        "max_drawdown_20": np.nan,
        "vol_base": np.nan,
        "range_base": np.nan,
        "accel": np.nan,
        "trend_efficiency_20": np.nan,
        "close_strength_5": np.nan,
        "upper_shadow_5": np.nan,
        "kline_err": str(last_err)[:120],
    }


SHORT_KLINE_COLUMNS = [
    "code",
    "kline_ok",
    "listed_days_kline",
    "ret_3",
    "ret_5",
    "ret_10",
    "ret_20",
    "breakout_20",
    "high_breakout_20",
    "close_position_20",
    "price_vs_ma20",
    "ma_alignment_20",
    "trend_slope_20",
    "avg_amount_20",
    "amount_ratio_1_20",
    "amount_ratio_3_20",
    "amount_ratio_5_20",
    "turnover_5",
    "turnover_20",
    "turnover_accel_5_20",
    "vol_10",
    "vol_20",
    "downside_vol_20",
    "win_rate_20",
    "max_drawdown_20",
    "vol_base",
    "range_base",
    "accel",
    "trend_efficiency_20",
    "close_strength_5",
    "upper_shadow_5",
]


def empty_short_kline_features(codes: List[str]) -> pd.DataFrame:
    df = pd.DataFrame({"code": codes})
    for c in SHORT_KLINE_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    df["kline_ok"] = 0
    return df[SHORT_KLINE_COLUMNS]


def fetch_short_kline_features(codes: List[str], max_workers: int = 8, retries: int = 2) -> pd.DataFrame:
    if not codes:
        return empty_short_kline_features([])

    out = []
    total = len(codes)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(get_short_kline_feature, c, retries): c for c in codes}
        for fut in as_completed(futs):
            done += 1
            out.append(fut.result())
            if done % 200 == 0 or done == total:
                ok = sum(1 for r in out if r.get("kline_ok") == 1)
                print(f"[kline-short] {done}/{total}, success={ok}")
    return pd.DataFrame(out)


def add_fast_prefilter_columns(df: pd.DataFrame, as_of) -> pd.DataFrame:
    df = df.copy()
    df["industry"] = df["industry"].fillna("未知行业")
    df["is_st"] = df["name"].astype(str).str.upper().str.contains("ST", na=False)
    calendar_days = (pd.Timestamp(as_of) - pd.to_datetime(df["listing_date"], errors="coerce")).dt.days
    df["calendar_listed_days"] = calendar_days
    calendar_days_floor = df["calendar_listed_days"].fillna(9999)
    # 预筛只用于减少明显不可交易样本，最终入选仍以真实20日K线指标为准。
    df["fast_prefilter_pass"] = (
        (~df["is_st"])
        & (calendar_days_floor >= 45)
        & (pd.to_numeric(df["deal_amount"], errors="coerce").fillna(0) >= 30_000_000)
        & (pd.to_numeric(df["close"], errors="coerce").fillna(0) >= 2.0)
        & (pd.to_numeric(df["turnover"], errors="coerce").fillna(0).between(0.2, 35.0))
    )
    return df


def resolve_kline_candidate_limit(df: pd.DataFrame, requested_limit: int) -> int:
    if requested_limit > 0:
        return min(requested_limit, len(df))

    eligible = int(df["fast_prefilter_pass"].fillna(False).sum())
    if eligible <= 0:
        eligible = len(df)

    dynamic_limit = int(np.ceil(eligible * DEFAULT_KLINE_CANDIDATE_RATIO))
    dynamic_limit = max(DEFAULT_KLINE_CANDIDATE_MIN, dynamic_limit)
    dynamic_limit = min(DEFAULT_KLINE_CANDIDATE_MAX, dynamic_limit, len(df))
    return dynamic_limit


def select_short_kline_candidates(df: pd.DataFrame, limit: int) -> List[str]:
    df = df.copy()
    if limit <= 0:
        limit = len(df)

    change_rate = pd.to_numeric(df["change_rate"], errors="coerce")
    ret_60d = pd.to_numeric(df["ret_60d"], errors="coerce")
    deal_amount = pd.to_numeric(df["deal_amount"], errors="coerce")
    turnover = pd.to_numeric(df["turnover"], errors="coerce")
    amp = pd.to_numeric(df["amp"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")

    trade_prefilter = (
        df["fast_prefilter_pass"].fillna(False)
        & (~df["is_st"].fillna(False))
        & (df["calendar_listed_days"].fillna(9999) >= 90)
        & (deal_amount.fillna(0) >= 100_000_000)
        & (close.fillna(0) >= 3.0)
        & turnover.between(1.0, 20.0)
        & change_rate.between(-4.0, 9.8)
        & amp.le(16.0)
        & ret_60d.between(-20.0, 60.0)
    )

    candidate = df[trade_prefilter].copy()
    if candidate.empty:
        candidate = df[df["fast_prefilter_pass"].fillna(False)].copy()

    if candidate.empty:
        return []

    c_change = pd.to_numeric(candidate["change_rate"], errors="coerce")
    c_ret60 = pd.to_numeric(candidate["ret_60d"], errors="coerce")
    c_deal = pd.to_numeric(candidate["deal_amount"], errors="coerce")
    c_turnover = pd.to_numeric(candidate["turnover"], errors="coerce")
    c_amp = pd.to_numeric(candidate["amp"], errors="coerce")

    change_rank = c_change.clip(-3.0, 7.5).rank(pct=True)
    ret60_rank = c_ret60.clip(-20.0, 60.0).rank(pct=True)
    amount_rank = np.log1p(c_deal.clip(lower=0)).rank(pct=True)
    turnover_rank = c_turnover.clip(1.0, 12.0).rank(pct=True)
    amp_rank = c_amp.clip(0.0, 18.0).rank(pct=True)
    chase_penalty = c_change.gt(7.5).astype(float) * 0.20 + c_amp.gt(14.0).astype(float) * 0.10

    candidate["quote_prefilter_score"] = (
        0.28 * change_rank.fillna(0.0)
        + 0.14 * ret60_rank.fillna(0.0)
        + 0.26 * amount_rank.fillna(0.0)
        + 0.22 * turnover_rank.fillna(0.0)
        - 0.10 * amp_rank.fillna(0.0)
        - chase_penalty
    )
    candidate = candidate.sort_values("quote_prefilter_score", ascending=False)
    return candidate["code"].head(limit).tolist()


def apply_short_kline_fallback(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    needs_fallback = df["fast_prefilter_pass"].fillna(False) & (df["kline_ok"].fillna(0).astype(int) != 1)
    if not needs_fallback.any():
        df["kline_fallback_used"] = False
        return df

    change_1d = (pd.to_numeric(df["change_rate"], errors="coerce") / 100.0).clip(-0.2, 0.2)
    ret_60d = (pd.to_numeric(df["ret_60d"], errors="coerce") / 100.0).clip(-0.8, 1.5)
    amp = (pd.to_numeric(df["amp"], errors="coerce") / 100.0).clip(0.0, 0.3)
    turnover = pd.to_numeric(df["turnover"], errors="coerce")

    fallback_values = {
        "ret_3": (change_1d + ret_60d / 20.0).clip(-0.25, 0.25),
        "ret_5": (change_1d + ret_60d / 12.0).clip(-0.3, 0.3),
        "ret_10": (change_1d + ret_60d / 6.0).clip(-0.4, 0.4),
        "ret_20": (ret_60d / 3.0).clip(-0.5, 0.6),
        "breakout_20": (change_1d + ret_60d / 6.0).clip(-0.4, 0.4),
        "high_breakout_20": (change_1d + ret_60d / 10.0).clip(-0.4, 0.4),
        "close_position_20": (0.5 + ret_60d / 3.0 + change_1d).clip(0.0, 1.0),
        "price_vs_ma20": (change_1d + ret_60d / 6.0).clip(-0.4, 0.4),
        "ma_alignment_20": (ret_60d / 8.0).clip(-0.25, 0.25),
        "trend_slope_20": (ret_60d / 20.0).clip(-0.08, 0.08),
        "avg_amount_20": pd.to_numeric(df["deal_amount"], errors="coerce"),
        "amount_ratio_1_20": (1.0 + change_1d.abs() * 5.0).clip(0.5, 2.5),
        "amount_ratio_3_20": (1.0 + change_1d.abs() * 4.5).clip(0.5, 2.3),
        "amount_ratio_5_20": (1.0 + change_1d.abs() * 4.0).clip(0.6, 2.0),
        "turnover_5": turnover,
        "turnover_20": turnover,
        "turnover_accel_5_20": pd.Series(0.0, index=df.index),
        "vol_10": amp.where(amp > 0, np.nan),
        "vol_20": amp.where(amp > 0, np.nan),
        "downside_vol_20": (amp / 2.0).where(amp > 0, np.nan),
        "win_rate_20": (0.5 + ret_60d / 4.0).clip(0.0, 1.0),
        "max_drawdown_20": -amp,
        "vol_base": amp.where(amp > 0, np.nan),
        "range_base": amp.where(amp > 0, np.nan),
        "accel": (change_1d + ret_60d / 12.0 - ret_60d / 3.0).clip(-0.5, 0.5),
        "trend_efficiency_20": (ret_60d / (ret_60d.abs() + amp + 1e-9)).clip(-1.0, 1.0),
        "close_strength_5": (0.5 + change_1d * 2.0).clip(0.0, 1.0),
        "upper_shadow_5": (amp - change_1d.clip(lower=0.0)).clip(0.0, 0.3),
    }
    for col, values in fallback_values.items():
        df.loc[needs_fallback & df[col].isna(), col] = values.loc[needs_fallback & df[col].isna()]

    df["kline_fallback_used"] = needs_fallback
    return df


def score_factors(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["lowvol_10_raw"] = -df["vol_10"]
    df["lowvol_20_raw"] = -df["vol_20"]
    df["low_downside_vol_20_raw"] = -df["downside_vol_20"]
    df["drawdown_20_raw"] = df["max_drawdown_20"]
    df["low_vol_base_raw"] = -df["vol_base"]  # 底部收敛度（越低越好）
    df["low_range_base_raw"] = -df["range_base"]
    df["accel_raw"] = df["accel"]  # 启动加速度
    df["upper_shadow_5_raw"] = -df["upper_shadow_5"]

    raw_factor_cols = [
        "ret_3",
        "ret_5",
        "ret_10",
        "ret_20",
        "breakout_20",
        "high_breakout_20",
        "close_position_20",
        "price_vs_ma20",
        "ma_alignment_20",
        "trend_slope_20",
        "amount_ratio_1_20",
        "amount_ratio_3_20",
        "amount_ratio_5_20",
        "turnover_5",
        "turnover_accel_5_20",
        "lowvol_10_raw",
        "lowvol_20_raw",
        "low_downside_vol_20_raw",
        "drawdown_20_raw",
        "win_rate_20",
        "avg_amount_20_used",
        "turnover_20_used",
        "low_vol_base_raw",
        "low_range_base_raw",
        "accel_raw",
        "trend_efficiency_20",
        "close_strength_5",
        "upper_shadow_5_raw",
    ]

    for c in raw_factor_cols:
        df[c] = winsorize(pd.to_numeric(df[c], errors="coerce"), p=0.025)
        zc = f"{c}_z"
        df[zc] = industry_zscore(df[c], df["industry"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    df["launch_score"] = (
        0.18 * df["ret_3_z"]
        + 0.20 * df["ret_5_z"]
        + 0.18 * df["accel_raw_z"]
        + 0.16 * df["high_breakout_20_z"]
        + 0.13 * df["close_position_20_z"]
        + 0.10 * df["price_vs_ma20_z"]
        + 0.05 * df["close_strength_5_z"]
    )
    df["trend_score"] = (
        0.18 * df["ret_10_z"]
        + 0.14 * df["ret_20_z"]
        + 0.18 * df["breakout_20_z"]
        + 0.20 * df["ma_alignment_20_z"]
        + 0.18 * df["trend_slope_20_z"]
        + 0.12 * df["trend_efficiency_20_z"]
    )
    df["momentum_score"] = 0.58 * df["launch_score"] + 0.42 * df["trend_score"]
    df["activity_score"] = (
        0.24 * df["amount_ratio_1_20_z"]
        + 0.31 * df["amount_ratio_3_20_z"]
        + 0.20 * df["amount_ratio_5_20_z"]
        + 0.15 * df["turnover_5_z"]
        + 0.10 * df["turnover_accel_5_20_z"]
    )
    df["stability_score"] = (
        0.16 * df["lowvol_10_raw_z"]
        + 0.12 * df["lowvol_20_raw_z"]
        + 0.12 * df["low_downside_vol_20_raw_z"]
        + 0.18 * df["drawdown_20_raw_z"]
        + 0.14 * df["win_rate_20_z"]
        + 0.16 * df["low_vol_base_raw_z"]
        + 0.08 * df["low_range_base_raw_z"]
        + 0.04 * df["upper_shadow_5_raw_z"]
    )
    df["liquidity_score"] = 0.55 * df["avg_amount_20_used_z"] + 0.45 * df["turnover_20_used_z"]

    df["score"] = (
        0.42 * df["launch_score"]
        + 0.18 * df["trend_score"]
        + 0.24 * df["activity_score"]
        + 0.10 * df["stability_score"]
        + 0.06 * df["liquidity_score"]
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


def add_next_2_3d_trade_filters(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    change_rate = pd.to_numeric(df["change_rate"], errors="coerce")
    amp = pd.to_numeric(df["amp"], errors="coerce")

    df["pass_daily_chase"] = change_rate.between(-4.0, 9.8) & (amp.fillna(99.0) <= 16.0)
    df["pass_launch_window"] = (
        pd.to_numeric(df["ret_3"], errors="coerce").between(-0.02, 0.12)
        & pd.to_numeric(df["ret_5"], errors="coerce").between(-0.01, 0.18)
        & pd.to_numeric(df["ret_20"], errors="coerce").between(-0.08, 0.35)
        & pd.to_numeric(df["accel"], errors="coerce").ge(-0.05)
    )
    df["pass_breakout_setup"] = (
        pd.to_numeric(df["close_position_20"], errors="coerce").between(0.55, 1.05)
        & pd.to_numeric(df["high_breakout_20"], errors="coerce").between(-0.04, 0.12)
        & pd.to_numeric(df["price_vs_ma20"], errors="coerce").between(-0.02, 0.18)
    )
    df["pass_activity_setup"] = (
        pd.to_numeric(df["amount_ratio_3_20"], errors="coerce").between(1.05, 3.50)
        & pd.to_numeric(df["amount_ratio_5_20"], errors="coerce").between(0.95, 3.00)
        & pd.to_numeric(df["turnover_5"], errors="coerce").between(1.50, 18.00)
    )
    df["pass_risk_setup"] = (
        pd.to_numeric(df["vol_20"], errors="coerce").le(0.08)
        & pd.to_numeric(df["max_drawdown_20"], errors="coerce").ge(-0.18)
        & pd.to_numeric(df["upper_shadow_5"], errors="coerce").le(0.42)
    )
    df["pass_next_2_3d_setup"] = (
        df["pass_daily_chase"]
        & df["pass_launch_window"]
        & df["pass_breakout_setup"]
        & df["pass_activity_setup"]
        & df["pass_risk_setup"]
    )
    return df


def score_quote_only_candidates(df: pd.DataFrame, limit: int = 50) -> pd.DataFrame:
    df = df.copy()
    change_rate = pd.to_numeric(df["change_rate"], errors="coerce")
    ret_60d = pd.to_numeric(df["ret_60d"], errors="coerce")
    deal_amount = pd.to_numeric(df["deal_amount"], errors="coerce")
    turnover = pd.to_numeric(df["turnover"], errors="coerce")
    amp = pd.to_numeric(df["amp"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")

    quote_pass = (
        df["fast_prefilter_pass"].fillna(False)
        & (~df["is_st"].fillna(False))
        & (df["calendar_listed_days"].fillna(9999) >= 90)
        & (deal_amount.fillna(0) >= 100_000_000)
        & (close.fillna(0) >= 3.0)
        & turnover.between(1.2, 18.0)
        & change_rate.between(-3.5, 8.8)
        & amp.le(15.0)
        & ret_60d.between(-25.0, 100.0)
    )
    out = df[quote_pass].copy()
    if out.empty:
        return out

    q_change = pd.to_numeric(out["change_rate"], errors="coerce")
    q_ret60 = pd.to_numeric(out["ret_60d"], errors="coerce")
    q_deal = pd.to_numeric(out["deal_amount"], errors="coerce")
    q_turnover = pd.to_numeric(out["turnover"], errors="coerce")
    q_amp = pd.to_numeric(out["amp"], errors="coerce")
    industry = out["industry"].fillna("未知行业")

    change_z = industry_zscore(winsorize(q_change.clip(-3.0, 7.5), p=0.025), industry).fillna(0.0)
    trend_z = industry_zscore(winsorize(q_ret60.clip(-20.0, 80.0), p=0.025), industry).fillna(0.0)
    amount_z = industry_zscore(winsorize(np.log1p(q_deal.clip(lower=0)), p=0.025), industry).fillna(0.0)
    turnover_z = industry_zscore(winsorize(q_turnover.clip(1.0, 12.0), p=0.025), industry).fillna(0.0)
    low_amp_z = industry_zscore(winsorize(-q_amp.clip(0.0, 18.0), p=0.025), industry).fillna(0.0)
    chase_penalty = q_change.gt(7.5).astype(float) * 0.35 + q_amp.gt(13.0).astype(float) * 0.20

    out["launch_score"] = 0.72 * change_z + 0.28 * trend_z
    out["trend_score"] = 0.80 * trend_z + 0.20 * change_z
    out["activity_score"] = 0.48 * amount_z + 0.52 * turnover_z
    out["stability_score"] = low_amp_z
    out["liquidity_score"] = amount_z
    out["momentum_score"] = 0.58 * out["launch_score"] + 0.42 * out["trend_score"]
    out["score"] = (
        0.42 * out["launch_score"]
        + 0.18 * out["trend_score"]
        + 0.24 * out["activity_score"]
        + 0.10 * out["stability_score"]
        + 0.06 * out["liquidity_score"]
        - chase_penalty
    )
    out["score_raw"] = out["score"]
    out["quote_only_fallback_used"] = True
    out["kline_fallback_used"] = False
    out["pass_momentum_floor"] = True
    out["pass_next_2_3d_setup"] = True
    out = out.sort_values("score", ascending=False).head(limit).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    if len(out) > 1:
        out["score_100"] = (len(out) - out["rank"]) / (len(out) - 1) * 100.0
    else:
        out["score_100"] = 100.0
    return out


def write_rank_table(f, rows: pd.DataFrame, title: str, run_ts: datetime) -> None:
    f.write(f"# {title}\n\n")
    f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
    if rows.get("quote_only_fallback_used", pd.Series(False, index=rows.index)).fillna(False).any():
        f.write("- 数据状态: 历史K线不可用，当前为纯行情降级候选，置信度低于真实K线模型\n")
    f.write("| 排名 | 代码 | 名称 | 百分制得分 | 原始分 | 启动 | 趋势 | 动量 | 活跃 | 稳定 | 流动 |\n")
    f.write("|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for _, r in rows.iterrows():
        f.write(
            (
                "| {rank} | {code} | {name} | {score100:.2f} | {score_raw:.4f} | "
                "{launch:.4f} | {trend:.4f} | {momentum:.4f} | {activity:.4f} | "
                "{stability:.4f} | {liquidity:.4f} |\n"
            ).format(
                rank=int(r["rank"]),
                code=r["code"],
                name=r["name"],
                score100=float(r["score_100"]),
                score_raw=float(r["score_raw"]),
                launch=float(r["launch_score"]),
                trend=float(r["trend_score"]),
                momentum=float(r["momentum_score"]),
                activity=float(r["activity_score"]),
                stability=float(r["stability_score"]),
                liquidity=float(r["liquidity_score"]),
            )
        )


def build_output_path(output_stem: str, suffix: str):
    return OUTPUT_DIR / f"{output_stem}_{suffix}"


def write_outputs(
    scored: pd.DataFrame,
    merged: pd.DataFrame,
    run_ts: datetime,
    model_name: str = DEFAULT_MODEL_NAME,
    output_stem: str = DEFAULT_OUTPUT_STEM,
    trade_target_text: str = DEFAULT_TRADE_TARGET_TEXT,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scored = scored.copy()
    merged = merged.copy()
    if "kline_fallback_used" not in scored.columns:
        scored["kline_fallback_used"] = False
    if "quote_only_fallback_used" not in scored.columns:
        scored["quote_only_fallback_used"] = False
    if "pass_momentum_floor" not in scored.columns:
        scored["pass_momentum_floor"] = True

    export_cols = [
        "rank",
        "code",
        "name",
        "industry",
        "score_100",
        "score_raw",
        "launch_score",
        "trend_score",
        "momentum_score",
        "activity_score",
        "stability_score",
        "liquidity_score",
        "ret_3",
        "ret_5",
        "ret_10",
        "ret_20",
        "breakout_20",
        "high_breakout_20",
        "close_position_20",
        "price_vs_ma20",
        "ma_alignment_20",
        "trend_slope_20",
        "amount_ratio_1_20",
        "amount_ratio_3_20",
        "amount_ratio_5_20",
        "turnover_5",
        "turnover_20_used",
        "turnover_accel_5_20",
        "vol_10",
        "vol_20",
        "downside_vol_20",
        "win_rate_20",
        "max_drawdown_20",
        "vol_base",
        "range_base",
        "accel",
        "trend_efficiency_20",
        "close_strength_5",
        "upper_shadow_5",
        "avg_amount_20_used",
        "close",
        "trade_date",
        "kline_fallback_used",
        "quote_only_fallback_used",
        "pass_momentum_floor",
        "pass_next_2_3d_setup",
    ]
    scored[export_cols].to_csv(build_output_path(output_stem, "passed.csv"), index=False, encoding="utf-8-sig")

    with build_output_path(output_stem, "passed.md").open("w", encoding="utf-8") as f:
        f.write(f"# 全A（不含科创板）{model_name}模型 符合清单\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 共 {len(scored)} 只\n\n")
        f.write("| 排名 | 代码 | 名称 | 行业 | 百分制得分 | 原始分 |\n")
        f.write("|---:|---:|---|---|---:|---:|\n")
        for _, r in scored.iterrows():
            f.write(
                f"| {int(r['rank'])} | {r['code']} | {r['name']} | {r['industry']} | {float(r['score_100']):.2f} | {float(r['score_raw']):.4f} |\n"
            )

    top5 = scored.head(SHORT_TOP_N).copy()
    top5[export_cols].to_csv(build_output_path(output_stem, "top5.csv"), index=False, encoding="utf-8-sig")

    with build_output_path(output_stem, "top5.md").open("w", encoding="utf-8") as f:
        write_rank_table(f, top5, f"全A（不含科创板）{model_name}模型 Top 5", run_ts)

    top20 = scored.head(20).copy()
    top20[export_cols].to_csv(build_output_path(output_stem, "top20.csv"), index=False, encoding="utf-8-sig")

    with build_output_path(output_stem, "top20.md").open("w", encoding="utf-8") as f:
        write_rank_table(f, top20, f"全A（不含科创板）{model_name}模型 Top 20", run_ts)

    kline_ok = int((merged["kline_ok"] == 1).sum())
    kline_candidates = int(merged.get("kline_requested", pd.Series(False, index=merged.index)).fillna(False).sum())
    kline_fallback_used = int(merged.get("kline_fallback_used", pd.Series(False, index=merged.index)).fillna(False).sum())
    scored_real_kline = int((scored["kline_ok"] == 1).sum()) if "kline_ok" in scored.columns else 0
    scored_fallback = int(scored.get("kline_fallback_used", pd.Series(False, index=scored.index)).fillna(False).sum())
    scored_quote_only = int(scored.get("quote_only_fallback_used", pd.Series(False, index=scored.index)).fillna(False).sum())
    hard_pass = merged.get("hard_pass", pd.Series(False, index=merged.index)).fillna(False)
    setup_pass = int((hard_pass & merged.get("pass_next_2_3d_setup", pd.Series(False, index=merged.index)).fillna(False)).sum())
    with build_output_path(output_stem, "summary.md").open("w", encoding="utf-8") as f:
        f.write(f"# 全A（不含科创板）{model_name}筛选统计\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 全A（沪主板+深主板+创业板）初始样本: {len(merged)}\n")
        f.write(f"- K线候选样本: {kline_candidates}\n")
        f.write(f"- K线成功样本: {kline_ok}\n")
        f.write(f"- K线代理兜底样本: {kline_fallback_used}\n")
        f.write(f"- 硬过滤后样本: {int(hard_pass.sum())}\n")
        f.write(f"- 次日2-3天交易形态样本: {setup_pass}\n")
        f.write(f"- 最终符合样本: {len(scored)}\n\n")
        f.write(f"- 最终真实K线样本: {scored_real_kline}\n")
        f.write(f"- 最终兜底样本: {scored_fallback}\n\n")
        f.write(f"- 最终纯行情降级样本: {scored_quote_only}\n\n")
        f.write("## 输出文件\n\n")
        f.write(f"- `docs/list/{output_stem}_passed.csv`\n")
        f.write(f"- `docs/list/{output_stem}_passed.md`\n")
        f.write(f"- `docs/list/{output_stem}_top5.csv`\n")
        f.write(f"- `docs/list/{output_stem}_top5.md`\n")
        f.write(f"- `docs/list/{output_stem}_top20.csv`\n")
        f.write(f"- `docs/list/{output_stem}_top20.md`\n")
        f.write(f"- `docs/list/{output_stem}_summary.md`\n\n")
        f.write("## 口径说明\n\n")
        f.write("- 股票池: 沪A主板 + 深A主板 + 创业板（不含科创板）\n")
        f.write("- ST过滤: 名称含 `ST` 或 `*ST` 剔除\n")
        f.write("- 上市天数: 优先用K线交易日，缺失时按上市日自然日近似\n")
        f.write("- 执行流动性: 20日均成交额优先，缺失时回退到当日成交额\n")
        f.write("- 启动: 启动加速度（最高权重）、3日/5日收益、20日新高突破、20日区间收盘位置、站上20日均线、近5日收盘强度\n")
        f.write("- 趋势: 10日/20日收益、20日均线突破、均线排列、20日斜率、趋势效率\n")
        f.write("- 活跃度: 1日/3日/5日对20日成交额放大比、5日换手率、换手加速度\n")
        f.write("- 稳定性: 底部低波动/振幅收敛（最高权重）、10/20日波动率、下行波动、20日最大回撤、20日上涨胜率、近5日上影线\n")
        f.write("- 选股流程: 先做硬过滤并在可交易样本内打分，再叠加次日2-3天交易过滤与动量地板\n")
        f.write("- 启动检测: 底部横盘低波动收敛 + 量价同步放大 + 刚突破均线（20日涨幅≤20%、5日涨幅≤12%）+ 回撤受控\n")
        f.write(f"- 交易目标: {trade_target_text}\n")
        f.write("- 次日交易过滤: 过滤当日追高、过热、量能过弱、波动过大、上影压力过重样本\n")
        if kline_fallback_used:
            f.write("- 快速兜底: 已启用代理因子；实盘交易建议优先使用真实K线样本\n")
        elif scored_quote_only:
            f.write("- 纯行情降级: 当前历史K线不可用，Top5由收盘涨跌幅、60日涨幅、成交额、换手率、振幅生成，仅作低置信候选\n")
        else:
            f.write("- K线口径: 默认不使用代理兜底，真实K线缺失时不进入最终Top5\n")
        f.write("- 百分制得分: 按原始综合分在全样本中的线性排名换算到0-100\n")


def run_screen(
    model_name: str = DEFAULT_MODEL_NAME,
    output_stem: str = DEFAULT_OUTPUT_STEM,
    trade_target_text: str = DEFAULT_TRADE_TARGET_TEXT,
) -> None:
    run_ts = datetime.now()
    as_of = run_ts.date()
    kline_workers = max(1, int(os.environ.get("SHORT_KLINE_WORKERS", "6")))
    kline_retries = max(1, int(os.environ.get("SHORT_KLINE_RETRIES", "2")))
    kline_candidate_limit = max(0, int(os.environ.get("SHORT_KLINE_CANDIDATE_LIMIT", str(DEFAULT_KLINE_CANDIDATE_LIMIT))))
    momentum_score_floor = float(
        os.environ.get("SHORT_MOMENTUM_SCORE_FLOOR", str(DEFAULT_MOMENTUM_SCORE_FLOOR))
    )
    launch_score_floor = float(os.environ.get("SHORT_LAUNCH_SCORE_FLOOR", str(DEFAULT_LAUNCH_SCORE_FLOOR)))
    fast_prefilter = os.environ.get("SHORT_FAST_PREFILTER", "1") != "0"
    skip_kline = os.environ.get("SHORT_SKIP_KLINE", "0") == "1"
    kline_fallback = skip_kline or os.environ.get("SHORT_KLINE_FALLBACK", "0") == "1"
    require_real_kline = os.environ.get("SHORT_REQUIRE_REAL_KLINE", "1") != "0"
    quote_only_fallback = os.environ.get("SHORT_QUOTE_ONLY_FALLBACK", "1") != "0"
    quote_only_limit = max(SHORT_TOP_N, int(os.environ.get("SHORT_QUOTE_ONLY_LIMIT", "50")))

    print("[1/3] fetch A-share quote universe (no STAR)...")
    quote = fetch_a_no_star_quotes()
    print(f"[1/3] quote rows={len(quote)}")

    print("[2/3] fetch org info + short kline features...")
    print(
        f"[2/3] short kline config: workers={kline_workers}, retries={kline_retries}, "
        f"fast_prefilter={int(fast_prefilter)}, skip_kline={int(skip_kline)}, "
        f"kline_fallback={int(kline_fallback)}, require_real_kline={int(require_real_kline)}, "
        f"requested_candidate_limit={kline_candidate_limit}, quote_only_fallback={int(quote_only_fallback)}"
    )
    org = fetch_org_info(quote["secucode"].unique().tolist())

    pre = quote.merge(org[["secucode", "listing_date", "industry"]], on="secucode", how="left")
    pre = add_fast_prefilter_columns(pre, as_of)
    kline_candidate_limit = resolve_kline_candidate_limit(pre, kline_candidate_limit)
    print(f"[2/3] resolved kline candidate limit={kline_candidate_limit}")
    if fast_prefilter:
        kline_codes = select_short_kline_candidates(pre, kline_candidate_limit)
    else:
        kline_codes = quote["code"].head(kline_candidate_limit or len(quote)).tolist()
    pre["kline_requested"] = pre["code"].isin(kline_codes)
    print(f"[2/3] kline candidate rows={len(kline_codes)} / {len(quote)}")
    if skip_kline:
        kf = empty_short_kline_features(kline_codes)
    else:
        kf = fetch_short_kline_features(kline_codes, max_workers=kline_workers, retries=kline_retries)
    print(f"[2/3] org rows={len(org)}, short kline rows={len(kf)}")

    print("[3/3] merge, filter, score...")
    df = pre
    df = df.merge(kf, on="code", how="left")
    if kline_fallback:
        df = apply_short_kline_fallback(df)

    calendar_days = df["calendar_listed_days"].fillna(9999)
    df["pass_listing"] = np.where(df["listed_days_kline"].notna(), df["listed_days_kline"] >= 60, calendar_days >= 90)

    df["avg_amount_20_used"] = df["avg_amount_20"].where(df["avg_amount_20"].notna(), df["deal_amount"])
    df["turnover_20_used"] = df["turnover_20"].where(df["turnover_20"].notna(), df["turnover"])

    df["pass_liquidity"] = df["avg_amount_20_used"].fillna(0) >= 100_000_000
    df["pass_price"] = pd.to_numeric(df["close"], errors="coerce").fillna(0) >= 3.0
    df["pass_turnover"] = df["turnover_20_used"].fillna(0).between(1.0, 20.0)

    df["hard_pass"] = (
        (~df["is_st"]) & df["pass_listing"] & df["pass_liquidity"] & df["pass_price"] & df["pass_turnover"]
    )
    if fast_prefilter:
        df["hard_pass"] = df["hard_pass"] & df["fast_prefilter_pass"]
    if require_real_kline:
        df["hard_pass"] = df["hard_pass"] & (df["kline_ok"].fillna(0).astype(int) == 1)

    df = add_next_2_3d_trade_filters(df)

    base = df[df["hard_pass"]].copy()
    raw_cols = [
        "ret_3",
        "ret_5",
        "ret_10",
        "ret_20",
        "breakout_20",
        "high_breakout_20",
        "close_position_20",
        "ma_alignment_20",
        "trend_slope_20",
        "amount_ratio_1_20",
        "amount_ratio_3_20",
        "amount_ratio_5_20",
        "turnover_5",
        "turnover_20_used",
        "turnover_accel_5_20",
        "vol_10",
        "vol_20",
        "downside_vol_20",
        "win_rate_20",
        "max_drawdown_20",
        "vol_base",
        "range_base",
        "accel",
        "trend_efficiency_20",
        "close_strength_5",
        "upper_shadow_5",
        "avg_amount_20_used",
    ]
    base["raw_missing_count"] = base[raw_cols].isna().sum(axis=1)
    base = base[base["raw_missing_count"] <= 4].copy()

    scored = score_factors(base)
    if not scored.empty:
        scored["pass_momentum_floor"] = (
            (scored["momentum_score"] > momentum_score_floor)
            & (scored["launch_score"] > launch_score_floor)
        )
        scored["pass_next_2_3d_setup"] = scored["pass_next_2_3d_setup"].fillna(False)
        scored = scored[scored["pass_next_2_3d_setup"] & scored["pass_momentum_floor"]].copy()

    if scored.empty and quote_only_fallback:
        print("[3/3] no real-kline final candidates, use quote-only fallback candidates")
        quote_scored = score_quote_only_candidates(df, limit=quote_only_limit)
        if not quote_scored.empty:
            scored = quote_scored
    write_outputs(scored, df, run_ts, model_name=model_name, output_stem=output_stem, trade_target_text=trade_target_text)

    print("done")
    print(f"universe_total={len(df)}")
    print(f"hard_pass={int(df['hard_pass'].sum())}")
    print(f"final_passed={len(scored)}")


def main() -> None:
    run_screen()


if __name__ == "__main__":
    main()
