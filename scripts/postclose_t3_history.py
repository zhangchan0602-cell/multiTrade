#!/usr/bin/env python3
"""按盘后版规则生成 T+5 口径历史回测结果。"""

from __future__ import annotations

import argparse
import os
from datetime import datetime

import numpy as np
import pandas as pd

from screen_common import OUTPUT_DIR, fetch_trade_cal_dates, get_latest_trade_date
from short_screen import DEFAULT_KLINE_CANDIDATE_LIMIT
from strategy_backtest import run_portfolio_for_strategy

OUTPUT_TRADES_CSV = OUTPUT_DIR / "short_t5_history_trades.csv"
OUTPUT_EQUITY_CSV = OUTPUT_DIR / "short_t5_history_equity.csv"
OUTPUT_OPEN_CSV = OUTPUT_DIR / "short_t5_history_open_positions.csv"
OUTPUT_MD = OUTPUT_DIR / "short_t5_history.md"

CASH_PER_STOCK = 100_000.0
MAX_POSITIONS = 3
RETRACEMENT_PCT = 5.0
SHORT_TOP_N = 5

os.environ.setdefault("SHORT_KLINE_SOURCE", "tushare")
os.environ.setdefault("SHORT_KLINE_WORKERS", "1")
os.environ.setdefault("SHORT_KLINE_RETRIES", "2")
os.environ["SHORT_KLINE_CANDIDATE_LIMIT"] = str(DEFAULT_KLINE_CANDIDATE_LIMIT)
os.environ.setdefault("SHORT_QUOTE_ONLY_FALLBACK", "0")
os.environ.setdefault("TUSHARE_MIN_INTERVAL", "0.18")


def _discover_default_start_date() -> str:
    history_root = OUTPUT_DIR / "history" / "short"
    if history_root.exists():
        dates = sorted(path.name for path in history_root.iterdir() if path.is_dir())
        if dates:
            return dates[0].replace("-", "")
    latest = pd.to_datetime(get_latest_trade_date(), format="%Y%m%d", errors="coerce")
    if pd.isna(latest):
        return datetime.now().strftime("%Y%m%d")
    return (latest - pd.Timedelta(days=90)).strftime("%Y%m%d")


def _resolve_dates(start_date: str, market_end_date: str) -> tuple[list[str], list[str], str]:
    warmup_start = (pd.to_datetime(start_date, format="%Y%m%d") - pd.Timedelta(days=450)).strftime("%Y%m%d")
    all_dates = fetch_trade_cal_dates(warmup_start, market_end_date)
    if len(all_dates) < 6:
        raise SystemExit("交易日不足 6 天，无法生成 T+5 回测")

    signal_end_date = all_dates[-6]
    signal_dates = [day for day in all_dates if start_date <= day <= signal_end_date]
    if not signal_dates:
        raise SystemExit("信号日期为空，无法回测")
    return all_dates, signal_dates, signal_end_date


def _format_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):+.2f}%"


def _format_money(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.2f}"


