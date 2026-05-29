#!/usr/bin/env python3
"""
全A（不含科创板）短线多因子-盘后版筛选（基于 Tushare 数据）

股票池：
- 沪A主板
- 深A主板
- 创业板

输出文件：
- docs/list/history/short/YYYY-MM-DD/short_passed.csv
- docs/list/history/short/YYYY-MM-DD/short_passed.md
- docs/list/history/short/YYYY-MM-DD/short_top5.csv
- docs/list/history/short/YYYY-MM-DD/short_top5.md
- docs/list/history/short/YYYY-MM-DD/short_top20.csv
- docs/list/history/short/YYYY-MM-DD/short_top20.md
- docs/list/history/short/YYYY-MM-DD/short_summary.md
- docs/list/history/short/YYYY-MM-DD/runs/HHMM/short_top5.md
- docs/list/history/short/YYYY-MM-DD/runs/HHMM/short_top20.csv
- docs/list/history/short/YYYY-MM-DD/runs/HHMM/short_summary.md
- docs/list/history/tail/YYYY-MM-DD/tail_passed.csv
- docs/list/history/tail/YYYY-MM-DD/tail_passed.md
- docs/list/history/tail/YYYY-MM-DD/tail_top5.csv
- docs/list/history/tail/YYYY-MM-DD/tail_top5.md
- docs/list/history/tail/YYYY-MM-DD/tail_top20.csv
- docs/list/history/tail/YYYY-MM-DD/tail_top20.md
- docs/list/history/tail/YYYY-MM-DD/tail_summary.md
- docs/list/history/tail/YYYY-MM-DD/runs/HHMM/tail_top5.md
- docs/list/history/tail/YYYY-MM-DD/runs/HHMM/tail_top20.csv
- docs/list/history/tail/YYYY-MM-DD/runs/HHMM/tail_summary.md
"""

import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from buylist_settlement import SETTLEMENT_SUMMARY_PREFIX, auto_settle_due_buylists
from screen_common import (
    OUTPUT_DIR,
    fetch_a_no_star_quotes,
    fetch_kline_frame,
    fetch_org_info,
    fetch_tushare_kline_frame,
    industry_zscore,
    winsorize,
)

# ── 权重配置（config.toml）加载 ─────────────────────────────────────────────
try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # pip install tomli
    except ImportError:
        tomllib = None  # type: ignore

_CONFIG_FILE = Path(__file__).resolve().parent / "config.toml"


def _load_screen_config() -> dict:
    if tomllib is None or not _CONFIG_FILE.exists():
        return {}
    try:
        with open(_CONFIG_FILE, "rb") as _f:
            return tomllib.load(_f)
    except Exception as _e:
        print(f"[config] 加载 config.toml 失败，使用内置默认值: {_e}")
        return {}


_SCREEN_CFG = _load_screen_config()


SHORT_TOP_N = 5
DEFAULT_KLINE_CANDIDATE_LIMIT = 1200
DEFAULT_KLINE_CANDIDATE_MIN = 600
DEFAULT_KLINE_CANDIDATE_MAX = 900
DEFAULT_KLINE_CANDIDATE_RATIO = 0.20
DEFAULT_MOMENTUM_SCORE_FLOOR = 0.0
DEFAULT_LAUNCH_SCORE_FLOOR = -0.10
DEFAULT_MODEL_NAME = "策略-多因子盘后"
DEFAULT_OUTPUT_STEM = "short"
DEFAULT_TRADE_TARGET_TEXT = "盘后运行，次日择机买入Top5，目标持有后续2-3个交易日"

# 尾盘版专用常量（14:30-14:45 运行，面向当日 14:50 操作）
DEFAULT_TAIL_MOMENTUM_SCORE_FLOOR = 0.05
DEFAULT_TAIL_LAUNCH_SCORE_FLOOR = 0.02
DEFAULT_TAIL_MODEL_NAME = "策略-多因子尾盘"
DEFAULT_TAIL_OUTPUT_STEM = "tail"
DEFAULT_TAIL_TRADE_TARGET_TEXT = "尾盘14:30-14:45运行，当日14:50前后买入，参考持有至次日"


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


def series_corr(x: np.ndarray, y: np.ndarray, min_obs: int = 5) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    if valid.sum() < min_obs:
        return np.nan
    x = x[valid]
    y = y[valid]
    if np.nanstd(x) <= 1e-12 or np.nanstd(y) <= 1e-12:
        return np.nan
    return float(np.corrcoef(x, y)[0, 1])


