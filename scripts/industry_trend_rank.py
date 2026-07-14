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
MODEL_VERSION = 2
MIN_HISTORY_SESSIONS = 60
HISTORY_SESSIONS = 80
QUANTILE_WINDOW = 60


def safe_float(value):
    return None if pd.isna(value) or not np.isfinite(value) else round(float(value), 6)


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -8.0, 8.0)))


def estimate_probabilities(current: pd.Series) -> dict:
    """Transparent direction estimates from the three-model consensus state.

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
        + 0.90 * np.clip(current["consensus_score"], -1.0, 1.0)
    )
    output = {}
    for horizon, scale in ((3, 0.62), (5, 0.54), (10, 0.46), (20, 0.38)):
        up = sigmoid(-0.08 + scale * trend)
        drawdown = sigmoid(-0.22 - 0.52 * scale * trend + 0.26 * max(current["ret_3"] / 0.10 - 0.7, 0.0))
        output[str(horizon)] = {
            "up": safe_float(float(np.clip(up, 0.12, 0.88))),
            "drawdown": safe_float(float(np.clip(drawdown, 0.10, 0.82))),
        }
    return output


def model_events(row: pd.Series) -> list[str]:
    events = [row["macd_volume_signal"], row["quantile_signal"], row["dual_ma_signal"]]
    if row["ret_3"] >= 0.10 or row["ret_5"] >= 0.16:
        events.append("短线过热")
    if row["ret_5"] <= -0.06 and row["breadth_5"] <= 0.35:
        events.append("趋势转弱")
    return list(dict.fromkeys(events))


def clip_score(series: pd.Series, scale: float) -> pd.Series:
    return (series / scale).clip(-1.0, 1.0).fillna(0.0)


def macd_volume_signal(row: pd.Series) -> str:
    if row["macd_hist_pct"] > 0 and row["macd_dif_pct"] > 0 and row["amount_ratio_3_20"] >= 1.15:
        return "MACD量能共振"
    if row["macd_hist_pct"] > 0 and row["macd_dif_pct"] > 0:
        return "MACD转强"
    if row["macd_hist_pct"] < 0 and row["amount_ratio_3_20"] >= 1.15:
        return "量能失配"
    return "MACD转弱"


def quantile_signal(row: pd.Series) -> str:
    if row["quantile_score"] >= 0.70:
        return "强势极值"
    if row["quantile_score"] <= -0.70:
        return "弱势极值"
    return "分位中性"


def dual_ma_signal(row: pd.Series) -> str:
    if row["ma5_ma20_spread"] > 0 and row["ma20_slope_5"] > 0:
        return "双均线多头"
    if row["ma5_ma20_spread"] < 0 and row["ma20_slope_5"] < 0:
        return "双均线空头"
    return "均线收敛"


def load_daily_history(session_limit: int | None = HISTORY_SESSIONS) -> pd.DataFrame:
    files = sorted(DAILY_DIR.glob("*.csv"))
    if len(files) < MIN_HISTORY_SESSIONS:
        raise RuntimeError(f"日线缓存不足，至少需要 {MIN_HISTORY_SESSIONS} 个交易日快照")
    if session_limit is not None:
        files = files[-session_limit:]

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


def build_industry_features(daily: pd.DataFrame) -> pd.DataFrame:
    """Build causal daily model features for every available industry session."""
    daily = daily.copy()
    daily["up_today"] = daily["pct_chg"] > 0
    industry = daily.groupby(["trade_date", "industry"], as_index=False).agg(
        member_count=("secucode", "nunique"),
        daily_ret=("pct_chg", "median"),
        breadth_today=("up_today", "mean"),
        amount=("amount", "sum"),
    ).sort_values(["industry", "trade_date"]).reset_index(drop=True)
    for horizon in (3, 5, 10, 20):
        industry[f"ret_{horizon}"] = industry.groupby("industry")["daily_ret"].transform(
            lambda values: np.expm1(np.log1p(values).rolling(horizon).sum())
        )
    industry["breadth_3"] = industry.groupby("industry")["breadth_today"].transform(lambda values: values.rolling(3).mean())
    industry["breadth_5"] = industry.groupby("industry")["breadth_today"].transform(lambda values: values.rolling(5).mean())
    industry["amount_ma3"] = industry.groupby("industry")["amount"].transform(lambda values: values.rolling(3).mean())
    industry["amount_ma20"] = industry.groupby("industry")["amount"].transform(lambda values: values.rolling(20).mean())
    industry["amount_ratio_3_20"] = industry["amount_ma3"] / industry["amount_ma20"]

    # A synthetic industry close built from median daily returns keeps the three
    # technical models robust against a single high-volatility component stock.
    industry["index_close"] = industry.groupby("industry")["daily_ret"].transform(
        lambda values: 100.0 * (1.0 + values).cumprod()
    )
    industry["ema12"] = industry.groupby("industry")["index_close"].transform(
        lambda values: values.ewm(span=12, adjust=False, min_periods=12).mean()
    )
    industry["ema26"] = industry.groupby("industry")["index_close"].transform(
        lambda values: values.ewm(span=26, adjust=False, min_periods=26).mean()
    )
    industry["macd_dif"] = industry["ema12"] - industry["ema26"]
    industry["macd_dea"] = industry.groupby("industry")["macd_dif"].transform(
        lambda values: values.ewm(span=9, adjust=False, min_periods=9).mean()
    )
    industry["macd_hist_pct"] = (industry["macd_dif"] - industry["macd_dea"]) / industry["index_close"]
    industry["macd_dif_pct"] = industry["macd_dif"] / industry["index_close"]
    industry["macd_volume_score"] = (
        0.60 * clip_score(industry["macd_hist_pct"], 0.008)
        + 0.20 * clip_score(industry["macd_dif_pct"], 0.015)
        + 0.20 * clip_score(industry["amount_ratio_3_20"] - 1.0, 0.60)
    )

    industry["ret5_quantile"] = industry.groupby("industry")["ret_5"].transform(
        lambda values: values.rolling(QUANTILE_WINDOW, min_periods=30).rank(pct=True)
    )
    industry["ret20_quantile"] = industry.groupby("industry")["ret_20"].transform(
        lambda values: values.rolling(QUANTILE_WINDOW, min_periods=30).rank(pct=True)
    )
    industry["quantile_score"] = (
        0.60 * (industry["ret5_quantile"].fillna(0.5) - 0.5) * 2.0
        + 0.40 * (industry["ret20_quantile"].fillna(0.5) - 0.5) * 2.0
    ).clip(-1.0, 1.0)

    industry["ma5"] = industry.groupby("industry")["index_close"].transform(lambda values: values.rolling(5).mean())
    industry["ma20"] = industry.groupby("industry")["index_close"].transform(lambda values: values.rolling(20).mean())
    industry["ma5_ma20_spread"] = industry["ma5"] / industry["ma20"] - 1.0
    industry["ma20_slope_5"] = industry.groupby("industry")["ma20"].transform(lambda values: values / values.shift(5) - 1.0)
    industry["dual_ma_score"] = (
        0.60 * clip_score(industry["ma5_ma20_spread"], 0.025)
        + 0.40 * clip_score(industry["ma20_slope_5"], 0.040)
    )

    industry["consensus_score"] = (
        0.40 * industry["macd_volume_score"]
        + 0.30 * industry["quantile_score"]
        + 0.30 * industry["dual_ma_score"]
    ).clip(-1.0, 1.0)
    industry["heat"] = (50.0 + 35.0 * industry["consensus_score"]).clip(0.0, 100.0)
    industry["macd_volume_signal"] = industry.apply(macd_volume_signal, axis=1)
    industry["quantile_signal"] = industry.apply(quantile_signal, axis=1)
    industry["dual_ma_signal"] = industry.apply(dual_ma_signal, axis=1)

    return industry


def build_ranking() -> dict:
    industry = build_industry_features(load_daily_history())

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
            "consensusScore": safe_float(row["consensus_score"]),
            "models": {
                "macdVolume": {
                    "score": safe_float(row["macd_volume_score"]),
                    "signal": row["macd_volume_signal"],
                    "histPct": safe_float(row["macd_hist_pct"]),
                    "amountRatio": safe_float(row["amount_ratio_3_20"]),
                },
                "quantileExtreme": {
                    "score": safe_float(row["quantile_score"]),
                    "signal": row["quantile_signal"],
                    "ret5Quantile": safe_float(row["ret5_quantile"]),
                    "ret20Quantile": safe_float(row["ret20_quantile"]),
                },
                "dualMA": {
                    "score": safe_float(row["dual_ma_score"]),
                    "signal": row["dual_ma_signal"],
                    "maSpread": safe_float(row["ma5_ma20_spread"]),
                    "ma20Slope5": safe_float(row["ma20_slope_5"]),
                },
            },
            "probabilities": probabilities,
        })

    records.sort(key=lambda item: (item["heat"] is None, -(item["heat"] or 0), item["industry"]))
    for index, record in enumerate(records, start=1):
        record["rank"] = index
    return {
        "modelVersion": MODEL_VERSION,
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tradeDate": str(latest_date),
        "source": "本地全市场日线缓存；MACD量能、分位数极值、双均线三模型共识",
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
