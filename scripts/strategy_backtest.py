#!/usr/bin/env python3
"""
策略统一历史回测。

原有模式支持策略：
  rps90  : 重新计算历史 RPS双90 Top5（同 rps90_backtest.py 框架）
  short  : 读取 docs/list/history/short/YYYY-MM-DD/ 已存档盘后版信号
    tail   : 读取 docs/list/history/tail/YYYY-MM-DD/ 及 runs/HHMM/ 已存档尾盘版信号
  all    : 三策略全部回测

原有模式止盈止损规则：
  止损   ：持仓价格跌至买入价 -8% 即出
  止盈1  ：当日涨停（主板 pct_chg >= 9.5%，创业板 pct_chg >= 19.5%）即出
  止盈2  ：当日跌幅 >= 5%（单日回撤）即出
  到期   ：最大持有 MAX_HOLD_DAYS 个交易日出局

组合回测模式（--portfolio）：
    - 按历史交易日用 Tushare 数据重跑策略，重新生成当日可买入标的
    - 单票预算 10 万，整百股买入卖出，同时最多持有 3 只
    - 不限制最长持有期限
    - 涨停即出；未涨停时单日回撤 5% 即出；跌破 5 日线止损

用法：
  python3 scripts/strategy_backtest.py [--strategy rps90|short|tail|all]
                                       [--stop-loss 8]
                                       [--max-hold 60]
                                       [--retracement 5]
                                       [--lookback 90]   # 仅 rps90 生效
                                       [--interval 5]    # 仅 rps90 生效
                                       [--top-n 5]

    python3 scripts/strategy_backtest.py --portfolio --strategy all --lookback 30
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_common import (
    OUTPUT_DIR,
    code_to_ts_code,
    fetch_daily_snapshot,
    fetch_stock_basic,
    fetch_trade_cal_dates,
    get_latest_trade_date,
    ts_code_to_code,
)
from leader_screen import KLINE_CANDIDATE_LIMIT as LEADER_DEFAULT_KLINE_CANDIDATE_LIMIT, run_leader_screen
from short_screen import (
    DEFAULT_MODEL_NAME as SHORT_MODEL_NAME,
    DEFAULT_OUTPUT_STEM as SHORT_OUTPUT_STEM,
    DEFAULT_TAIL_MODEL_NAME,
    DEFAULT_TAIL_OUTPUT_STEM,
    DEFAULT_TAIL_TRADE_TARGET_TEXT,
    run_screen as run_short_screen,
)

# ── 目录 ─────────────────────────────────────────────────────────────────────
HISTORY_DIR = Path(__file__).resolve().parent.parent / "docs" / "list" / "history"

# ── RPS双90 策略参数（与 rps90_screen.py / rps90_backtest.py 一致）──────────
RPS20_MIN    = 90.0
RPS90_MIN    = 90.0
PRICE_MIN    = 3.0
AMOUNT_MIN   = 30_000_000   # 元；daily.amount 单位千元，使用前 ×1000
CAL_DAYS_MIN = 90
RPS_TOP_N    = 5

# ── 快照内存缓存（含 pct_chg）────────────────────────────────────────────────
_snap_cache: Dict[str, pd.DataFrame] = {}
# rps90 信号计算专用缓存（仅含 close/amount，与 _snap_cache 独立）
_rps_snap_cache: Dict[str, pd.DataFrame] = {}


def _get_snap(trade_date: str) -> pd.DataFrame:
    """带内存缓存的行情快照，含 ts_code / open / close / pct_chg。"""
    if trade_date not in _snap_cache:
        df = fetch_daily_snapshot(trade_date)
        if df is None or df.empty:
            _snap_cache[trade_date] = pd.DataFrame(columns=["ts_code", "open", "high", "low", "close", "pct_chg"])
        else:
            for col in ("open", "high", "low", "close", "pct_chg"):
                if col not in df.columns:
                    df[col] = np.nan
                df[col] = pd.to_numeric(df[col], errors="coerce")
            _snap_cache[trade_date] = df[["ts_code", "open", "high", "low", "close", "pct_chg"]].copy()
    return _snap_cache[trade_date]


def _get_rps_snap(trade_date: str) -> pd.DataFrame:
    """rps90 信号计算用快照（含 ts_code / close / amount）。"""
    if trade_date not in _rps_snap_cache:
        df = fetch_daily_snapshot(trade_date)
        if df is None or df.empty:
            _rps_snap_cache[trade_date] = pd.DataFrame(columns=["ts_code", "close", "amount"])
        else:
            for col in ("close", "amount"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            _rps_snap_cache[trade_date] = df[["ts_code", "close", "amount"]].copy()
    return _rps_snap_cache[trade_date]


# ── 涨停判断 ─────────────────────────────────────────────────────────────────
def _is_limit_up(ts_code: str, pct_chg: float, gem_threshold: float, main_threshold: float) -> bool:
    code = ts_code_to_code(ts_code)
    threshold = gem_threshold if code.startswith("3") else main_threshold
    return pct_chg >= threshold


def _find_snap_row(trade_date: str, ts_code: str) -> Optional[pd.Series]:
    snap = _get_snap(trade_date)
    if snap.empty:
        return None
    row = snap[snap["ts_code"] == ts_code]
    if row.empty:
        return None
    return row.iloc[0]


def _close_on(trade_date: str, ts_code: str) -> Optional[float]:
    row = _find_snap_row(trade_date, ts_code)
    if row is None:
        return None
    close_px = pd.to_numeric(row.get("close"), errors="coerce")
    if pd.isna(close_px) or float(close_px) <= 0:
        return None
    return float(close_px)


def _ma5_on(trade_date: str, ts_code: str, all_dates: List[str], date_to_idx: Dict[str, int]) -> Optional[float]:
    idx = date_to_idx.get(trade_date)
    if idx is None or idx < 4:
        return None
    closes = []
    for day in all_dates[idx - 4: idx + 1]:
        close_px = _close_on(day, ts_code)
        if close_px is None:
            return None
        closes.append(close_px)
    return float(np.mean(closes)) if len(closes) == 5 else None


# ── 持仓模拟 ─────────────────────────────────────────────────────────────────
def simulate_exit(
    ts_code: str,
    entry_price: float,
    signal_date: str,          # YYYYMMDD 格式
    all_dates: List[str],
    stop_loss_pct: float = 8.0,
    retracement_pct: float = 5.0,
    max_hold: int = 60,
    gem_limit_up: float = 19.5,
    main_limit_up: float = 9.5,
) -> Dict:
    """
    从 signal_date 后第1个交易日（T+1）开始监控持仓，
    触发止损/止盈或到达最大持有日数后返回结果。

    返回 dict:
      exit_date, exit_price, exit_reason, hold_days, ret_pct, win
    """
    stop_ratio = 1.0 - stop_loss_pct / 100.0    # e.g. 0.92
    retracement_thr = -abs(retracement_pct)      # e.g. -5.0

    def _missing():
        return {
            "exit_date": None, "exit_price": None,
            "exit_reason": "no_data", "hold_days": 0,
            "ret_pct": np.nan, "win": None,
        }

    if entry_price <= 0 or pd.isna(entry_price):
        return _missing()

    try:
        sig_idx = all_dates.index(signal_date)
    except ValueError:
        return _missing()

    exit_px     = None
    exit_reason = "expired"
    exit_day    = max_hold

    for k in range(1, max_hold + 1):
        day_idx = sig_idx + k
        if day_idx >= len(all_dates):
            exit_day = k - 1
            break

        snap = _get_snap(all_dates[day_idx])
        if snap.empty:
            continue

        row = snap[snap["ts_code"] == ts_code]
        if row.empty:
            continue

        close_px = float(row["close"].iloc[0])
        pct_chg  = float(row["pct_chg"].iloc[0])

        if pd.isna(close_px) or pd.isna(pct_chg):
            continue

        # 1. 止损
        if close_px <= entry_price * stop_ratio:
            exit_px, exit_reason, exit_day = close_px, "stop_loss", k
            break

        # 2. 涨停止盈
        if _is_limit_up(ts_code, pct_chg, gem_limit_up, main_limit_up):
            exit_px, exit_reason, exit_day = close_px, "limit_up", k
            break

        # 3. 单日回撤止盈
        if pct_chg <= retracement_thr:
            exit_px, exit_reason, exit_day = close_px, "retracement", k
            break

        exit_px = close_px   # 记录当前持仓最新价（未触发，继续持有）

    actual_exit_date = (
        all_dates[sig_idx + exit_day]
        if (sig_idx + exit_day) < len(all_dates)
        else all_dates[-1]
    )

    ret = (exit_px / entry_price - 1.0) * 100.0 if (exit_px is not None and not pd.isna(exit_px)) else np.nan

    return {
        "exit_date"  : actual_exit_date,
        "exit_price" : round(exit_px, 2) if exit_px is not None else None,
        "exit_reason": exit_reason,
        "hold_days"  : exit_day,
        "ret_pct"    : round(ret, 2) if not np.isnan(ret) else None,
        "win"        : (1 if ret > 0 else 0) if not np.isnan(ret) else None,
    }


# ── RPS双90 信号重算 ─────────────────────────────────────────────────────────
def _nth_day_back(dates: List[str], n: int) -> Optional[str]:
    idx = -(n + 1)
    return dates[idx] if abs(idx) <= len(dates) else None


def _compute_rps90_top5(signal_date: str, window: List[str], basic_inv: pd.DataFrame) -> pd.DataFrame:
    """重算 RPS双90 Top5；逻辑与 rps90_backtest.py::_compute_top5 保持一致。"""
    date_20d = _nth_day_back(window, 20)
    date_90d = _nth_day_back(window, 90)
    if not date_20d or not date_90d:
        return pd.DataFrame()

    snap_t   = _get_rps_snap(signal_date)
    snap_20d = _get_rps_snap(date_20d)
    snap_90d = _get_rps_snap(date_90d)

    if snap_t.empty or snap_20d.empty or snap_90d.empty:
        return pd.DataFrame()

    prices = (
        snap_t.rename(columns={"close": "close_t", "amount": "amount_t"})
        .merge(snap_20d[["ts_code", "close"]].rename(columns={"close": "close_20d"}), on="ts_code", how="inner")
        .merge(snap_90d[["ts_code", "close"]].rename(columns={"close": "close_90d"}), on="ts_code", how="inner")
    )
    prices = prices.dropna(subset=["close_t", "close_20d", "close_90d"])
    prices = prices[
        (prices["close_t"] > 0) & (prices["close_20d"] > 0) & (prices["close_90d"] > 0)
    ].copy()

    prices["code6"] = prices["ts_code"].map(ts_code_to_code)
    prices = prices[~prices["code6"].str.startswith("688")].copy()

    if prices.empty:
        return pd.DataFrame()

    prices["ret_20d"] = prices["close_t"] / prices["close_20d"] - 1.0
    prices["ret_90d"] = prices["close_t"] / prices["close_90d"] - 1.0
    prices["rps20"] = prices["ret_20d"].rank(pct=True, ascending=True, na_option="bottom") * 100.0
    prices["rps90"] = prices["ret_90d"].rank(pct=True, ascending=True, na_option="bottom") * 100.0

    df = prices.merge(
        basic_inv[["secucode", "name", "industry", "listing_date"]],
        left_on="ts_code", right_on="secucode", how="inner"
    )

    df["is_st"] = df["name"].str.contains("ST", na=False)
    df["listed_days"] = (
        pd.Timestamp(signal_date) - pd.to_datetime(df["listing_date"], errors="coerce")
    ).dt.days.fillna(0)
    df["amount_yuan"] = df["amount_t"] * 1_000.0

    df = df[
        (~df["is_st"]) &
        (df["listed_days"] >= CAL_DAYS_MIN) &
        (df["close_t"] >= PRICE_MIN) &
        (df["amount_yuan"] >= AMOUNT_MIN)
    ].copy()

    df = df[(df["rps20"] >= RPS20_MIN) & (df["rps90"] >= RPS90_MIN)].copy()
    if df.empty:
        return pd.DataFrame()

    df["rps_score"] = 0.40 * df["rps20"] + 0.60 * df["rps90"]
    amount_rank = df["amount_yuan"].rank(pct=True, ascending=True, na_option="bottom")
    df["composite_score"] = df["rps_score"] + amount_rank * 5.0
    df = df.sort_values("composite_score", ascending=False).head(RPS_TOP_N).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)

    return df[["rank", "ts_code", "code6", "name", "industry", "rps20", "rps90", "close_t"]].rename(
        columns={"code6": "code", "close_t": "entry_close"}
    )


# ── 日期格式工具 ─────────────────────────────────────────────────────────────
def _normalize_date(d: str) -> str:
    """将 YYYY-MM-DD 统一为 YYYYMMDD；已是 YYYYMMDD 则原样返回。"""
    return str(d).replace("-", "")


# ── 信号加载器（short / tail 从历史文件读取）─────────────────────────────────
def _load_csv_signals(strategy: str, csv_path: Path, signal_date: str, top_n: int) -> List[Dict]:
    """从已存档筛选 CSV 中提取前 top_n 条信号。"""
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        print(f"  [警告] 读取失败 {csv_path}: {e}")
        return []

    if df.empty:
        return []

    if "rank" in df.columns:
        df = df.sort_values("rank").head(top_n)
    else:
        df = df.head(top_n)

    close_col = next((c for c in ("close", "close_today") if c in df.columns), None)

    records = []
    for _, row in df.iterrows():
        code = str(row.get("code", "")).zfill(6)
        if not code or code == "000000":
            continue
        try:
            ts = code_to_ts_code(code)
        except ValueError:
            continue
        entry_close = float(row[close_col]) if (close_col and pd.notna(row.get(close_col))) else None
        records.append({
            "strategy"   : strategy,
            "signal_date": signal_date,
            "rank"       : int(row["rank"]) if "rank" in row else 1,
            "code"       : code,
            "ts_code"    : ts,
            "name"       : str(row.get("name", "")),
            "industry"   : str(row.get("industry", "")),
            "entry_close": entry_close,
        })
    return records


def load_short_signals(top_n: int = 5) -> pd.DataFrame:
    """读取 docs/list/history/short/YYYY-MM-DD/short_top*.csv。"""
    short_dir = HISTORY_DIR / "short"
    records = []
    for date_dir in sorted(short_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        # 优先 top5，没有就用 top20
        csv = date_dir / "short_top5.csv"
        if not csv.exists():
            csv = date_dir / "short_top20.csv"
        if not csv.exists():
            continue
        signal_date = _normalize_date(date_dir.name)
        records.extend(_load_csv_signals("short", csv, signal_date, top_n))
    return pd.DataFrame(records) if records else pd.DataFrame()


def load_tail_signals(top_n: int = 5) -> pd.DataFrame:
    """读取 docs/list/history/tail/YYYY-MM-DD/ 及旧版扁平 tail_*_top20.csv。"""
    records = []
    tail_dir = HISTORY_DIR / "tail"
    if tail_dir.exists():
        for date_dir in sorted(tail_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            signal_date = _normalize_date(date_dir.name)
            if not signal_date:
                continue
            direct_csv = date_dir / "tail_top5.csv"
            if not direct_csv.exists():
                direct_csv = date_dir / "tail_top20.csv"
            if direct_csv.exists():
                records.extend(_load_csv_signals("tail", direct_csv, signal_date, top_n))
                continue

            runs_dir = date_dir / "runs"
            if not runs_dir.exists():
                continue
            for run_dir in sorted(runs_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                csv_path = run_dir / "tail_top5.csv"
                if not csv_path.exists():
                    csv_path = run_dir / "tail_top20.csv"
                if not csv_path.exists():
                    continue
                records.extend(_load_csv_signals("tail", csv_path, signal_date, top_n))

    # 根目录下的旧格式文件
    for csv_path in sorted(HISTORY_DIR.glob("tail_*_top20.csv")):
        stem = csv_path.stem  # e.g. tail_20260514-1351_top20
        parts = stem.split("_")
        signal_date = parts[1].split("-")[0] if len(parts) > 1 else ""
        if not signal_date:
            continue
        records.extend(_load_csv_signals("tail", csv_path, signal_date, top_n))
    return pd.DataFrame(records) if records else pd.DataFrame()


def load_leader_signals(top_n: int = 5) -> pd.DataFrame:
    """读取 docs/list/history/leader/YYYYMMDD/leader_top*.csv。"""
    leader_dir = HISTORY_DIR / "leader"
    records = []
    if not leader_dir.exists():
        return pd.DataFrame()
    for date_dir in sorted(leader_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        csv = date_dir / "leader_top5.csv"
        if not csv.exists():
            csv = date_dir / "leader_top20.csv"
        if not csv.exists():
            continue
        signal_date = _normalize_date(date_dir.name)
        records.extend(_load_csv_signals("leader", csv, signal_date, top_n))
    return pd.DataFrame(records) if records else pd.DataFrame()


# ── 组合级统一回测（按历史日重跑策略）───────────────────────────────────────
PORTFOLIO_LABELS = {
    "rps90": "RPS双90",
    "short": "短线盘后版",
    "tail": "短线尾盘版",
    "leader": "龙头抱团",
}


def _configure_portfolio_env(kline_candidate_limit: int, leader_kline_candidate_limit: int) -> None:
    os.environ.setdefault("TUSHARE_MIN_INTERVAL", "0.18")
    os.environ.setdefault("SHORT_KLINE_SOURCE", "tushare")
    os.environ.setdefault("SHORT_KLINE_WORKERS", "1")
    os.environ.setdefault("SHORT_KLINE_RETRIES", "2")
    os.environ["SHORT_KLINE_CANDIDATE_LIMIT"] = str(max(1, kline_candidate_limit))
    os.environ["SHORT_TAIL_ALLOW_DAILY_FALLBACK"] = "1"
    os.environ["LEADER_KLINE_CANDIDATE_LIMIT"] = str(max(1, leader_kline_candidate_limit))


def _signal_run_ts(trade_date: str) -> datetime:
    ts = pd.to_datetime(trade_date, format="%Y%m%d", errors="coerce")
    if pd.isna(ts):
        return datetime.now()
    return (ts + pd.Timedelta(hours=15, minutes=30)).to_pydatetime()


def _signals_from_scored(strategy: str, signal_date: str, scored: pd.DataFrame, top_n: int) -> List[Dict]:
    if scored is None or scored.empty:
        return []
    df = scored.copy()
    if "rank" in df.columns:
        df = df.sort_values("rank")
    df = df.head(top_n).reset_index(drop=True)

    close_col = next((c for c in ("entry_close", "close", "close_today", "close_t") if c in df.columns), None)
    records = []
    for idx, row in df.iterrows():
        ts_code = str(row.get("ts_code") or row.get("secucode") or "").strip()
        code = str(row.get("code") or "").strip()
        if not code and ts_code:
            code = ts_code_to_code(ts_code)
        code = code.zfill(6)
        if not code or code == "000000":
            continue
        if not ts_code:
            try:
                ts_code = code_to_ts_code(code)
            except ValueError:
                continue
        entry_close = None
        if close_col:
            value = pd.to_numeric(row.get(close_col), errors="coerce")
            if pd.notna(value) and float(value) > 0:
                entry_close = float(value)
        records.append({
            "strategy": strategy,
            "signal_date": signal_date,
            "rank": int(row.get("rank", idx + 1)) if pd.notna(row.get("rank", np.nan)) else idx + 1,
            "code": code,
            "ts_code": ts_code,
            "name": str(row.get("name") or code),
            "industry": str(row.get("industry") or ""),
            "entry_close": entry_close,
        })
    return records


def _compute_strategy_signals(
    strategy: str,
    trade_date: str,
    all_dates: List[str],
    date_to_idx: Dict[str, int],
    top_n: int,
    basic_inv: Optional[pd.DataFrame] = None,
) -> List[Dict]:
    try:
        if strategy == "rps90":
            idx = date_to_idx.get(trade_date)
            if idx is None or idx < 95 or basic_inv is None:
                return []
            window = all_dates[max(0, idx - 120): idx + 1]
            scored = _compute_rps90_top5(trade_date, window, basic_inv)
            return _signals_from_scored(strategy, trade_date, scored, top_n)

        if strategy == "short":
            result = run_short_screen(
                model_name=SHORT_MODEL_NAME,
                output_stem=SHORT_OUTPUT_STEM,
                _mode="postclose",
                run_ts=_signal_run_ts(trade_date),
                trade_date=trade_date,
                persist_outputs=False,
                copy_history=False,
            )
            return _signals_from_scored(strategy, trade_date, result.get("scored", pd.DataFrame()), top_n)

        if strategy == "tail":
            result = run_short_screen(
                model_name=DEFAULT_TAIL_MODEL_NAME,
                output_stem=DEFAULT_TAIL_OUTPUT_STEM,
                trade_target_text=DEFAULT_TAIL_TRADE_TARGET_TEXT,
                _mode="tail",
                run_ts=_signal_run_ts(trade_date),
                trade_date=trade_date,
                persist_outputs=False,
                copy_history=False,
            )
            return _signals_from_scored(strategy, trade_date, result.get("scored", pd.DataFrame()), top_n)

        if strategy == "leader":
            result = run_leader_screen(
                trade_date=trade_date,
                run_ts=_signal_run_ts(trade_date),
                persist_outputs=False,
                copy_history=False,
            )
            return _signals_from_scored(strategy, trade_date, result.get("scored", pd.DataFrame()), top_n)
    except Exception as exc:
        print(f"  [{strategy}] {trade_date} 信号生成失败: {exc}")
        return []
    return []


def run_portfolio_for_strategy(
    strategy: str,
    all_dates: List[str],
    signal_dates: List[str],
    top_n: int = 5,
    cash_per_stock: float = 100_000.0,
    max_positions: int = 3,
    retracement_pct: float = 5.0,
    gem_limit_up: float = 19.5,
    main_limit_up: float = 9.5,
) -> Dict[str, pd.DataFrame]:
    date_to_idx = {day: idx for idx, day in enumerate(all_dates)}
    cash = float(cash_per_stock * max_positions)
    positions: List[Dict] = []
    trades: List[Dict] = []
    equity_rows: List[Dict] = []

    basic_inv = None
    if strategy == "rps90":
        basic = fetch_stock_basic()
        valid_markets = {"主板", "创业板"}
        basic_inv = basic[basic["market"].isin(valid_markets)][
            ["secucode", "name", "industry", "listing_date"]
        ].copy()

    print(f"\n[组合回测] {PORTFOLIO_LABELS.get(strategy, strategy)}  信号日={len(signal_dates)}")
    for trade_date in signal_dates:
        # 先处理已有持仓卖出，再用当日新信号补仓。
        next_positions: List[Dict] = []
        for position in positions:
            row = _find_snap_row(trade_date, position["ts_code"])
            if row is None:
                next_positions.append(position)
                continue
            close_px = pd.to_numeric(row.get("close"), errors="coerce")
            pct_chg = pd.to_numeric(row.get("pct_chg"), errors="coerce")
            if pd.isna(close_px) or float(close_px) <= 0 or pd.isna(pct_chg):
                next_positions.append(position)
                continue
            close_px = float(close_px)
            pct_chg = float(pct_chg)
            ma5 = _ma5_on(trade_date, position["ts_code"], all_dates, date_to_idx)

            exit_reason = None
            if _is_limit_up(position["ts_code"], pct_chg, gem_limit_up, main_limit_up):
                exit_reason = "limit_up"
            elif pct_chg <= -abs(retracement_pct):
                exit_reason = "retracement_5pct"
            elif ma5 is not None and close_px < ma5:
                exit_reason = "ma5_stop"

            if exit_reason is None:
                next_positions.append(position)
                continue

            sell_amount = close_px * position["shares"]
            cash += sell_amount
            ret_pct = (close_px / position["entry_price"] - 1.0) * 100.0
            hold_days = max(0, date_to_idx[trade_date] - date_to_idx.get(position["entry_date"], date_to_idx[trade_date]))
            trades.append({
                "strategy": strategy,
                "code": position["code"],
                "ts_code": position["ts_code"],
                "name": position["name"],
                "industry": position["industry"],
                "rank": position["rank"],
                "entry_date": position["entry_date"],
                "entry_price": round(position["entry_price"], 4),
                "shares": position["shares"],
                "buy_amount": round(position["buy_amount"], 2),
                "exit_date": trade_date,
                "exit_price": round(close_px, 4),
                "sell_amount": round(sell_amount, 2),
                "exit_reason": exit_reason,
                "hold_days": hold_days,
                "ma5": round(ma5, 4) if ma5 is not None else None,
                "pct_chg": round(pct_chg, 4),
                "ret_pct": round(ret_pct, 4),
                "win": 1 if ret_pct > 0 else 0,
            })

        positions = next_positions

        if len(positions) < max_positions:
            signals = _compute_strategy_signals(strategy, trade_date, all_dates, date_to_idx, top_n, basic_inv)
            held_codes = {position["code"] for position in positions}
            for signal in signals:
                if len(positions) >= max_positions:
                    break
                if signal["code"] in held_codes:
                    continue
                row = _find_snap_row(trade_date, signal["ts_code"])
                close_px = None
                if row is not None:
                    close_px = pd.to_numeric(row.get("close"), errors="coerce")
                if close_px is None or pd.isna(close_px) or float(close_px) <= 0:
                    close_px = signal.get("entry_close")
                if close_px is None or pd.isna(close_px) or float(close_px) <= 0:
                    continue
                close_px = float(close_px)
                budget = min(float(cash_per_stock), cash)
                shares = int(budget // close_px // 100) * 100
                if shares < 100:
                    continue
                buy_amount = shares * close_px
                cash -= buy_amount
                positions.append({
                    "strategy": strategy,
                    "code": signal["code"],
                    "ts_code": signal["ts_code"],
                    "name": signal["name"],
                    "industry": signal["industry"],
                    "rank": signal["rank"],
                    "entry_date": trade_date,
                    "entry_price": close_px,
                    "shares": shares,
                    "buy_amount": buy_amount,
                })
                held_codes.add(signal["code"])

        position_value = 0.0
        for position in positions:
            close_px = _close_on(trade_date, position["ts_code"])
            if close_px is None:
                close_px = position["entry_price"]
            position_value += close_px * position["shares"]
        total_equity = cash + position_value
        equity_rows.append({
            "strategy": strategy,
            "trade_date": trade_date,
            "cash": round(cash, 2),
            "position_value": round(position_value, 2),
            "total_equity": round(total_equity, 2),
            "positions": len(positions),
        })
        if len(equity_rows) % 10 == 0 or trade_date == signal_dates[-1]:
            print(f"  {trade_date} equity={total_equity:.2f} cash={cash:.2f} positions={len(positions)} trades={len(trades)}")

    open_rows = []
    final_date = signal_dates[-1] if signal_dates else all_dates[-1]
    for position in positions:
        close_px = _close_on(final_date, position["ts_code"])
        if close_px is None:
            close_px = position["entry_price"]
        open_rows.append({
            **position,
            "last_date": final_date,
            "last_close": round(float(close_px), 4),
            "market_value": round(float(close_px) * position["shares"], 2),
            "unrealized_ret_pct": round((float(close_px) / position["entry_price"] - 1.0) * 100.0, 4),
        })

    return {
        "trades": pd.DataFrame(trades),
        "equity": pd.DataFrame(equity_rows),
        "open_positions": pd.DataFrame(open_rows),
    }


def _portfolio_summary_markdown(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    open_positions: pd.DataFrame,
    generated_at: datetime,
    signal_start: str,
    signal_end: str,
    cash_per_stock: float,
    max_positions: int,
) -> str:
    lines = [
        "# 统一组合历史回测汇总",
        "",
        f"- 生成时间: {generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 信号区间: {signal_start} 至 {signal_end}",
        f"- 单票预算: {cash_per_stock:,.0f} 元",
        f"- 最大持仓数: {max_positions}",
        "- 买卖数量: 整百股",
        "- 止盈: 涨停即出；未涨停时单日回撤 5% 即出",
        "- 止损: 跌破 5 日线即出",
        "- 持有期限: 不限制最长持有天数，回测结束时剩余持仓按市值展示",
        "",
        "| 策略 | 已平仓笔数 | 胜率 | 平均收益 | 最终权益 | 最大回撤 | 未平仓数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    strategies = sorted(set(equity["strategy"].tolist())) if not equity.empty else []
    for strategy in strategies:
        eq = equity[equity["strategy"] == strategy].copy()
        tr = trades[trades["strategy"] == strategy].copy() if not trades.empty else pd.DataFrame()
        op = open_positions[open_positions["strategy"] == strategy].copy() if not open_positions.empty else pd.DataFrame()
        final_equity = float(eq["total_equity"].iloc[-1]) if not eq.empty else 0.0
        if not eq.empty:
            curve = pd.to_numeric(eq["total_equity"], errors="coerce")
            drawdown = (curve / curve.cummax() - 1.0).min() * 100.0
        else:
            drawdown = np.nan
        if not tr.empty:
            win_rate = tr["win"].mean() * 100.0
            avg_ret = tr["ret_pct"].mean()
        else:
            win_rate = np.nan
            avg_ret = np.nan
        lines.append(
            "| {strategy} | {count} | {win_rate} | {avg_ret} | {final_equity} | {drawdown} | {open_count} |".format(
                strategy=PORTFOLIO_LABELS.get(strategy, strategy),
                count=len(tr),
                win_rate=f"{win_rate:.1f}%" if pd.notna(win_rate) else "-",
                avg_ret=f"{avg_ret:+.2f}%" if pd.notna(avg_ret) else "-",
                final_equity=f"{final_equity:,.2f}",
                drawdown=f"{drawdown:.2f}%" if pd.notna(drawdown) else "-",
                open_count=len(op),
            )
        )
    lines.append("")
    lines.extend([
        "## 输出文件",
        "",
        "- `docs/list/strategy_portfolio_backtest_trades.csv`",
        "- `docs/list/strategy_portfolio_backtest_equity.csv`",
        "- `docs/list/strategy_portfolio_backtest_open_positions.csv`",
        "- `docs/list/strategy_portfolio_backtest_summary.md`",
        "",
    ])
    return "\n".join(lines)


def run_portfolio_mode(args) -> None:
    _configure_portfolio_env(args.kline_candidate_limit, args.leader_kline_candidate_limit)
    end_date = args.end_date or get_latest_trade_date()
    if args.start_date:
        signal_start = args.start_date
        warmup_start = (pd.to_datetime(signal_start, format="%Y%m%d") - pd.Timedelta(days=450)).strftime("%Y%m%d")
    else:
        end_ts = pd.to_datetime(end_date, format="%Y%m%d")
        warmup_start = (end_ts - pd.Timedelta(days=700)).strftime("%Y%m%d")
        signal_start = None

    all_dates = fetch_trade_cal_dates(warmup_start, end_date)
    if not all_dates:
        raise SystemExit("交易日历为空，无法回测")
    if signal_start is None:
        first_idx = max(95, len(all_dates) - args.lookback)
        signal_start = all_dates[first_idx]
    signal_dates = [day for day in all_dates if signal_start <= day <= end_date]
    signal_dates = signal_dates[::max(1, args.signal_interval)]
    if not signal_dates:
        raise SystemExit("信号日期为空，无法回测")

    strategies = ["rps90", "short", "tail", "leader"] if args.strategy == "all" else [args.strategy]
    valid = {"rps90", "short", "tail", "leader"}
    strategies = [strategy for strategy in strategies if strategy in valid]
    if not strategies:
        raise SystemExit("未指定有效策略：rps90|short|tail|leader|all")

    print(f"[统一组合回测] 策略={','.join(strategies)}  日期={signal_dates[0]}~{signal_dates[-1]}  信号日={len(signal_dates)}")
    print(f"规则: 单票预算={args.cash_per_stock:.0f}  最大持仓={args.max_positions}  整百股  MA5止损")

    all_trades = []
    all_equity = []
    all_open = []
    for strategy in strategies:
        result = run_portfolio_for_strategy(
            strategy,
            all_dates=all_dates,
            signal_dates=signal_dates,
            top_n=args.top_n,
            cash_per_stock=args.cash_per_stock,
            max_positions=args.max_positions,
            retracement_pct=args.retracement,
            gem_limit_up=args.gem_limit_up,
            main_limit_up=args.main_limit_up,
        )
        if not result["trades"].empty:
            all_trades.append(result["trades"])
        if not result["equity"].empty:
            all_equity.append(result["equity"])
        if not result["open_positions"].empty:
            all_open.append(result["open_positions"])

    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    equity = pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame()
    open_positions = pd.concat(all_open, ignore_index=True) if all_open else pd.DataFrame()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    trades_path = OUTPUT_DIR / "strategy_portfolio_backtest_trades.csv"
    equity_path = OUTPUT_DIR / "strategy_portfolio_backtest_equity.csv"
    open_path = OUTPUT_DIR / "strategy_portfolio_backtest_open_positions.csv"
    summary_path = OUTPUT_DIR / "strategy_portfolio_backtest_summary.md"
    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    equity.to_csv(equity_path, index=False, encoding="utf-8-sig")
    open_positions.to_csv(open_path, index=False, encoding="utf-8-sig")
    summary_path.write_text(
        _portfolio_summary_markdown(
            trades,
            equity,
            open_positions,
            generated_at=datetime.now(),
            signal_start=signal_dates[0],
            signal_end=signal_dates[-1],
            cash_per_stock=args.cash_per_stock,
            max_positions=args.max_positions,
        ),
        encoding="utf-8",
    )
    print(f"\n组合回测输出: {summary_path}")


# ── 策略回测主函数 ─────────────────────────────────────────────────────────
def run_strategy_backtest(
    signals_df: pd.DataFrame,
    strategy_name: str,
    all_dates: List[str],
    stop_loss_pct: float = 8.0,
    retracement_pct: float = 5.0,
    max_hold: int = 60,
    gem_limit_up: float = 19.5,
    main_limit_up: float = 9.5,
) -> pd.DataFrame:
    """
    对给定的信号 DataFrame 运行持仓模拟，返回包含出局详情的完整结果 DataFrame。
    signals_df 必须含列: strategy, signal_date, rank, code, ts_code, name, industry, entry_close
    """
    if signals_df.empty:
        print(f"[{strategy_name}] 无历史信号，跳过。")
        return pd.DataFrame()

    records = []
    signal_dates = sorted(signals_df["signal_date"].unique())
    print(f"\n{'='*62}")
    print(f"策略: {strategy_name}  |  信号日期数: {len(signal_dates)}  |  信号总数: {len(signals_df)}")
    print(f"  止损: -{stop_loss_pct:.0f}%  止盈-涨停: 主板>={main_limit_up:.1f}% 创业板>={gem_limit_up:.1f}%")
    print(f"  止盈-回撤: 单日跌 >={retracement_pct:.0f}%  最大持有: {max_hold} 交易日")
    print(f"{'='*62}")

    for td in signal_dates:
        batch = signals_df[signals_df["signal_date"] == td]
        date_rets = []
        for _, row in batch.iterrows():
            entry_close = row["entry_close"]
            if entry_close is None or pd.isna(entry_close) or float(entry_close) <= 0:
                continue

            res = simulate_exit(
                ts_code=row["ts_code"],
                entry_price=float(entry_close),
                signal_date=td,
                all_dates=all_dates,
                stop_loss_pct=stop_loss_pct,
                retracement_pct=retracement_pct,
                max_hold=max_hold,
                gem_limit_up=gem_limit_up,
                main_limit_up=main_limit_up,
            )

            record = {
                "strategy"   : row["strategy"],
                "signal_date": td,
                "rank"       : row["rank"],
                "code"       : row["code"],
                "ts_code"    : row["ts_code"],
                "name"       : row["name"],
                "industry"   : row["industry"],
                "entry_close": round(float(entry_close), 2),
                **res,
            }
            records.append(record)
            if res["ret_pct"] is not None:
                date_rets.append(res["ret_pct"])

        if date_rets:
            n = len(date_rets)
            win_r = sum(1 for r in date_rets if r > 0) / n
            print(
                f"  {td}  n={n}  "
                f"均收益={np.mean(date_rets):+.1f}%  "
                f"胜率={win_r:.0%}"
            )
        else:
            print(f"  {td}  无有效收益数据")

    return pd.DataFrame(records)


# ── 汇总统计展示 ──────────────────────────────────────────────────────────────
def print_summary(df: pd.DataFrame, strategy_name: str) -> None:
    valid = df.dropna(subset=["ret_pct"])
    if valid.empty:
        print(f"\n[{strategy_name}] 无有效回测记录")
        return

    n_signals  = df["signal_date"].nunique()
    n_valid    = len(valid)
    win_rate   = valid["win"].mean()
    avg_ret    = valid["ret_pct"].mean()
    median_ret = valid["ret_pct"].median()
    std_ret    = valid["ret_pct"].std()

    print(f"\n{'─'*62}")
    print(f"【{strategy_name}】回测汇总")
    print(f"{'─'*62}")
    print(f"  信号日期数    : {n_signals}")
    print(f"  有效信号总数  : {n_valid}")
    print(f"  胜率(收益>0)  : {win_rate:.1%}")
    print(f"  平均收益      : {avg_ret:+.2f}%")
    print(f"  中位数收益    : {median_ret:+.2f}%")
    print(f"  收益标准差    : {std_ret:.2f}%")
    print(f"  最大单笔收益  : {valid['ret_pct'].max():+.2f}%")
    print(f"  最大单笔亏损  : {valid['ret_pct'].min():+.2f}%")
    print(f"  平均持有天数  : {valid['hold_days'].mean():.1f} 交易日")

    # 出局原因分布
    reason_labels = {
        "stop_loss"  : "止损(-8%)",
        "limit_up"   : "涨停止盈",
        "retracement": "回撤止盈(-5%)",
        "expired"    : "持有到期",
        "no_data"    : "数据缺失",
    }
    reason_stats = (
        valid.groupby("exit_reason")["ret_pct"]
        .agg(n="count", mean="mean", win=lambda x: (x > 0).mean())
        .reset_index()
        .sort_values("n", ascending=False)
    )
    print(f"\n  出局原因分布:")
    print(f"  {'原因':<14} {'次数':>5}  {'胜率':>6}  {'均收益':>9}")
    for _, row in reason_stats.iterrows():
        label = reason_labels.get(row["exit_reason"], row["exit_reason"])
        print(f"  {label:<14} {int(row['n']):>5}  {row['win']:>6.1%}  {row['mean']:>+9.2f}%")

    # 按排名分组
    ranks_present = sorted(valid["rank"].unique())
    if len(ranks_present) > 1:
        print(f"\n  按排名分组:")
        print(f"  {'Rank':>4}  {'n':>4}  {'胜率':>6}  {'均收益':>9}  {'中位数':>9}  {'均持有天':>8}")
        for rk in ranks_present:
            sub = valid[valid["rank"] == rk]
            if len(sub) > 0:
                print(
                    f"  {rk:>4}  {len(sub):>4}  "
                    f"{sub['win'].mean():>6.1%}  "
                    f"{sub['ret_pct'].mean():>+9.2f}%  "
                    f"{sub['ret_pct'].median():>+9.2f}%  "
                    f"{sub['hold_days'].mean():>8.1f}"
                )

    # 最佳/最差 5 笔
    cols = ["signal_date", "code", "name", "rank", "entry_close", "exit_close", "exit_reason",
            "hold_days", "ret_pct"]
    avail = [c for c in cols if c in valid.columns]
    print(f"\n  最佳 5 笔:")
    print(valid.nlargest(5, "ret_pct")[avail].to_string(index=False))
    print(f"\n  最差 5 笔:")
    print(valid.nsmallest(5, "ret_pct")[avail].to_string(index=False))


# ── 主入口 ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="三策略统一历史回测")
    parser.add_argument("--strategy",      default="all",   help="rps90|short|tail|leader|all（默认 all）")
    parser.add_argument("--portfolio",     action="store_true", default=False, help="启用组合级历史回测：逐日重跑策略信号并按统一交易规则开平仓")
    parser.add_argument("--start-date",    default=None, help="组合回测信号起始日 YYYYMMDD")
    parser.add_argument("--end-date",      default=None, help="组合回测结束日 YYYYMMDD，默认最近交易日")
    parser.add_argument("--signal-interval", type=int, default=1, help="组合回测信号日间隔，默认每个交易日")
    parser.add_argument("--cash-per-stock", type=float, default=100_000.0, help="组合回测单票预算，默认 100000")
    parser.add_argument("--max-positions", type=int, default=3, help="组合回测最大同时持仓数，默认 3")
    parser.add_argument("--kline-candidate-limit", type=int, default=80, help="组合回测 short/tail 每日 K 线候选数，默认 80")
    parser.add_argument(
        "--leader-kline-candidate-limit",
        type=int,
        default=LEADER_DEFAULT_KLINE_CANDIDATE_LIMIT,
        help=f"组合回测 leader 每日 K 线候选数，默认 {LEADER_DEFAULT_KLINE_CANDIDATE_LIMIT}",
    )
    parser.add_argument("--stop-loss",     type=float, default=8.0,  help="止损百分比（默认 8）")
    parser.add_argument("--retracement",   type=float, default=5.0,  help="单日回撤止盈百分比（默认 5）")
    parser.add_argument("--max-hold",      type=int,   default=60,   help="最大持有交易日数（默认 60）")
    parser.add_argument("--main-limit-up", type=float, default=9.5,  help="主板涨停阈值 pct_chg（默认 9.5）")
    parser.add_argument("--gem-limit-up",  type=float, default=19.5, help="创业板涨停阈值 pct_chg（默认 19.5）")
    parser.add_argument("--lookback",      type=int,   default=90,   help="rps90 回溯交易日窗口（默认 90）")
    parser.add_argument("--interval",      type=int,   default=5,    help="rps90 取样间隔交易日（默认 5）")
    parser.add_argument("--top-n",         type=int,   default=5,    help="每期取前 N 只（默认 5）")
    args = parser.parse_args()

    if args.portfolio:
        run_portfolio_mode(args)
        return

    run_ts = datetime.now()
    print(f"[三策略统一回测] {run_ts.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"策略={args.strategy}  止损=-{args.stop_loss:.0f}%  "
          f"回撤止盈=-{args.retracement:.0f}%  "
          f"涨停止盈=主板>={args.main_limit_up:.1f}% / 创业板>={args.gem_limit_up:.1f}%  "
          f"最大持有={args.max_hold}交易日")

    strategies = (
        ["rps90", "short", "tail", "leader"] if args.strategy == "all"
        else [args.strategy]
    )

    # ── 获取交易日历 ──────────────────────────────────────────────────────────
    today_td   = get_latest_trade_date()
    cal_start  = (pd.Timestamp(today_td) - pd.Timedelta(days=600)).strftime("%Y%m%d")
    all_dates  = fetch_trade_cal_dates(cal_start, today_td)
    print(f"\n交易日历: {all_dates[0]} ~ {all_dates[-1]}  共 {len(all_dates)} 个交易日")

    all_results = []

    for strategy in strategies:
        if strategy == "rps90":
            # ── 重新计算 RPS双90 历史信号 ──────────────────────────────────
            print(f"\n[rps90] 正在重算历史 Top{args.top_n}，回溯={args.lookback}交易日，间隔={args.interval}交易日")
            if len(all_dates) < 95:
                print("[rps90] 交易日历不足，跳过")
                continue

            basic = fetch_stock_basic()
            valid_markets = {"主板", "创业板"}
            basic_inv = basic[basic["market"].isin(valid_markets)][
                ["secucode", "name", "industry", "listing_date"]
            ].copy()

            last_sig_idx  = len(all_dates) - 2    # 至少保留1个持仓日
            first_sig_idx = max(95, last_sig_idx - args.lookback)
            test_indices  = range(first_sig_idx, last_sig_idx + 1, args.interval)
            test_dates    = [all_dates[i] for i in test_indices]
            print(f"  信号日期: {test_dates[0]} ~ {test_dates[-1]}  共 {len(test_dates)} 个")

            rps_signals = []
            for td in test_dates:
                td_idx = all_dates.index(td)
                window_start = max(0, td_idx - 120)
                window = all_dates[window_start: td_idx + 1]
                top5 = _compute_rps90_top5(td, window, basic_inv)
                if top5.empty:
                    continue
                for _, row in top5.iterrows():
                    rps_signals.append({
                        "strategy"   : "rps90",
                        "signal_date": td,
                        "rank"       : int(row["rank"]),
                        "code"       : row["code"],
                        "ts_code"    : row["ts_code"],
                        "name"       : row["name"],
                        "industry"   : row["industry"],
                        "entry_close": float(row["entry_close"]),
                    })
            signals_df = pd.DataFrame(rps_signals)

        elif strategy == "short":
            signals_df = load_short_signals(args.top_n)
            if signals_df.empty:
                print(f"\n[short] 未找到历史信号文件，跳过。")
                continue

        elif strategy == "tail":
            signals_df = load_tail_signals(args.top_n)
            if signals_df.empty:
                print(f"\n[tail] 未找到历史信号文件，跳过。")
                continue

        elif strategy == "leader":
            signals_df = load_leader_signals(args.top_n)
            if signals_df.empty:
                print(f"\n[leader] 未找到历史信号文件，跳过。")
                continue

        else:
            print(f"[未知策略] {strategy}，跳过。")
            continue

        label_map = {
            "rps90": "RPS双90",
            "short": "短线盘后版",
            "tail" : "短线尾盘版",
            "leader": "龙头抱团",
        }
        label = label_map.get(strategy, strategy)

        result_df = run_strategy_backtest(
            signals_df,
            strategy_name=label,
            all_dates=all_dates,
            stop_loss_pct=args.stop_loss,
            retracement_pct=args.retracement,
            max_hold=args.max_hold,
            gem_limit_up=args.gem_limit_up,
            main_limit_up=args.main_limit_up,
        )

        if not result_df.empty:
            print_summary(result_df, label)
            all_results.append(result_df)

    # ── 保存汇总 CSV ──────────────────────────────────────────────────────────
    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        out_path = OUTPUT_DIR / "strategy_backtest.csv"
        combined.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n{'='*62}")
        print(f"回测详情已保存: {out_path}")
        print(f"总记录数: {len(combined)}")

        # 三策略横向对比
        if len(all_results) > 1:
            print(f"\n三策略横向对比:")
            label_map = {"rps90": "RPS双90", "short": "短线盘后版", "tail": "短线尾盘版", "leader": "龙头抱团"}
            print(f"  {'策略':<10} {'n':>5}  {'胜率':>6}  {'均收益':>9}  {'中位数':>9}  {'均持有天':>8}")
            for res in all_results:
                valid = res.dropna(subset=["ret_pct"])
                if valid.empty:
                    continue
                strat = label_map.get(res["strategy"].iloc[0], res["strategy"].iloc[0])
                print(
                    f"  {strat:<10} {len(valid):>5}  "
                    f"{valid['win'].mean():>6.1%}  "
                    f"{valid['ret_pct'].mean():>+9.2f}%  "
                    f"{valid['ret_pct'].median():>+9.2f}%  "
                    f"{valid['hold_days'].mean():>8.1f}"
                )
    else:
        print("\n无回测结果生成。")


if __name__ == "__main__":
    main()