def get_short_kline_feature(
    code: str,
    retries: int = 6,
    kline_source: str = "auto",
    end_trade_date: Optional[str] = None,
) -> Dict:
    anchor = pd.to_datetime(end_trade_date, format="%Y%m%d", errors="coerce") if end_trade_date else pd.Timestamp.now()
    if pd.isna(anchor):
        anchor = pd.Timestamp.now()
    end_date = anchor.strftime("%Y%m%d")
    start_date = (anchor - pd.Timedelta(days=220)).strftime("%Y%m%d")
    last_err = None
    for i in range(retries):
        try:
            kline = fetch_kline_frame(code, start_date=start_date, end_date=end_date, source=kline_source)
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
            ma60 = trailing_mean(c, 60)
            breakout_20 = safe_ratio(c[-1], ma20) - 1.0 if pd.notna(ma20) else np.nan
            # price_vs_ma20: MA5/MA20 相对强度，量化短期均线对中期均线的偏离
            # 与 breakout_20（收盘价/MA20）使用不同来源，避免因子重复计数
            price_vs_ma20 = safe_ratio(ma5, ma20) - 1.0 if (pd.notna(ma5) and pd.notna(ma20)) else np.nan
            ma_alignment_20 = (
                0.6 * (safe_ratio(ma5, ma20) - 1.0) + 0.4 * (safe_ratio(ma10, ma20) - 1.0)
                if pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20)
                else np.nan
            )
            ma_bull_20_60 = safe_ratio(ma20, ma60) - 1.0 if (pd.notna(ma20) and pd.notna(ma60)) else np.nan
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

            flow_rets_20 = rets[-20:] if len(rets) >= 20 else np.array([])
            flow_amounts_20 = a[1:][-20:] if len(a) >= 21 else np.array([])
            if len(flow_rets_20) == len(flow_amounts_20) and len(flow_rets_20) > 0:
                up_amount = float(np.nansum(flow_amounts_20[flow_rets_20 > 0]))
                down_amount = float(np.nansum(flow_amounts_20[flow_rets_20 < 0]))
                money_flow_bias_20 = safe_ratio(up_amount - down_amount, up_amount + down_amount)
            else:
                money_flow_bias_20 = np.nan

            amount_rets = a[1:] / a[:-1] - 1.0 if len(a) >= 2 else np.array([])
            price_volume_sync_10 = (
                series_corr(rets[-10:], amount_rets[-10:], min_obs=6)
                if len(rets) >= 10 and len(amount_rets) >= 10
                else np.nan
            )

            turnover_5_window = t[-5:] if len(t) >= 5 else np.array([])
            turnover_20_window = t[-20:] if len(t) >= 20 else np.array([])
            turnover_5 = float(np.nanmean(turnover_5_window)) if np.isfinite(turnover_5_window).any() else np.nan
            turnover_20 = float(np.nanmean(turnover_20_window)) if np.isfinite(turnover_20_window).any() else np.nan
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
            chip_parts = []
            chip_weights = []
            if pd.notna(vol_base):
                chip_parts.append(vol_base)
                chip_weights.append(0.6)
            if pd.notna(range_base):
                chip_parts.append(range_base)
                chip_weights.append(0.4)
            chip_tightness_20 = -float(np.average(chip_parts, weights=chip_weights)) if chip_weights else np.nan

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
                "ma_bull_20_60": ma_bull_20_60,
                "trend_slope_20": trend_slope_20,
                "avg_amount_20": avg_amount_20,
                "amount_ratio_1_20": amount_ratio_1_20,
                "amount_ratio_3_20": amount_ratio_3_20,
                "amount_ratio_5_20": amount_ratio_5_20,
                "money_flow_bias_20": money_flow_bias_20,
                "price_volume_sync_10": price_volume_sync_10,
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
                "chip_tightness_20": chip_tightness_20,
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
        "ma_bull_20_60": np.nan,
        "trend_slope_20": np.nan,
        "avg_amount_20": np.nan,
        "amount_ratio_1_20": np.nan,
        "amount_ratio_3_20": np.nan,
        "amount_ratio_5_20": np.nan,
        "money_flow_bias_20": np.nan,
        "price_volume_sync_10": np.nan,
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
        "chip_tightness_20": np.nan,
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
    "ma_bull_20_60",
    "trend_slope_20",
    "avg_amount_20",
    "amount_ratio_1_20",
    "amount_ratio_3_20",
    "amount_ratio_5_20",
    "money_flow_bias_20",
    "price_volume_sync_10",
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
    "chip_tightness_20",
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


