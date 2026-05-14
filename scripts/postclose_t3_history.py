#!/usr/bin/env python3
"""按当前盘后版逻辑回放历史交易日 Top5，并汇总次日参考买入、T+3 结算收益。"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from screen_common import OUTPUT_DIR, fetch_daily_snapshot, fetch_trade_cal_dates, get_latest_trade_date, ts_code_to_code
from short_screen import DEFAULT_MODEL_NAME, SHORT_TOP_N, run_screen

OUTPUT_CSV = OUTPUT_DIR / "short_t3_history.csv"
OUTPUT_MD = OUTPUT_DIR / "short_t3_history.md"

os.environ["SHORT_KLINE_SOURCE"] = "tushare"
os.environ["SHORT_KLINE_WORKERS"] = "1"
os.environ["SHORT_KLINE_CANDIDATE_LIMIT"] = "80"
os.environ["TUSHARE_MIN_INTERVAL"] = "0.18"


def _discover_default_start_date() -> str:
    history_root = OUTPUT_DIR / "history" / "short"
    if history_root.exists():
        dates = sorted(path.name for path in history_root.iterdir() if path.is_dir())
        if dates:
            return dates[0].replace("-", "")
    latest = pd.to_datetime(get_latest_trade_date(), format="%Y%m%d", errors="coerce")
    if pd.isna(latest):
        return datetime.now().strftime("%Y%m%d")
    return (latest - pd.Timedelta(days=45)).strftime("%Y%m%d")


def _resolve_signal_dates(start_date: str, end_date: str) -> list[str]:
    trade_dates = [d for d in fetch_trade_cal_dates(start_date, end_date) if not fetch_daily_snapshot(d).empty]
    return trade_dates


def _build_close_map(trade_date: str) -> dict[str, float]:
    daily = fetch_daily_snapshot(trade_date)
    if daily.empty:
        return {}
    daily = daily[["ts_code", "close"]].copy()
    daily["code"] = daily["ts_code"].map(ts_code_to_code)
    daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
    daily = daily.dropna(subset=["code", "close"])
    return dict(zip(daily["code"], daily["close"]))


def _format_pick_returns(rows: list[dict]) -> str:
    parts = []
    for row in rows:
        ret = row.get("return_pct")
        if ret is None or pd.isna(ret):
            parts.append(f"{row['code']}(NA)")
            continue
        parts.append(f"{row['code']}({ret:+.2f}%)")
    return " / ".join(parts)


def _render_markdown(df: pd.DataFrame, start_date: str, end_date: str, generated_at: datetime) -> str:
    lines = [
        "# 短线多因子-盘后版 历史 Top5 T+3 回放",
        "",
        f"- 生成时间: {generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 回放区间: {start_date[:4]}-{start_date[4:6]}-{start_date[6:]} 至 {end_date[:4]}-{end_date[4:6]}-{end_date[6:]}",
        "- 评分口径: 使用当前盘后版打分与过滤逻辑，对每个信号日重新计算当日 Top5",
        "- 收益口径: 以信号日收盘价作为次日参考买入价，买入日为下一交易日，结算日为买入日后第 3 个交易日收盘",
        "- 组合口径: 对当日 Top5 按等权统计平均收益，不含手续费、滑点和仓位约束",
        "",
        "| 信号日 | 买入日 | 结算日 | Top5数 | 平均收益率 | 胜率 | 代码 | 单票收益 |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]

    for _, row in df.iterrows():
        lines.append(
            "| {signal_date} | {buy_date} | {settle_date} | {pick_count} | {avg_return_pct} | {win_rate_pct} | {top5_codes} | {pick_returns} |".format(
                signal_date=row["signal_date"],
                buy_date=row["buy_date"],
                settle_date=row["settle_date"],
                pick_count=int(row["pick_count"]),
                avg_return_pct=f"{float(row['avg_return_pct']):+.2f}%" if pd.notna(row["avg_return_pct"]) else "-",
                win_rate_pct=f"{float(row['win_rate_pct']):.0f}%" if pd.notna(row["win_rate_pct"]) else "-",
                top5_codes=row["top5_codes"] or "-",
                pick_returns=row["pick_returns"] or "-",
            )
        )
    lines.append("")
    return "\n".join(lines)


def build_history_rows(signal_dates: list[str]) -> pd.DataFrame:
    rows = []
    if len(signal_dates) < 5:
        return pd.DataFrame()

    settle_close_cache: dict[str, dict[str, float]] = {}
    for idx in range(len(signal_dates) - 4):
        signal_trade_date = signal_dates[idx]
        buy_trade_date = signal_dates[idx + 1]
        settle_trade_date = signal_dates[idx + 4]
        signal_ts = pd.to_datetime(signal_trade_date, format="%Y%m%d", errors="coerce") + pd.Timedelta(hours=15, minutes=30)

        result = run_screen(
            model_name=DEFAULT_MODEL_NAME,
            output_stem="short",
            _mode="postclose",
            run_ts=signal_ts.to_pydatetime(),
            trade_date=signal_trade_date,
            persist_outputs=False,
            copy_history=False,
        )
        scored = result["scored"].head(SHORT_TOP_N).copy()
        if settle_trade_date not in settle_close_cache:
            settle_close_cache[settle_trade_date] = _build_close_map(settle_trade_date)
        settle_close_map = settle_close_cache[settle_trade_date]

        pick_rows = []
        for _, pick in scored.iterrows():
            code = str(pick.get("code") or "").zfill(6)
            buy_ref_close = pd.to_numeric(pick.get("close"), errors="coerce")
            settle_close = settle_close_map.get(code)
            return_pct = None
            if pd.notna(buy_ref_close) and buy_ref_close > 0 and settle_close is not None:
                return_pct = (float(settle_close) / float(buy_ref_close) - 1.0) * 100.0
            pick_rows.append(
                {
                    "code": code,
                    "name": str(pick.get("name") or ""),
                    "return_pct": return_pct,
                }
            )

        valid_returns = [row["return_pct"] for row in pick_rows if row["return_pct"] is not None and pd.notna(row["return_pct"])]
        pick_count = len(pick_rows)
        win_count = sum(1 for value in valid_returns if value > 0)
        loss_count = sum(1 for value in valid_returns if value <= 0)
        avg_return_pct = sum(valid_returns) / len(valid_returns) if valid_returns else None
        win_rate_pct = (win_count / len(valid_returns) * 100.0) if valid_returns else None

        rows.append(
            {
                "signal_date": f"{signal_trade_date[:4]}-{signal_trade_date[4:6]}-{signal_trade_date[6:]}",
                "buy_date": f"{buy_trade_date[:4]}-{buy_trade_date[4:6]}-{buy_trade_date[6:]}",
                "settle_date": f"{settle_trade_date[:4]}-{settle_trade_date[4:6]}-{settle_trade_date[6:]}",
                "pick_count": pick_count,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate_pct": win_rate_pct,
                "avg_return_pct": avg_return_pct,
                "top5_codes": " ".join(row["code"] for row in pick_rows),
                "top5_names": " / ".join(row["name"] for row in pick_rows),
                "pick_returns": _format_pick_returns(pick_rows),
            }
        )
        print(
            f"[history-t3] {signal_trade_date} -> buy {buy_trade_date}, settle {settle_trade_date}, picks={pick_count}, avg={avg_return_pct if avg_return_pct is not None else 'NA'}"
        )

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay postclose Top5 history with T+3 settlement")
    parser.add_argument("--start-date", default=None, help="YYYYMMDD")
    parser.add_argument("--end-date", default=None, help="YYYYMMDD")
    args = parser.parse_args()

    start_date = args.start_date or _discover_default_start_date()
    end_date = args.end_date or get_latest_trade_date()

    try:
        signal_dates = _resolve_signal_dates(start_date, end_date)
        history = build_history_rows(signal_dates)
    except RuntimeError as exc:
        if "missing TUSHARE_TOKEN" in str(exc):
            raise SystemExit("missing TUSHARE_TOKEN: 请先导出环境变量，或在项目根目录创建 .env 后再运行 npm run backtest:short")
        raise
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    history.to_csv(OUTPUT_CSV, index=False)
    OUTPUT_MD.write_text(_render_markdown(history, start_date, end_date, datetime.now()), encoding="utf-8")
    print(f"[history-t3] wrote {OUTPUT_CSV}")
    print(f"[history-t3] wrote {OUTPUT_MD}")


if __name__ == "__main__":
    main()