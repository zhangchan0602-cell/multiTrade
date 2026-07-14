#!/usr/bin/env python3
"""Monthly multi-opportunity accuracy backtest for the industry trend model."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from industry_trend_rank import build_industry_features, estimate_probabilities, load_daily_history
from screen_common import fetch_trade_cal_dates


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "list"


def date_label(value: str) -> str:
    text = str(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else text


def select_monthly_signal_dates(
    trade_calendar: list[str],
    available_dates: set[str],
    hold_days: int,
    months: int,
    opportunities_per_month: int,
) -> list[tuple[str, list[str]]]:
    calendar_index = {trade_date: index for index, trade_date in enumerate(trade_calendar)}
    eligible_dates = []
    for trade_date in trade_calendar:
        index = calendar_index[trade_date]
        holding_dates = trade_calendar[index + 1 : index + 1 + hold_days]
        if trade_date in available_dates and len(holding_dates) == hold_days and all(day in available_dates for day in holding_dates):
            eligible_dates.append(trade_date)

    by_month: dict[str, list[str]] = {}
    for trade_date in eligible_dates:
        by_month.setdefault(trade_date[:6], []).append(trade_date)

    selected = []
    for month, dates in sorted(by_month.items()):
        if len(dates) < opportunities_per_month:
            continue
        # Spread five opportunities across the month instead of front-loading entries.
        positions = np.linspace(0, len(dates) - 1, opportunities_per_month, dtype=int)
        selected.append((month, [dates[index] for index in positions]))
    return selected[-months:]


def forward_return(
    daily_returns: pd.Series,
    trade_dates: list[str],
    date_index: dict[str, int],
    signal_date: str,
    hold_days: int,
) -> tuple[str, float] | None:
    start = date_index[signal_date] + 1
    exit_dates = trade_dates[start : start + hold_days]
    if len(exit_dates) != hold_days:
        return None
    returns = daily_returns.reindex(exit_dates)
    if returns.isna().any():
        return None
    return exit_dates[-1], float(np.expm1(np.log1p(returns).sum()))


def qualifies(row: pd.Series, up_3d: float, up_5d: float, max_drawdown: float) -> tuple[bool, str]:
    probabilities = estimate_probabilities(row)
    pass_3d = probabilities["3"]["up"] > up_3d and probabilities["3"]["drawdown"] < max_drawdown
    pass_5d = probabilities["5"]["up"] > up_5d and probabilities["5"]["drawdown"] < max_drawdown
    if pass_3d and pass_5d:
        return True, "3日+5日"
    if pass_3d:
        return True, "3日"
    if pass_5d:
        return True, "5日"
    return False, ""


def write_markdown(
    path: Path,
    trades: pd.DataFrame,
    monthly: pd.DataFrame,
    *,
    months: int,
    opportunities_per_month: int,
    top_n: int,
    up_3d: float,
    up_5d: float,
    max_drawdown: float,
    hold_days: int,
    max_trades: int,
) -> None:
    valid = trades["forward_return"] if not trades.empty else pd.Series(dtype=float)
    active_months = monthly[monthly["trade_count"] > 0]
    win_rate = float((valid > 0).mean()) if not valid.empty else float("nan")
    avg_return = float(valid.mean()) if not valid.empty else float("nan")
    median_return = float(valid.median()) if not valid.empty else float("nan")
    monthly_win_rate = float((active_months["avg_return"] > 0).mean()) if not active_months.empty else float("nan")

    def pct(value: float) -> str:
        return f"{value:.2%}" if np.isfinite(value) else "-"

    lines = [
        "# 行业趋势多机会回测",
        "",
        f"- 月度机会: 最近 {months} 个完整自然月，每月均匀取 {opportunities_per_month} 个实际交易日",
        f"- 买入上限: 全周期按时间顺序最多 {max_trades} 笔；每个机会日最多买入热度最高的一只行业",
        f"- 候选池: 当日行业热度前 {top_n}",
        f"- 入选规则: 3日上涨概率 > {up_3d:.0%} 且回撤概率 < {max_drawdown:.0%}，或 5日上涨概率 > {up_5d:.0%} 且回撤概率 < {max_drawdown:.0%}",
        f"- 持有口径: 信号日收盘后，持有后续 {hold_days} 个实际交易日",
        "- 收益口径: 行业内成分股日收益中位数复利，不含手续费与滑点；用于检验行业信号准确性，不等同于可交易 ETF 收益",
        "",
        "## 汇总",
        "",
        f"- 计划机会数: {int(monthly['opportunity_count'].sum())}",
        f"- 实际触发买入机会: {int(monthly['qualified_opportunity_count'].sum())}",
        f"- 实际买入行业次数: {len(valid)}",
        f"- 5日方向准确率: {pct(win_rate)}",
        f"- 平均 5 日收益: {pct(avg_return)}",
        f"- 中位 5 日收益: {pct(median_return)}",
        f"- 有持仓月份等权收益为正比例: {pct(monthly_win_rate)}",
        "",
        "## 月度明细",
        "",
        "| 月份 | 机会数 | 触发机会 | 买入数 | 胜/负 | 月度平均收益 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in monthly.iterrows():
        win_loss = f"{int(row['win_count'])}/{int(row['loss_count'])}" if row["trade_count"] else "-"
        lines.append(
            f"| {row['month'][:4]}-{row['month'][4:]} | {int(row['opportunity_count'])} | {int(row['qualified_opportunity_count'])} | {int(row['trade_count'])} | {win_loss} | {pct(row['avg_return'])} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_backtest(
    months: int,
    opportunities_per_month: int,
    top_n: int,
    up_3d: float,
    up_5d: float,
    max_drawdown: float,
    hold_days: int,
    max_trades: int,
    output_dir: Path,
) -> dict:
    daily = load_daily_history(session_limit=None)
    features = build_industry_features(daily)
    available_dates = set(features["trade_date"].astype(str).unique().tolist())
    trade_dates = fetch_trade_cal_dates(min(available_dates), max(available_dates))
    if not trade_dates:
        raise RuntimeError("无法读取真实交易日历，拒绝使用缓存文件数量替代交易日")

    monthly_dates = select_monthly_signal_dates(
        trade_dates, available_dates, hold_days, months, opportunities_per_month
    )
    if len(monthly_dates) != months:
        raise RuntimeError(f"只有 {len(monthly_dates)} 个自然月具备完整行情，无法完成 {months} 个月度回测")

    date_index = {trade_date: index for index, trade_date in enumerate(trade_dates)}
    return_series = {
        industry: frame.set_index("trade_date")["daily_ret"]
        for industry, frame in features.groupby("industry", sort=False)
    }
    trade_rows = []
    monthly_rows = []

    for month, signal_dates in monthly_dates:
        monthly_trades = []
        qualified_opportunities = 0
        for signal_date in signal_dates:
            snapshot = features[features["trade_date"] == signal_date].copy()
            top = snapshot.sort_values(["heat", "industry"], ascending=[False, True]).head(top_n).copy()
            top["rank"] = np.arange(1, len(top) + 1)
            top[["qualified", "signal_rule"]] = top.apply(
                lambda row: pd.Series(qualifies(row, up_3d, up_5d, max_drawdown)), axis=1
            )
            selected = top[top["qualified"]].head(1)
            if selected.empty:
                continue
            qualified_opportunities += 1
            if len(trade_rows) >= max_trades:
                continue

            row = selected.iloc[0]
            result = forward_return(return_series[row["industry"]], trade_dates, date_index, signal_date, hold_days)
            if result is None:
                continue
            exit_date, ret = result
            probabilities = estimate_probabilities(row)
            trade = {
                "signal_date": signal_date,
                "exit_date": exit_date,
                "industry": row["industry"],
                "rank": int(row["rank"]),
                "heat": float(row["heat"]),
                "consensus_score": float(row["consensus_score"]),
                "signal_rule": row["signal_rule"],
                "up_prob_3d": float(probabilities["3"]["up"]),
                "drawdown_prob_3d": float(probabilities["3"]["drawdown"]),
                "up_prob_5d": float(probabilities["5"]["up"]),
                "drawdown_prob_5d": float(probabilities["5"]["drawdown"]),
                "macd_signal": row["macd_volume_signal"],
                "quantile_signal": row["quantile_signal"],
                "dual_ma_signal": row["dual_ma_signal"],
                "forward_return": ret,
                "win": int(ret > 0),
            }
            trade_rows.append(trade)
            monthly_trades.append(trade)

        monthly_returns = [trade["forward_return"] for trade in monthly_trades]
        monthly_rows.append({
            "month": month,
            "opportunity_count": len(signal_dates),
            "qualified_opportunity_count": qualified_opportunities,
            "trade_count": len(monthly_trades),
            "win_count": sum(trade["win"] for trade in monthly_trades),
            "loss_count": sum(not trade["win"] for trade in monthly_trades),
            "avg_return": float(np.mean(monthly_returns)) if monthly_returns else np.nan,
        })

    trades = pd.DataFrame(trade_rows)
    monthly = pd.DataFrame(monthly_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output_dir / "industry_trend_backtest_trades.csv", index=False, encoding="utf-8-sig")
    monthly.to_csv(output_dir / "industry_trend_backtest_monthly.csv", index=False, encoding="utf-8-sig")
    write_markdown(
        output_dir / "industry_trend_backtest.md",
        trades,
        monthly,
        months=months,
        opportunities_per_month=opportunities_per_month,
        top_n=top_n,
        up_3d=up_3d,
        up_5d=up_5d,
        max_drawdown=max_drawdown,
        hold_days=hold_days,
        max_trades=max_trades,
    )

    valid = trades["forward_return"] if not trades.empty else pd.Series(dtype=float)
    return {
        "months": len(monthly),
        "opportunities": int(monthly["opportunity_count"].sum()),
        "trades": len(trades),
        "win_rate": float((valid > 0).mean()) if not valid.empty else np.nan,
        "avg_return": float(valid.mean()) if not valid.empty else np.nan,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="industry trend multi-opportunity accuracy backtest")
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--opportunities-per-month", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--up-3d", type=float, default=0.75)
    parser.add_argument("--up-5d", type=float, default=0.70)
    parser.add_argument("--max-drawdown", type=float, default=0.30)
    parser.add_argument("--hold-days", type=int, default=5)
    parser.add_argument("--max-trades", type=int, default=50)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    result = run_backtest(
        months=args.months,
        opportunities_per_month=args.opportunities_per_month,
        top_n=args.top_n,
        up_3d=args.up_3d,
        up_5d=args.up_5d,
        max_drawdown=args.max_drawdown,
        hold_days=args.hold_days,
        max_trades=args.max_trades,
        output_dir=args.output_dir,
    )
    print(
        "[industry-trend-backtest] "
        f"months={result['months']} opportunities={result['opportunities']} trades={result['trades']} "
        f"win_rate={result['win_rate']:.2%} avg_return={result['avg_return']:.2%}"
    )


if __name__ == "__main__":
    main()