def _compute_profit(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    buy_amount = pd.to_numeric(trades.get("buy_amount"), errors="coerce")
    sell_amount = pd.to_numeric(trades.get("sell_amount"), errors="coerce")
    profit = sell_amount - buy_amount
    return profit.fillna(0.0)


def _render_markdown(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    open_positions: pd.DataFrame,
    signal_start: str,
    signal_end: str,
    market_end: str,
    generated_at: datetime,
) -> str:
    realized_profit = _compute_profit(trades)
    trade_count = len(trades)
    win_rate = float(trades["win"].mean() * 100.0) if trade_count and "win" in trades.columns else np.nan
    avg_ret = float(pd.to_numeric(trades.get("ret_pct"), errors="coerce").mean()) if trade_count else np.nan
    final_equity = float(pd.to_numeric(equity["total_equity"], errors="coerce").iloc[-1]) if not equity.empty else np.nan
    if not equity.empty:
        curve = pd.to_numeric(equity["total_equity"], errors="coerce")
        max_drawdown = float((curve / curve.cummax() - 1.0).min() * 100.0)
    else:
        max_drawdown = np.nan
    open_count = len(open_positions)
    unrealized_profit = float(pd.to_numeric(open_positions.get("market_value"), errors="coerce").fillna(0.0).sum() - pd.to_numeric(open_positions.get("buy_amount"), errors="coerce").fillna(0.0).sum()) if open_count else 0.0

    lines = [
        "# 短线多因子-盘后版 T+5 历史回测",
        "",
        f"- 生成时间: {generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 信号区间: {signal_start[:4]}-{signal_start[4:6]}-{signal_start[6:]} 至 {signal_end[:4]}-{signal_end[4:6]}-{signal_end[6:]}",
        f"- 行情截止: {market_end[:4]}-{market_end[4:6]}-{market_end[6:]}",
        "- T+5口径: 仅纳入买入后至少已有 5 个交易日行情可供观察的信号日；个股实际卖出仍按规则触发，不设置最大持有期限",
        f"- 单票预算: {CASH_PER_STOCK:,.0f} 元",
        f"- 最大持仓数: {MAX_POSITIONS}",
        "- 买卖数量: 整百股",
        "- 卖出规则: 涨停即出；未涨停时后续单日回撤 5% 即出；跌破 5 日线止损",
        "- 买入规则: 每个信号日按当前盘后版 Top5 顺序尝试买入，受现金与最大持仓数限制",
        f"- 已平仓笔数: {trade_count}",
        f"- 胜率: {f'{win_rate:.2f}%' if pd.notna(win_rate) else '-'}",
        f"- 平均收益: {_format_pct(avg_ret)}",
        f"- 已实现盈亏: {_format_money(realized_profit.sum())} 元",
        f"- 最终权益: {_format_money(final_equity)} 元",
        f"- 最大回撤: {_format_pct(max_drawdown)}",
        f"- 未平仓笔数: {open_count}",
        f"- 未平仓浮盈亏: {_format_money(unrealized_profit)} 元",
        "",
        "## 输出文件",
        "",
        "- `docs/list/short_t5_history_trades.csv`",
        "- `docs/list/short_t5_history_equity.csv`",
        "- `docs/list/short_t5_history_open_positions.csv`",
        "- `docs/list/short_t5_history.md`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay postclose history with T+5 eligibility and rule-based exits")
    parser.add_argument("--start-date", default=None, help="YYYYMMDD")
    parser.add_argument("--end-date", default=None, help="YYYYMMDD，默认最新交易日")
    args = parser.parse_args()

    start_date = args.start_date or _discover_default_start_date()
    market_end_date = args.end_date or get_latest_trade_date()
    all_dates, signal_dates, signal_end_date = _resolve_dates(start_date, market_end_date)

    try:
        result = run_portfolio_for_strategy(
            "short",
            all_dates=all_dates,
            signal_dates=signal_dates,
            top_n=SHORT_TOP_N,
            cash_per_stock=CASH_PER_STOCK,
            max_positions=MAX_POSITIONS,
            retracement_pct=RETRACEMENT_PCT,
        )
    except RuntimeError as exc:
        if "missing TUSHARE_TOKEN" in str(exc):
            raise SystemExit("missing TUSHARE_TOKEN: 请先导出环境变量，或在项目根目录创建 .env 后再运行 npm run backtest:short")
        raise

    trades = result.get("trades", pd.DataFrame())
    equity = result.get("equity", pd.DataFrame())
    open_positions = result.get("open_positions", pd.DataFrame())

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    trades.to_csv(OUTPUT_TRADES_CSV, index=False, encoding="utf-8-sig")
    equity.to_csv(OUTPUT_EQUITY_CSV, index=False, encoding="utf-8-sig")
    open_positions.to_csv(OUTPUT_OPEN_CSV, index=False, encoding="utf-8-sig")
    OUTPUT_MD.write_text(
        _render_markdown(
            trades,
            equity,
            open_positions,
            signal_start=signal_dates[0],
            signal_end=signal_end_date,
            market_end=market_end_date,
            generated_at=datetime.now(),
        ),
        encoding="utf-8",
    )
    print(f"[history-t5] wrote {OUTPUT_TRADES_CSV}")
    print(f"[history-t5] wrote {OUTPUT_EQUITY_CSV}")
    print(f"[history-t5] wrote {OUTPUT_OPEN_CSV}")
    print(f"[history-t5] wrote {OUTPUT_MD}")


if __name__ == "__main__":
    main()