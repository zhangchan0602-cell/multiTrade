#!/usr/bin/env python3
"""Build an industry trend ranking from cached full-market daily snapshots.

The output is deliberately based on price/volume model events, not news. It
keeps the industry dashboard available from the same local data used by the
screeners and avoids inventing external event labels.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".cache"
DAILY_DIR = CACHE_DIR / "daily"
BASIC_PATH = CACHE_DIR / "stock_basic.csv"
DEFAULT_OUTPUT = ROOT.parent / "docs" / "list" / "industry_trend_rank.json"
HORIZONS = (3, 5, 10)


def safe_float(value):
    return None if pd.isna(value) or not np.isfinite(value) else round(float(value), 6)


def zscore_by_date(series: pd.Series, dates: pd.Series) -> pd.Series:
    grouped = series.groupby(dates)
    mean = grouped.transform("mean")
    std = grouped.transform(lambda values: values.std(ddof=0))
    return ((series - mean) / std.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -8.0, 8.0)))


def estimate_probabilities(current: pd.Series) -> dict:
    """Transparent direction estimates from the current industry state.

    These are model estimates rather than historical-frequency probabilities.
    They intentionally remain bounded and use the same public fields shown in
    the ranking, making the result inspectable from the UI.
    """
    trend = (
        0.80 * np.clip(current["ret_3"] / 0.05, -2.0, 2.0)
        + 0.70 * np.clip(current["ret_5"] / 0.08, -2.0, 2.0)
        + 0.45 * np.clip(current["ret_10"] / 0.12, -2.0, 2.0)
        + 0.55 * np.clip((current["breadth_3"] - 0.5) / 0.30, -2.0, 2.0)
        + 0.25 * np.clip(np.log(max(current["amount_ratio_3_20"], 0.25)), -1.0, 1.0)
    )
    output = {}
    for horizon, scale in ((3, 0.62), (5, 0.54), (10, 0.46)):
        up = sigmoid(-0.08 + scale * trend)
        drawdown = sigmoid(-0.22 - 0.52 * scale * trend + 0.26 * max(current["ret_3"] / 0.10 - 0.7, 0.0))
        output[str(horizon)] = {
            "up": safe_float(float(np.clip(up, 0.12, 0.88))),
            "drawdown": safe_float(float(np.clip(drawdown, 0.10, 0.82))),
        }
    return output


def model_events(row: pd.Series) -> list[str]:
    events = []
    if row["ret_3"] >= 0.045 and row["ret_5"] > 0:
        events.append("趋势加速")
    if row["breadth_3"] >= 0.65:
        events.append("上涨扩散")
    if row["amount_ratio_3_20"] >= 1.25:
        events.append("量能放大")
    if row["ret_3"] >= 0.10 or row["ret_5"] >= 0.16:
        events.append("短线过热")
    if row["ret_5"] <= -0.06 and row["breadth_5"] <= 0.35:
        events.append("趋势转弱")
    return events or ["趋势观察"]


def load_daily_history() -> pd.DataFrame:
    # 25 sessions cover the current 3/5/10-day trend and 20-day volume base while
    # keeping an on-demand dashboard refresh comfortably bounded.
    files = sorted(DAILY_DIR.glob("*.csv"))[-25:]
    if len(files) < 25:
        raise RuntimeError("日线缓存不足，至少需要 25 个交易日快照")

    basic = pd.read_csv(BASIC_PATH, dtype={"secucode": str})
    industry_map = basic.set_index("secucode")["industry"].fillna("未知行业")
    frames = []
    for file_path in files:
        frame = pd.read_csv(file_path, usecols=lambda column: column in {"ts_code", "trade_date", "pct_chg", "amount"})
        if frame.empty or "ts_code" not in frame.columns:
            continue
        frame = frame.rename(columns={"ts_code": "secucode"})
        frame["trade_date"] = frame["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
        frame["pct_chg"] = pd.to_numeric(frame.get("pct_chg"), errors="coerce") / 100.0
        frame["amount"] = pd.to_numeric(frame.get("amount"), errors="coerce").fillna(0.0)
        frame["industry"] = frame["secucode"].map(industry_map).fillna("未知行业")
        frame = frame[(frame["industry"] != "未知行业") & ~frame["secucode"].str.startswith("688")]
        frames.append(frame[["trade_date", "secucode", "industry", "pct_chg", "amount"]])

    if not frames:
        raise RuntimeError("没有可用的行业日线缓存")
    return pd.concat(frames, ignore_index=True).drop_duplicates(["trade_date", "secucode"], keep="last")


def build_ranking() -> dict:
    daily = load_daily_history()
    daily["up_today"] = daily["pct_chg"] > 0
    industry = daily.groupby(["trade_date", "industry"], as_index=False).agg(
        member_count=("secucode", "nunique"),
        daily_ret=("pct_chg", "median"),
        breadth_today=("up_today", "mean"),
        amount=("amount", "sum"),
    ).sort_values(["industry", "trade_date"]).reset_index(drop=True)
    for horizon in (3, 5, 10):
        industry[f"ret_{horizon}"] = industry.groupby("industry")["daily_ret"].transform(
            lambda values: np.expm1(np.log1p(values).rolling(horizon).sum())
        )
    industry["breadth_3"] = industry.groupby("industry")["breadth_today"].transform(lambda values: values.rolling(3).mean())
    industry["breadth_5"] = industry.groupby("industry")["breadth_today"].transform(lambda values: values.rolling(5).mean())
    industry["amount_ma3"] = industry.groupby("industry")["amount"].transform(lambda values: values.rolling(3).mean())
    industry["amount_ma20"] = industry.groupby("industry")["amount"].transform(lambda values: values.rolling(20).mean())
    industry["amount_ratio_3_20"] = industry["amount_ma3"] / industry["amount_ma20"]

    score = (
        0.30 * zscore_by_date(industry["ret_3"], industry["trade_date"])
        + 0.28 * zscore_by_date(industry["ret_5"], industry["trade_date"])
        + 0.18 * zscore_by_date(industry["ret_10"], industry["trade_date"])
        + 0.12 * zscore_by_date(industry["breadth_3"], industry["trade_date"])
        + 0.12 * zscore_by_date(industry["breadth_5"], industry["trade_date"])
    )
    industry["heat"] = (50.0 + 18.0 * score).clip(0.0, 100.0)

    latest_date = industry["trade_date"].max()
    latest = industry[industry["trade_date"] == latest_date].copy()
    if latest.empty:
        raise RuntimeError("无法计算最新行业截面")

    records = []
    for _, row in latest.iterrows():
        probabilities = estimate_probabilities(row)
        records.append({
            "industry": row["industry"],
            "memberCount": int(row["member_count"]),
            "heat": safe_float(row["heat"]),
            "events": model_events(row),
            "ret3": safe_float(row["ret_3"]),
            "ret5": safe_float(row["ret_5"]),
            "ret10": safe_float(row["ret_10"]),
            "breadth3": safe_float(row["breadth_3"]),
            "amountRatio3_20": safe_float(row["amount_ratio_3_20"]),
            "probabilities": probabilities,
        })

    records.sort(key=lambda item: (item["heat"] is None, -(item["heat"] or 0), item["industry"]))
    for index, record in enumerate(records, start=1):
        record["rank"] = index
    return {
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tradeDate": str(latest_date),
        "source": "本地全市场日线缓存；事件为量价模型事件",
        "industries": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="build industry trend ranking")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_ranking()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[industry-trend] {result['tradeDate']} industries={len(result['industries'])} output={args.output}")


if __name__ == "__main__":
    main()