def fetch_short_kline_features(
    codes: List[str],
    max_workers: int = 8,
    retries: int = 2,
    kline_source: str = "auto",
    end_trade_date: Optional[str] = None,
) -> pd.DataFrame:
    if not codes:
        return empty_short_kline_features([])

    out = []
    total = len(codes)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {
            ex.submit(get_short_kline_feature, c, retries, kline_source, end_trade_date): c
            for c in codes
        }
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
    vr_rank = pd.to_numeric(
        candidate["volume_ratio"] if "volume_ratio" in candidate.columns else pd.Series(dtype=float),
        errors="coerce",
    ).clip(0.5, 5.0).rank(pct=True)
    chase_penalty = c_change.gt(7.5).astype(float) * 0.20 + c_amp.gt(14.0).astype(float) * 0.10

    candidate["quote_prefilter_score"] = (
        0.28 * change_rank.fillna(0.0)
        + 0.14 * ret60_rank.fillna(0.0)
        + 0.26 * amount_rank.fillna(0.0)
        + 0.22 * turnover_rank.fillna(0.0)
        - 0.10 * amp_rank.fillna(0.0)
        + 0.10 * vr_rank.fillna(0.5)  # 量比缺失时取中位值（0.5 pct）
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
        "ma_bull_20_60": (ret_60d / 10.0).clip(-0.20, 0.25),
        "trend_slope_20": (ret_60d / 20.0).clip(-0.08, 0.08),
        "avg_amount_20": pd.to_numeric(df["deal_amount"], errors="coerce"),
        "amount_ratio_1_20": (1.0 + change_1d.abs() * 5.0).clip(0.5, 2.5),
        "amount_ratio_3_20": (1.0 + change_1d.abs() * 4.5).clip(0.5, 2.3),
        "amount_ratio_5_20": (1.0 + change_1d.abs() * 4.0).clip(0.6, 2.0),
        "money_flow_bias_20": (change_1d + ret_60d / 5.0).clip(-0.8, 0.8),
        "price_volume_sync_10": (ret_60d / 6.0 - amp / 3.0).clip(-1.0, 1.0),
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
        "chip_tightness_20": -(amp.fillna(0.0)).clip(0.0, 0.3),
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

    # 量比：缺失时填充 1.0（成交量与5日均量持平，中性值）
    if "volume_ratio" not in df.columns:
        df["volume_ratio"] = np.nan
    df["volume_ratio"] = pd.to_numeric(df["volume_ratio"], errors="coerce").fillna(1.0)

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
        "ma_bull_20_60",
        "trend_slope_20",
        "amount_ratio_1_20",
        "amount_ratio_3_20",
        "amount_ratio_5_20",
        "money_flow_bias_20",
        "price_volume_sync_10",
        "turnover_5",
        "turnover_accel_5_20",
        "volume_ratio",
        "lowvol_10_raw",
        "lowvol_20_raw",
        "low_downside_vol_20_raw",
        "drawdown_20_raw",
        "win_rate_20",
        "avg_amount_20_used",
        "turnover_20_used",
        "low_vol_base_raw",
        "low_range_base_raw",
        "chip_tightness_20",
        "accel_raw",
        "trend_efficiency_20",
        "close_strength_5",
        "upper_shadow_5_raw",
    ]

    for c in raw_factor_cols:
        df[c] = winsorize(pd.to_numeric(df[c], errors="coerce"), p=0.025)
        zc = f"{c}_z"
        df[zc] = industry_zscore(df[c], df["industry"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # ── 盘后版权重（15:30-20:00 运行，次日 2-3 交易日持有）──
    wl  = _SCREEN_CFG.get("postclose", {}).get("launch",    {})
    wt  = _SCREEN_CFG.get("postclose", {}).get("trend",     {})
    wm  = _SCREEN_CFG.get("postclose", {}).get("momentum",  {})
    wa  = _SCREEN_CFG.get("postclose", {}).get("activity",  {})
    ws  = _SCREEN_CFG.get("postclose", {}).get("stability", {})
    wsc = _SCREEN_CFG.get("postclose", {}).get("score",     {})

    df["launch_score"] = (
        wl.get("ret_3",              0.16) * df["ret_3_z"]
        + wl.get("ret_5",            0.22) * df["ret_5_z"]
        + wl.get("accel",            0.20) * df["accel_raw_z"]
        + wl.get("high_breakout_20",  0.18) * df["high_breakout_20_z"]
        + wl.get("close_position_20", 0.12) * df["close_position_20_z"]
        + wl.get("price_vs_ma20",    0.08) * df["price_vs_ma20_z"]
        + wl.get("close_strength_5",  0.04) * df["close_strength_5_z"]
    )
    df["trend_score"] = (
        wt.get("ret_10",              0.14) * df["ret_10_z"]
        + wt.get("ret_20",            0.10) * df["ret_20_z"]
        + wt.get("breakout_20",       0.12) * df["breakout_20_z"]
        + wt.get("ma_alignment_20",   0.18) * df["ma_alignment_20_z"]
        + wt.get("ma_bull_20_60",     0.28) * df["ma_bull_20_60_z"]
        + wt.get("trend_slope_20",    0.10) * df["trend_slope_20_z"]
        + wt.get("trend_efficiency_20", 0.08) * df["trend_efficiency_20_z"]
    )
    df["momentum_score"] = (
        wm.get("launch", 0.56) * df["launch_score"]
        + wm.get("trend", 0.44) * df["trend_score"]
    )
    df["activity_score"] = (
        wa.get("amount_ratio_1_20",    0.10) * df["amount_ratio_1_20_z"]
        + wa.get("amount_ratio_3_20",  0.18) * df["amount_ratio_3_20_z"]
        + wa.get("amount_ratio_5_20",  0.06) * df["amount_ratio_5_20_z"]
        + wa.get("turnover_5",         0.10) * df["turnover_5_z"]
        + wa.get("turnover_accel_5_20", 0.06) * df["turnover_accel_5_20_z"]
        + wa.get("volume_ratio",       0.10) * df["volume_ratio_z"]
        + wa.get("money_flow_bias_20", 0.22) * df["money_flow_bias_20_z"]
        + wa.get("price_volume_sync_10", 0.18) * df["price_volume_sync_10_z"]
    )
    df["stability_score"] = (
        ws.get("lowvol_10",             0.08) * df["lowvol_10_raw_z"]
        + ws.get("lowvol_20",           0.10) * df["lowvol_20_raw_z"]
        + ws.get("low_downside_vol_20", 0.10) * df["low_downside_vol_20_raw_z"]
        + ws.get("drawdown_20",         0.18) * df["drawdown_20_raw_z"]
        + ws.get("win_rate_20",         0.12) * df["win_rate_20_z"]
        + ws.get("chip_tightness_20",   0.34) * df["chip_tightness_20_z"]
        + ws.get("upper_shadow_5",      0.08) * df["upper_shadow_5_raw_z"]
    )
    df["liquidity_score"] = 0.55 * df["avg_amount_20_used_z"] + 0.45 * df["turnover_20_used_z"]
    df["score"] = (
        wsc.get("launch",     0.25) * df["launch_score"]
        + wsc.get("trend",    0.26) * df["trend_score"]
        + wsc.get("activity", 0.29) * df["activity_score"]
        + wsc.get("stability", 0.14) * df["stability_score"]
        + wsc.get("liquidity", 0.06) * df["liquidity_score"]
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
    wf = _SCREEN_CFG.get("postclose", {}).get("filters", {})
    change_rate = pd.to_numeric(df["change_rate"], errors="coerce")
    amp = pd.to_numeric(df["amp"], errors="coerce")

    df["pass_daily_chase"] = (
        change_rate.between(wf.get("change_rate_min", -4.0), wf.get("change_rate_max", 9.8))
        & (amp.fillna(99.0) <= wf.get("amp_max", 16.0))
    )
    df["pass_launch_window"] = (
        pd.to_numeric(df["ret_3"],  errors="coerce").between(wf.get("ret_3_min",  -0.02), wf.get("ret_3_max",  0.12))
        & pd.to_numeric(df["ret_5"],  errors="coerce").between(wf.get("ret_5_min",  -0.01), wf.get("ret_5_max",  0.18))
        & pd.to_numeric(df["ret_20"], errors="coerce").between(wf.get("ret_20_min", -0.08), wf.get("ret_20_max", 0.35))
        & pd.to_numeric(df["accel"],  errors="coerce").ge(wf.get("accel_min", -0.05))
    )
    df["pass_breakout_setup"] = (
        pd.to_numeric(df["close_position_20"], errors="coerce").between(wf.get("close_position_20_min", 0.55),  wf.get("close_position_20_max", 1.05))
        & pd.to_numeric(df["high_breakout_20"],  errors="coerce").between(wf.get("high_breakout_20_min", -0.04), wf.get("high_breakout_20_max", 0.12))
        & pd.to_numeric(df["price_vs_ma20"],     errors="coerce").between(wf.get("price_vs_ma20_min",  -0.03), wf.get("price_vs_ma20_max",  0.10))
    )
    df["pass_bull_trend"] = pd.to_numeric(df["ma_bull_20_60"], errors="coerce").ge(
        wf.get("ma_bull_20_60_min", 0.0)
    )
    df["pass_activity_setup"] = (
        pd.to_numeric(df["amount_ratio_3_20"], errors="coerce").between(wf.get("amount_ratio_3_20_min", 1.05), wf.get("amount_ratio_3_20_max", 3.50))
        & pd.to_numeric(df["amount_ratio_5_20"], errors="coerce").between(wf.get("amount_ratio_5_20_min", 0.95), wf.get("amount_ratio_5_20_max", 3.00))
        & pd.to_numeric(df["turnover_5"],         errors="coerce").between(wf.get("turnover_5_min", 1.50),        wf.get("turnover_5_max", 18.00))
    )
    df["pass_flow_sync"] = (
        pd.to_numeric(df["money_flow_bias_20"], errors="coerce").ge(wf.get("money_flow_bias_20_min", 0.05))
        & pd.to_numeric(df["price_volume_sync_10"], errors="coerce").ge(wf.get("price_volume_sync_10_min", 0.0))
    )
    df["pass_risk_setup"] = (
        pd.to_numeric(df["vol_20"],          errors="coerce").le(wf.get("vol_20_max",          0.08))
        & pd.to_numeric(df["max_drawdown_20"], errors="coerce").ge(wf.get("max_drawdown_20_min", -0.18))
        & pd.to_numeric(df["upper_shadow_5"],  errors="coerce").le(wf.get("upper_shadow_5_max",  0.42))
    )
    df["pass_next_2_3d_setup"] = (
        df["pass_daily_chase"]
        & df["pass_launch_window"]
        & df["pass_breakout_setup"]
        & df["pass_bull_trend"]
        & df["pass_activity_setup"]
        & df["pass_flow_sync"]
        & df["pass_risk_setup"]
    )
    return df


def score_factors_tail(df: pd.DataFrame) -> pd.DataFrame:
    """尾盘版（14:30-14:45 运行）打分函数。
    面向当日 14:50 操作，偏重近期短期冲量与当日量能爆发，降低长期趋势权重。
    """
    df = df.copy()

    df["lowvol_10_raw"] = -df["vol_10"]
    df["lowvol_20_raw"] = -df["vol_20"]
    df["low_downside_vol_20_raw"] = -df["downside_vol_20"]
    df["drawdown_20_raw"] = df["max_drawdown_20"]
    df["low_vol_base_raw"] = -df["vol_base"]
    df["low_range_base_raw"] = -df["range_base"]
    df["accel_raw"] = df["accel"]
    df["upper_shadow_5_raw"] = -df["upper_shadow_5"]

    # 量比：缺失时填充 1.0（成交量与5日均量持平，中性值）
    if "volume_ratio" not in df.columns:
        df["volume_ratio"] = np.nan
    df["volume_ratio"] = pd.to_numeric(df["volume_ratio"], errors="coerce").fillna(1.0)

    raw_factor_cols = [
        "ret_3", "ret_5", "ret_10", "ret_20",
        "breakout_20", "high_breakout_20", "close_position_20",
        "price_vs_ma20", "ma_alignment_20", "ma_bull_20_60", "trend_slope_20",
        "amount_ratio_1_20", "amount_ratio_3_20", "amount_ratio_5_20",
        "money_flow_bias_20", "price_volume_sync_10",
        "turnover_5", "turnover_accel_5_20", "volume_ratio",
        "lowvol_10_raw", "lowvol_20_raw", "low_downside_vol_20_raw",
        "drawdown_20_raw", "win_rate_20", "avg_amount_20_used", "turnover_20_used",
        "low_vol_base_raw", "low_range_base_raw", "chip_tightness_20", "accel_raw",
        "trend_efficiency_20", "close_strength_5", "upper_shadow_5_raw",
    ]

    for c in raw_factor_cols:
        df[c] = winsorize(pd.to_numeric(df[c], errors="coerce"), p=0.025)
        zc = f"{c}_z"
        df[zc] = industry_zscore(df[c], df["industry"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # ── 尾盘版权重（14:30-14:45 运行，面向当日 14:50 操作）──
    wtl  = _SCREEN_CFG.get("tail", {}).get("launch",    {})
    wtt  = _SCREEN_CFG.get("tail", {}).get("trend",     {})
    wtm  = _SCREEN_CFG.get("tail", {}).get("momentum",  {})
    wta  = _SCREEN_CFG.get("tail", {}).get("activity",  {})
    wts  = _SCREEN_CFG.get("tail", {}).get("stability", {})
    wtsc = _SCREEN_CFG.get("tail", {}).get("score",     {})

    # 启动：近3/5日冲量 + 加速度为核心，趋势站位权重降低
    df["launch_score"] = (
        wtl.get("ret_3",               0.22) * df["ret_3_z"]
        + wtl.get("ret_5",             0.26) * df["ret_5_z"]
        + wtl.get("accel",             0.22) * df["accel_raw_z"]
        + wtl.get("high_breakout_20",  0.16) * df["high_breakout_20_z"]
        + wtl.get("close_position_20", 0.08) * df["close_position_20_z"]
        + wtl.get("price_vs_ma20",     0.04) * df["price_vs_ma20_z"]
        + wtl.get("close_strength_5",  0.02) * df["close_strength_5_z"]
    )
    # 趋势：突破信号最重要，20日周期权重降低
    df["trend_score"] = (
        wtt.get("ret_10",               0.18) * df["ret_10_z"]
        + wtt.get("ret_20",             0.06) * df["ret_20_z"]
        + wtt.get("breakout_20",        0.24) * df["breakout_20_z"]
        + wtt.get("ma_alignment_20",    0.16) * df["ma_alignment_20_z"]
        + wtt.get("ma_bull_20_60",      0.22) * df["ma_bull_20_60_z"]
        + wtt.get("trend_slope_20",     0.08) * df["trend_slope_20_z"]
        + wtt.get("trend_efficiency_20", 0.06) * df["trend_efficiency_20_z"]
    )
    # 动量：尾盘以"刚启动"为主，大幅偏向 launch
    df["momentum_score"] = (
        wtm.get("launch", 0.68) * df["launch_score"]
        + wtm.get("trend", 0.32) * df["trend_score"]
    )
    # 活跃度：当日量能爆发（amount_ratio_1_20）权重大幅提升；量比为实时量能佐证
    df["activity_score"] = (
        wta.get("amount_ratio_1_20",    0.20) * df["amount_ratio_1_20_z"]
        + wta.get("amount_ratio_3_20",  0.10) * df["amount_ratio_3_20_z"]
        + wta.get("amount_ratio_5_20",  0.04) * df["amount_ratio_5_20_z"]
        + wta.get("turnover_5",         0.08) * df["turnover_5_z"]
        + wta.get("turnover_accel_5_20", 0.04) * df["turnover_accel_5_20_z"]
        + wta.get("volume_ratio",       0.08) * df["volume_ratio_z"]
        + wta.get("money_flow_bias_20", 0.28) * df["money_flow_bias_20_z"]
        + wta.get("price_volume_sync_10", 0.18) * df["price_volume_sync_10_z"]
    )
    # 稳定性：近期低波动 + 底部整理紧密 + 上影线压力
    df["stability_score"] = (
        wts.get("lowvol_10",              0.16) * df["lowvol_10_raw_z"]
        + wts.get("lowvol_20",            0.08) * df["lowvol_20_raw_z"]
        + wts.get("low_downside_vol_20",  0.10) * df["low_downside_vol_20_raw_z"]
        + wts.get("drawdown_20",          0.14) * df["drawdown_20_raw_z"]
        + wts.get("win_rate_20",          0.08) * df["win_rate_20_z"]
        + wts.get("chip_tightness_20",    0.30) * df["chip_tightness_20_z"]
        + wts.get("upper_shadow_5",       0.14) * df["upper_shadow_5_raw_z"]
    )
    df["liquidity_score"] = 0.55 * df["avg_amount_20_used_z"] + 0.45 * df["turnover_20_used_z"]
    # 综合：活跃度与启动并列最高权重，趋势降至最低
    df["score"] = (
        wtsc.get("launch",     0.28) * df["launch_score"]
        + wtsc.get("trend",    0.18) * df["trend_score"]
        + wtsc.get("activity", 0.34) * df["activity_score"]
        + wtsc.get("stability", 0.14) * df["stability_score"]
        + wtsc.get("liquidity", 0.06) * df["liquidity_score"]
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


def add_tail_trade_filters(df: pd.DataFrame) -> pd.DataFrame:
    """尾盘版（14:30-14:45 运行）交易过滤，面向当日 14:50 操作。"""
    df = df.copy()
    wf = _SCREEN_CFG.get("tail", {}).get("filters", {})
    change_rate = pd.to_numeric(df["change_rate"], errors="coerce")
    amp = pd.to_numeric(df["amp"], errors="coerce")

    # 当日涨幅必须在合理上涨区间，排除未启动或已过热的标的
    df["pass_daily_chase"] = (
        change_rate.between(wf.get("change_rate_min", 0.5), wf.get("change_rate_max", 8.5))
        & (amp.fillna(99.0) <= wf.get("amp_max", 15.0))
    )
    # 近3/5日必须有正向动量，且加速度 >= 0（确认刚启动）
    df["pass_launch_window"] = (
        pd.to_numeric(df["ret_3"],  errors="coerce").between(wf.get("ret_3_min",  0.0),   wf.get("ret_3_max",  0.10))
        & pd.to_numeric(df["ret_5"],  errors="coerce").between(wf.get("ret_5_min",  0.0),   wf.get("ret_5_max",  0.15))
        & pd.to_numeric(df["ret_20"], errors="coerce").between(wf.get("ret_20_min", -0.05), wf.get("ret_20_max", 0.30))
        & pd.to_numeric(df["accel"],  errors="coerce").ge(wf.get("accel_min", 0.0))
    )
    # 突破形态：已站上20日均线，收盘位置偏高
    df["pass_breakout_setup"] = (
        pd.to_numeric(df["close_position_20"], errors="coerce").between(wf.get("close_position_20_min", 0.60), wf.get("close_position_20_max", 1.05))
        & pd.to_numeric(df["high_breakout_20"],  errors="coerce").between(wf.get("high_breakout_20_min", -0.02), wf.get("high_breakout_20_max", 0.10))
        & pd.to_numeric(df["price_vs_ma20"],     errors="coerce").between(wf.get("price_vs_ma20_min",   0.0),  wf.get("price_vs_ma20_max",  0.08))
    )
    df["pass_bull_trend"] = pd.to_numeric(df["ma_bull_20_60"], errors="coerce").ge(
        wf.get("ma_bull_20_60_min", 0.0)
    )
    # 活跃度：当日成交量必须明显高于均值
    df["pass_activity_setup"] = (
        pd.to_numeric(df["amount_ratio_1_20"], errors="coerce").ge(wf.get("amount_ratio_1_20_min", 1.20))
        & pd.to_numeric(df["amount_ratio_3_20"], errors="coerce").between(wf.get("amount_ratio_3_20_min", 1.05), wf.get("amount_ratio_3_20_max", 3.50))
        & pd.to_numeric(df["turnover_5"],         errors="coerce").between(wf.get("turnover_5_min", 2.00),        wf.get("turnover_5_max", 18.00))
    )
    df["pass_flow_sync"] = (
        pd.to_numeric(df["money_flow_bias_20"], errors="coerce").ge(wf.get("money_flow_bias_20_min", 0.10))
        & pd.to_numeric(df["price_volume_sync_10"], errors="coerce").ge(wf.get("price_volume_sync_10_min", 0.0))
    )
    # 风险：波动率和回撤更严格，上影线过大不买
    df["pass_risk_setup"] = (
        pd.to_numeric(df["vol_20"],          errors="coerce").le(wf.get("vol_20_max",          0.075))
        & pd.to_numeric(df["max_drawdown_20"], errors="coerce").ge(wf.get("max_drawdown_20_min", -0.16))
        & pd.to_numeric(df["upper_shadow_5"],  errors="coerce").le(wf.get("upper_shadow_5_max",  0.38))
    )
    df["pass_next_2_3d_setup"] = (
        df["pass_daily_chase"]
        & df["pass_launch_window"]
        & df["pass_breakout_setup"]
        & df["pass_bull_trend"]
        & df["pass_activity_setup"]
        & df["pass_flow_sync"]
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


def build_buy_signal_flag(rows: pd.DataFrame) -> pd.Series:
    pass_momentum = rows.get("pass_momentum_floor", pd.Series(False, index=rows.index)).fillna(False).astype(bool)
    pass_setup = rows.get("pass_next_2_3d_setup", pd.Series(False, index=rows.index)).fillna(False).astype(bool)
    return pd.Series(np.where(pass_momentum & pass_setup, "可买入", "-"), index=rows.index, dtype="object")


def write_rank_table(
    f,
    rows: pd.DataFrame,
    title: str,
    run_ts: datetime,
    show_buy_signal: bool = False,
) -> None:
    f.write(f"# {title}\n\n")
    f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
    if rows.get("quote_only_fallback_used", pd.Series(False, index=rows.index)).fillna(False).any():
        f.write("- 数据状态: 历史K线不可用，当前为纯行情降级候选，置信度低于真实K线模型\n")
    if show_buy_signal:
        f.write("| 排名 | 代码 | 名称 | 信号标记 | 百分制得分 | 原始分 | 启动 | 趋势 | 动量 | 活跃 | 稳定 | 流动 |\n")
        f.write("|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    else:
        f.write("| 排名 | 代码 | 名称 | 百分制得分 | 原始分 | 启动 | 趋势 | 动量 | 活跃 | 稳定 | 流动 |\n")
        f.write("|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for _, r in rows.iterrows():
        buy_signal = r.get("buy_signal_flag", "-") if show_buy_signal else None
        if show_buy_signal:
            f.write(
                (
                    "| {rank} | {code} | {name} | {buy_signal} | {score100:.2f} | {score_raw:.4f} | "
                    "{launch:.4f} | {trend:.4f} | {momentum:.4f} | {activity:.4f} | "
                    "{stability:.4f} | {liquidity:.4f} |\n"
                ).format(
                    rank=int(r["rank"]),
                    code=r["code"],
                    name=r["name"],
                    buy_signal=buy_signal,
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
        else:
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
def build_output_dir(output_stem: str, run_ts: datetime) -> Path:
    if output_stem in {DEFAULT_OUTPUT_STEM, DEFAULT_TAIL_OUTPUT_STEM}:
        return OUTPUT_DIR / "history" / output_stem / run_ts.strftime("%Y-%m-%d")
    return OUTPUT_DIR


def build_snapshot_dir(output_stem: str, run_ts: datetime) -> Optional[Path]:
    if output_stem not in {DEFAULT_OUTPUT_STEM, DEFAULT_TAIL_OUTPUT_STEM}:
        return None
    return build_output_dir(output_stem, run_ts) / "runs" / run_ts.strftime("%H%M")


def build_output_path(output_stem: str, suffix: str, run_ts: Optional[datetime] = None) -> Path:
    anchor = run_ts or datetime.now()
    return build_output_dir(output_stem, anchor) / f"{output_stem}_{suffix}"


def build_output_display_path(output_stem: str, suffix: str, run_ts: datetime) -> str:
    output_path = build_output_path(output_stem, suffix, run_ts)
    project_root = OUTPUT_DIR.parent.parent
    return output_path.relative_to(project_root).as_posix()


def write_outputs(
    scored: pd.DataFrame,
    merged: pd.DataFrame,
    run_ts: datetime,
    model_name: str = DEFAULT_MODEL_NAME,
    output_stem: str = DEFAULT_OUTPUT_STEM,
    trade_target_text: str = DEFAULT_TRADE_TARGET_TEXT,
    copy_history: bool = True,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_output_dir(output_stem, run_ts).mkdir(parents=True, exist_ok=True)
    scored = scored.copy()
    merged = merged.copy()
    if "kline_fallback_used" not in scored.columns:
        scored["kline_fallback_used"] = False
    if "quote_only_fallback_used" not in scored.columns:
        scored["quote_only_fallback_used"] = False
    if "pass_momentum_floor" not in scored.columns:
        scored["pass_momentum_floor"] = True
    scored["buy_signal_flag"] = build_buy_signal_flag(scored)

    export_cols = [
        "rank",
        "code",
        "name",
        "industry",
        "buy_signal_flag",
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
        "ma_bull_20_60",
        "trend_slope_20",
        "amount_ratio_1_20",
        "amount_ratio_3_20",
        "amount_ratio_5_20",
        "money_flow_bias_20",
        "price_volume_sync_10",
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
        "chip_tightness_20",
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
    for col in export_cols:
        if col not in scored.columns:
            scored[col] = np.nan
    scored[export_cols].to_csv(build_output_path(output_stem, "passed.csv", run_ts), index=False, encoding="utf-8-sig")

    with build_output_path(output_stem, "passed.md", run_ts).open("w", encoding="utf-8") as f:
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
    top5[export_cols].to_csv(build_output_path(output_stem, "top5.csv", run_ts), index=False, encoding="utf-8-sig")

    with build_output_path(output_stem, "top5.md", run_ts).open("w", encoding="utf-8") as f:
        write_rank_table(
            f,
            top5,
            f"全A（不含科创板）{model_name}模型 Top 5",
            run_ts,
            show_buy_signal=output_stem == DEFAULT_OUTPUT_STEM,
        )

    top20 = scored.head(20).copy()
    top20[export_cols].to_csv(build_output_path(output_stem, "top20.csv", run_ts), index=False, encoding="utf-8-sig")

    with build_output_path(output_stem, "top20.md", run_ts).open("w", encoding="utf-8") as f:
        write_rank_table(f, top20, f"全A（不含科创板）{model_name}模型 Top 20", run_ts)

    kline_ok = int((merged["kline_ok"] == 1).sum())
    kline_candidates = int(merged.get("kline_requested", pd.Series(False, index=merged.index)).fillna(False).sum())
    kline_fallback_used = int(merged.get("kline_fallback_used", pd.Series(False, index=merged.index)).fillna(False).sum())
    scored_real_kline = int((scored["kline_ok"] == 1).sum()) if "kline_ok" in scored.columns else 0
    scored_fallback = int(scored.get("kline_fallback_used", pd.Series(False, index=scored.index)).fillna(False).sum())
    scored_quote_only = int(scored.get("quote_only_fallback_used", pd.Series(False, index=scored.index)).fillna(False).sum())
    hard_pass = merged.get("hard_pass", pd.Series(False, index=merged.index)).fillna(False)
    setup_pass = int((hard_pass & merged.get("pass_next_2_3d_setup", pd.Series(False, index=merged.index)).fillna(False)).sum())
    quote_source_used = str(merged.get("quote_source_used", pd.Series(["unknown"], index=merged.index[:1])).iloc[0]) if not merged.empty else "unknown"
    quote_source_requested = str(merged.get("quote_source_requested", pd.Series(["unknown"], index=merged.index[:1])).iloc[0]) if not merged.empty else "unknown"
    quote_is_intraday = bool(merged.get("quote_is_intraday", pd.Series([False], index=merged.index[:1])).iloc[0]) if not merged.empty else False
    quote_fallback_reason = str(merged.get("quote_fallback_reason", pd.Series([""], index=merged.index[:1])).iloc[0]) if not merged.empty else ""
    with build_output_path(output_stem, "summary.md", run_ts).open("w", encoding="utf-8") as f:
        f.write(f"# 全A（不含科创板）{model_name}筛选统计\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 行情快照请求来源: {quote_source_requested}\n")
        f.write(f"- 行情快照实际来源: {quote_source_used}\n")
        f.write(f"- 行情快照是否盘中: {'是' if quote_is_intraday else '否'}\n")
        if quote_fallback_reason:
            f.write(f"- 行情快照降级原因: {quote_fallback_reason}\n")
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
        f.write(f"- `{build_output_display_path(output_stem, 'passed.csv', run_ts)}`\n")
        f.write(f"- `{build_output_display_path(output_stem, 'passed.md', run_ts)}`\n")
        f.write(f"- `{build_output_display_path(output_stem, 'top5.csv', run_ts)}`\n")
        f.write(f"- `{build_output_display_path(output_stem, 'top5.md', run_ts)}`\n")
        f.write(f"- `{build_output_display_path(output_stem, 'top20.csv', run_ts)}`\n")
        f.write(f"- `{build_output_display_path(output_stem, 'top20.md', run_ts)}`\n")
        f.write(f"- `{build_output_display_path(output_stem, 'summary.md', run_ts)}`\n\n")
        f.write("## 口径说明\n\n")
        f.write("- 股票池: 沪A主板 + 深A主板 + 创业板（不含科创板）\n")
        f.write("- ST过滤: 名称含 `ST` 或 `*ST` 剔除\n")
        f.write("- 上市天数: 优先用K线交易日，缺失时按上市日自然日近似\n")
        f.write("- 执行流动性: 20日均成交额优先，缺失时回退到当日成交额\n")
        f.write("- 启动: 启动加速度（最高权重）、3日/5日收益、20日新高突破、20日区间收盘位置、站上20日均线、近5日收盘强度\n")
        f.write("- 趋势: 10日/20日收益、20日均线突破、均线排列、20/60日多头强度、20日斜率、趋势效率\n")
        f.write("- 活跃度: 1日/3日/5日对20日成交额放大比、5日换手率、换手加速度、20日资金流向偏置、10日量价同步\n")
        f.write("- 稳定性: 筹码集中度（底部低波动+振幅收敛，最高权重）、10/20日波动率、下行波动、20日最大回撤、20日上涨胜率、近5日上影线\n")
        f.write("- 选股流程: 先做硬过滤并在可交易样本内打分，再叠加次日2-3天交易过滤与动量地板\n")
        f.write("- 启动检测: 底部横盘低波动收敛 + 量价同步放大 + 刚突破均线（20日涨幅≤20%、5日涨幅≤12%）+ 回撤受控\n")
        f.write(f"- 交易目标: {trade_target_text}\n")
        f.write("- 次日交易过滤: 过滤当日追高、过热、量能过弱、波动过大、上影压力过重样本\n")
        if not quote_is_intraday:
            f.write("- 行情口径: 当前快照已降级为日线/非盘中口径，时效性弱于盘中快照\n")
            if output_stem == DEFAULT_TAIL_OUTPUT_STEM:
                f.write("- 尾盘风险提示: 本次未取得盘中快照，不建议直接用于14:30-14:45尾盘实盘决策\n")
        if kline_fallback_used:
            f.write("- 快速兜底: 已启用代理因子；实盘交易建议优先使用真实K线样本\n")
        elif scored_quote_only:
            f.write("- 纯行情降级: 当前历史K线不可用，Top5由收盘涨跌幅、60日涨幅、成交额、换手率、振幅生成，仅作低置信候选\n")
        else:
            f.write("- K线口径: 默认不使用代理兜底，真实K线缺失时不进入最终Top5\n")
        f.write("- 百分制得分: 按原始综合分在全样本中的线性排名换算到0-100\n")

    if copy_history:
        # 保留带时间戳的历史副本，避免同日多次运行覆盖。
        _history_dir = build_snapshot_dir(output_stem, run_ts)
        if _history_dir is None:
            _history_dir = OUTPUT_DIR / "history"
        _history_dir.mkdir(parents=True, exist_ok=True)
        for _suf in ["top5.md", "top20.csv", "summary.md"]:
            _src = build_output_path(output_stem, _suf, run_ts)
            if _src.exists():
                shutil.copy2(_src, _history_dir / f"{output_stem}_{_suf}")


def run_screen(
    model_name: str = DEFAULT_MODEL_NAME,
    output_stem: str = DEFAULT_OUTPUT_STEM,
    trade_target_text: str = DEFAULT_TRADE_TARGET_TEXT,
    _mode: str = "postclose",
    run_ts: Optional[datetime] = None,
    trade_date: Optional[str] = None,
    persist_outputs: bool = True,
    copy_history: bool = True,
) -> dict:
    """通用筛选入口。_mode='postclose'（盘后版）或 'tail'（尾盘版）。"""
    run_ts = run_ts or datetime.now()
    as_of = pd.to_datetime(trade_date, format="%Y%m%d", errors="coerce").date() if trade_date else run_ts.date()
    kline_workers = max(1, int(os.environ.get("SHORT_KLINE_WORKERS", "6")))
    kline_retries = max(1, int(os.environ.get("SHORT_KLINE_RETRIES", "2")))
    kline_candidate_limit = max(0, int(os.environ.get("SHORT_KLINE_CANDIDATE_LIMIT", str(DEFAULT_KLINE_CANDIDATE_LIMIT))))

    if _mode == "tail":
        momentum_score_floor = float(
            os.environ.get("SHORT_TAIL_MOMENTUM_SCORE_FLOOR", str(DEFAULT_TAIL_MOMENTUM_SCORE_FLOOR))
        )
        launch_score_floor = float(
            os.environ.get("SHORT_TAIL_LAUNCH_SCORE_FLOOR", str(DEFAULT_TAIL_LAUNCH_SCORE_FLOOR))
        )
    else:
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
    quote_source = "auto" if _mode == "tail" else "tushare"
    kline_source = "tushare"
    tail_allow_daily_fallback = os.environ.get("SHORT_TAIL_ALLOW_DAILY_FALLBACK", "0") == "1"

    print("[1/3] fetch A-share quote universe (no STAR)...")
    quote = fetch_a_no_star_quotes(source=quote_source, trade_date=trade_date, as_of=run_ts)
    quote_source_used = str(quote["quote_source_used"].iloc[0]) if (not quote.empty and "quote_source_used" in quote.columns) else quote_source
    quote_is_intraday = bool(quote["quote_is_intraday"].iloc[0]) if (not quote.empty and "quote_is_intraday" in quote.columns) else False
    quote_fallback_reason = str(quote["quote_fallback_reason"].iloc[0]) if (not quote.empty and "quote_fallback_reason" in quote.columns) else ""
    resolved_trade_date = ""
    if not quote.empty and "trade_date" in quote.columns:
        resolved_trade_date = pd.to_datetime(quote["trade_date"].iloc[0], errors="coerce").strftime("%Y%m%d")
    print(
        f"[1/3] quote rows={len(quote)}, source_used={quote_source_used}, intraday={int(quote_is_intraday)}"
    )
    if quote_fallback_reason:
        print(f"[1/3] quote fallback reason: {quote_fallback_reason}")
    if _mode == "tail" and not quote_is_intraday:
        msg = (
            "tail mode 未取得盘中快照，当前已降级为日线口径；"
            "默认中止运行，若仅做参考可设置 SHORT_TAIL_ALLOW_DAILY_FALLBACK=1 后重试"
        )
        if not tail_allow_daily_fallback:
            raise RuntimeError(msg)
        print(f"[tail-warning] {msg}")

    print("[2/3] fetch org info + short kline features...")
    print(
        f"[2/3] short kline config: workers={kline_workers}, retries={kline_retries}, "
        f"fast_prefilter={int(fast_prefilter)}, skip_kline={int(skip_kline)}, "
        f"kline_fallback={int(kline_fallback)}, require_real_kline={int(require_real_kline)}, "
        f"requested_candidate_limit={kline_candidate_limit}, quote_only_fallback={int(quote_only_fallback)}, "
        f"quote_source={quote_source}, quote_source_used={quote_source_used}, "
        f"tail_allow_daily_fallback={int(tail_allow_daily_fallback)}, kline_source={kline_source}"
    )
    org = fetch_org_info(quote["secucode"].unique().tolist(), trade_date=resolved_trade_date or trade_date, as_of=run_ts)

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
        kf = fetch_short_kline_features(
            kline_codes,
            max_workers=kline_workers,
            retries=kline_retries,
            kline_source=kline_source,
            end_trade_date=resolved_trade_date or trade_date,
        )
    print(f"[2/3] org rows={len(org)}, short kline rows={len(kf)}")

    print("[3/3] merge, filter, score...")
    df = pre
    df = df.merge(kf, on="code", how="left")
    if kline_fallback:
        df = apply_short_kline_fallback(df)

    calendar_days = df["calendar_listed_days"].fillna(9999)
    df["pass_listing"] = np.where(df["listed_days_kline"].notna(), df["listed_days_kline"] >= 60, calendar_days >= 90)

    df["avg_amount_20_used"] = df["avg_amount_20"].where(df["avg_amount_20"].notna(), df["deal_amount"])
    df["turnover_5"] = df["turnover_5"].where(df["turnover_5"].notna(), df["turnover"])
    df["turnover_20"] = df["turnover_20"].where(df["turnover_20"].notna(), df["turnover"])
    df["turnover_20_used"] = df["turnover_20"].where(df["turnover_20"].notna(), df["turnover"])
    neutral_turnover_accel = pd.Series(0.0, index=df.index)
    df["turnover_accel_5_20"] = df["turnover_accel_5_20"].where(
        df["turnover_accel_5_20"].notna(),
        neutral_turnover_accel,
    )

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

    if _mode == "tail":
        df = add_tail_trade_filters(df)
    else:
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
        "price_vs_ma20",
        "ma_alignment_20",
        "ma_bull_20_60",
        "trend_slope_20",
        "amount_ratio_1_20",
        "amount_ratio_3_20",
        "amount_ratio_5_20",
        "money_flow_bias_20",
        "price_volume_sync_10",
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
        "chip_tightness_20",
        "accel",
        "trend_efficiency_20",
        "close_strength_5",
        "upper_shadow_5",
        "avg_amount_20_used",
    ]
    base["raw_missing_count"] = base[raw_cols].isna().sum(axis=1)
    base = base[base["raw_missing_count"] <= 6].copy()

    if _mode == "tail":
        scored = score_factors_tail(base)
    else:
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
    if persist_outputs:
        write_outputs(
            scored,
            df,
            run_ts,
            model_name=model_name,
            output_stem=output_stem,
            trade_target_text=trade_target_text,
            copy_history=copy_history,
        )

    print("done")
    print(f"universe_total={len(df)}")
    print(f"hard_pass={int(df['hard_pass'].sum())}")
    print(f"final_passed={len(scored)}")
    return {
        "quote": quote,
        "quote_is_intraday": quote_is_intraday,
        "quote_source_used": quote_source_used,
        "trade_date": resolved_trade_date or trade_date,
        "scored": scored,
        "merged": df,
    }


def run_tail_screen(
    model_name: str = DEFAULT_TAIL_MODEL_NAME,
    output_stem: str = DEFAULT_TAIL_OUTPUT_STEM,
    trade_target_text: str = DEFAULT_TAIL_TRADE_TARGET_TEXT,
) -> None:
    """尾盘版筛选入口（14:30-14:45 运行，面向当日 14:50 操作）。"""
    result = run_screen(
        model_name=model_name,
        output_stem=output_stem,
        trade_target_text=trade_target_text,
        _mode="tail",
    )
    try:
        settlement_summary = auto_settle_due_buylists(
            result["quote"],
            quote_is_intraday=bool(result["quote_is_intraday"]),
        )
    except Exception as exc:
        print(f"[tail-settle] error: {exc}")
        settlement_summary = {
            "status": "error",
            "message": str(exc),
            "settledCount": 0,
            "pendingFileCount": 0,
            "pendingSymbolCount": 0,
            "settledFiles": [],
            "skippedFiles": [],
        }
    print(f"{SETTLEMENT_SUMMARY_PREFIX}{json.dumps(settlement_summary, ensure_ascii=False)}")


def main() -> None:
    run_screen()


if __name__ == "__main__":
    main()
