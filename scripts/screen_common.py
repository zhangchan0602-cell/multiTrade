import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import tushare as ts

OUTPUT_DIR = Path("docs/list")
CODE_RE = re.compile(r"^(6|0|3)\d{5}$")
VALID_MARKETS = {"主板", "创业板"}
STOCK_BASIC_CACHE = Path(__file__).resolve().parent / ".cache" / "stock_basic.csv"


def get_tushare_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    raise RuntimeError("missing TUSHARE_TOKEN environment variable")


@lru_cache(maxsize=1)
def get_tushare_pro():
    return ts.pro_api(get_tushare_token())


def code_to_ts_code(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    raise ValueError(f"unsupported A-share code: {code}")


def ts_code_to_code(ts_code: str) -> str:
    return str(ts_code).split(".")[0].zfill(6)


def parse_trade_date(value: str) -> pd.Timestamp:
    return pd.to_datetime(value, format="%Y%m%d", errors="coerce")


def empty_stock_basic() -> pd.DataFrame:
    return pd.DataFrame(columns=["secucode", "code", "name", "industry", "market", "listing_date"])


def load_stock_basic_cache() -> pd.DataFrame:
    if not STOCK_BASIC_CACHE.exists():
        return empty_stock_basic()

    df = pd.read_csv(STOCK_BASIC_CACHE)
    if df.empty:
        return empty_stock_basic()

    df["code"] = df["code"].astype(str).str.zfill(6)
    df["listing_date"] = pd.to_datetime(df["listing_date"], errors="coerce")
    df["industry"] = df["industry"].fillna("未知行业")
    return df[["secucode", "code", "name", "industry", "market", "listing_date"]]


def save_stock_basic_cache(df: pd.DataFrame) -> None:
    STOCK_BASIC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(STOCK_BASIC_CACHE, index=False)


@lru_cache(maxsize=1)
def fetch_stock_basic() -> pd.DataFrame:
    cached = load_stock_basic_cache()
    if not cached.empty:
        return cached

    pro = get_tushare_pro()
    try:
        df = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,industry,market,list_date",
        )
    except Exception as exc:
        print(f"[stock_basic] unavailable, fallback to bak_daily metadata: {exc}")
        return empty_stock_basic()

    if df.empty:
        return empty_stock_basic()

    df = df.rename(
        columns={
            "ts_code": "secucode",
            "symbol": "code",
            "list_date": "listing_date",
        }
    )
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["code"].str.match(CODE_RE, na=False)].copy()
    df = df[df["market"].isin(VALID_MARKETS)].copy()
    df["listing_date"] = pd.to_datetime(df["listing_date"], format="%Y%m%d", errors="coerce")
    df["industry"] = df["industry"].fillna("未知行业")
    df = df[["secucode", "code", "name", "industry", "market", "listing_date"]].drop_duplicates(
        subset=["secucode"], keep="first"
    )
    save_stock_basic_cache(df)
    return df


@lru_cache(maxsize=32)
def fetch_daily_snapshot(trade_date: str) -> pd.DataFrame:
    pro = get_tushare_pro()
    df = pro.daily(
        trade_date=trade_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
    )
    return df if df is not None else pd.DataFrame()


@lru_cache(maxsize=32)
def fetch_bak_daily_snapshot(trade_date: str) -> pd.DataFrame:
    pro = get_tushare_pro()
    df = pro.bak_daily(trade_date=trade_date)
    return df if df is not None else pd.DataFrame()


@lru_cache(maxsize=32)
def fetch_adj_factor_snapshot(trade_date: str) -> pd.DataFrame:
    pro = get_tushare_pro()
    df = pro.adj_factor(trade_date=trade_date)
    return df if df is not None else pd.DataFrame()


def get_latest_trade_date(as_of: Optional[datetime] = None, max_lookback_days: int = 14) -> str:
    anchor = pd.Timestamp(as_of or datetime.now()).normalize()
    for offset in range(max_lookback_days + 1):
        trade_date = (anchor - pd.Timedelta(days=offset)).strftime("%Y%m%d")
        if not fetch_daily_snapshot(trade_date).empty:
            return trade_date
    raise RuntimeError("unable to resolve latest trade date from Tushare daily")


def get_trade_date_by_calendar_offset(end_trade_date: str, offset_days: int, search_back_days: int = 20) -> Optional[str]:
    anchor = parse_trade_date(end_trade_date)
    target = anchor - pd.Timedelta(days=offset_days)
    for adjust in range(search_back_days + 1):
        trade_date = (target - pd.Timedelta(days=adjust)).strftime("%Y%m%d")
        if not fetch_daily_snapshot(trade_date).empty:
            return trade_date
    return None


def compute_cross_section_return(latest_trade_date: str, sessions_back: int) -> pd.DataFrame:
    approx_offset_days = {60: 90, 250: 380}.get(sessions_back, sessions_back)
    past_trade_date = get_trade_date_by_calendar_offset(latest_trade_date, approx_offset_days)
    col_name = f"ret_{sessions_back}d"
    if not past_trade_date:
        return pd.DataFrame(columns=["secucode", col_name])

    latest_daily = fetch_bak_daily_snapshot(latest_trade_date)[["ts_code", "close"]].rename(
        columns={"ts_code": "secucode", "close": "close_latest"}
    )
    past_daily = fetch_bak_daily_snapshot(past_trade_date)[["ts_code", "close"]].rename(
        columns={"ts_code": "secucode", "close": "close_past"}
    )
    latest_adj = fetch_adj_factor_snapshot(latest_trade_date)[["ts_code", "adj_factor"]].rename(
        columns={"ts_code": "secucode", "adj_factor": "adj_latest"}
    )
    past_adj = fetch_adj_factor_snapshot(past_trade_date)[["ts_code", "adj_factor"]].rename(
        columns={"ts_code": "secucode", "adj_factor": "adj_past"}
    )

    merged = latest_daily.merge(past_daily, on="secucode", how="left")
    merged = merged.merge(latest_adj, on="secucode", how="left")
    merged = merged.merge(past_adj, on="secucode", how="left")

    close_latest = pd.to_numeric(merged["close_latest"], errors="coerce")
    close_past = pd.to_numeric(merged["close_past"], errors="coerce")
    adj_latest = pd.to_numeric(merged["adj_latest"], errors="coerce")
    adj_past = pd.to_numeric(merged["adj_past"], errors="coerce")
    base = close_past * adj_past / adj_latest
    merged[col_name] = np.where(base > 0, close_latest / base - 1.0, np.nan) * 100.0
    return merged[["secucode", col_name]]


def fetch_a_no_star_quotes() -> pd.DataFrame:
    latest_trade_date = get_latest_trade_date()
    basic = fetch_stock_basic()

    bak = fetch_bak_daily_snapshot(latest_trade_date).rename(
        columns={
            "ts_code": "secucode",
            "pct_change": "change_rate",
            "turn_over": "turnover",
            "swing": "amp",
            "amount": "deal_amount_raw",
        }
    )

    if bak.empty:
        raise RuntimeError(f"empty latest quote snapshot for trade_date={latest_trade_date}")

    ret_60d = compute_cross_section_return(latest_trade_date, 60)
    quote = bak[["secucode", "name", "industry", "turnover", "amp", "pe", "total_mv", "float_mv"]].copy()
    quote["code"] = quote["secucode"].map(ts_code_to_code)
    quote = quote[quote["code"].astype(str).str.match(CODE_RE, na=False)].copy()
    quote = quote.merge(bak[["secucode", "trade_date", "close", "change_rate", "deal_amount_raw"]], on="secucode", how="inner")
    quote = quote.merge(ret_60d, on="secucode", how="left")

    if not basic.empty:
        enriched = basic[["secucode", "name", "industry", "listing_date"]].rename(
            columns={"name": "basic_name", "industry": "basic_industry"}
        )
        quote = quote.merge(enriched, on="secucode", how="left")
        quote["name"] = quote["basic_name"].fillna(quote["name"])
        quote["industry"] = quote["basic_industry"].fillna(quote["industry"])
        quote = quote.drop(columns=["basic_name", "basic_industry"])
    else:
        quote["listing_date"] = pd.NaT

    quote["trade_date"] = pd.to_datetime(quote["trade_date"], format="%Y%m%d", errors="coerce")
    quote["deal_amount"] = pd.to_numeric(quote["deal_amount_raw"], errors="coerce") * 10_000.0
    quote["industry"] = quote["industry"].fillna("未知行业")
    quote["pb"] = np.nan
    quote["listing_state"] = "0"
    quote["ret_250d"] = np.nan
    quote["total_mv"] = pd.to_numeric(quote["total_mv"], errors="coerce") * 100_000_000.0
    quote["float_mv"] = pd.to_numeric(quote["float_mv"], errors="coerce") * 100_000_000.0

    for c in ["close", "change_rate", "deal_amount", "amp", "turnover", "pe", "pb", "total_mv", "ret_60d", "ret_250d"]:
        quote[c] = pd.to_numeric(quote[c], errors="coerce")

    return quote[
        [
            "code",
            "secucode",
            "name",
            "close",
            "change_rate",
            "deal_amount",
            "amp",
            "turnover",
            "pe",
            "pb",
            "total_mv",
            "ret_60d",
            "ret_250d",
            "trade_date",
            "listing_state",
        ]
    ].copy()


def fetch_org_info(secucodes: List[str]) -> pd.DataFrame:
    basic = fetch_stock_basic()
    if not basic.empty:
        if secucodes:
            basic = basic[basic["secucode"].isin(secucodes)].copy()
        if not basic.empty:
            return basic[["secucode", "code", "listing_date", "industry"]].drop_duplicates(
                subset=["secucode"], keep="first"
            )

    latest_trade_date = get_latest_trade_date()
    bak = fetch_bak_daily_snapshot(latest_trade_date).rename(columns={"ts_code": "secucode"})
    if secucodes:
        bak = bak[bak["secucode"].isin(secucodes)].copy()
    if bak.empty:
        raise RuntimeError("empty org info")

    bak["code"] = bak["secucode"].map(ts_code_to_code)
    bak["industry"] = bak["industry"].fillna("未知行业")
    bak["listing_date"] = pd.NaT
    return bak[["secucode", "code", "listing_date", "industry"]].drop_duplicates(
        subset=["secucode"], keep="first"
    )


def fetch_tushare_kline_frame(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    ts_code = code_to_ts_code(code)
    pro = get_tushare_pro()

    daily = pro.daily(
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,vol,amount",
    )
    if daily.empty:
        raise RuntimeError(f"empty daily history for {ts_code}")

    adj = pro.adj_factor(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if adj.empty:
        raise RuntimeError(f"empty adj_factor history for {ts_code}")

    bak = pro.bak_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    bak_turnover = bak[["ts_code", "trade_date", "turn_over"]].copy() if not bak.empty else pd.DataFrame()

    merged = daily.merge(adj, on=["ts_code", "trade_date"], how="left")
    if not bak_turnover.empty:
        merged = merged.merge(bak_turnover, on=["ts_code", "trade_date"], how="left")
    else:
        merged["turn_over"] = np.nan

    merged["trade_date"] = pd.to_datetime(merged["trade_date"], format="%Y%m%d", errors="coerce")
    merged = merged.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last").copy()

    for c in ["open", "high", "low", "close", "vol", "amount", "adj_factor", "turn_over"]:
        merged[c] = pd.to_numeric(merged[c], errors="coerce")

    latest_adj = merged["adj_factor"].dropna()
    if latest_adj.empty or latest_adj.iloc[-1] <= 0:
        raise RuntimeError(f"invalid adj_factor history for {ts_code}")

    scale = merged["adj_factor"] / float(latest_adj.iloc[-1])
    for c in ["open", "high", "low", "close"]:
        merged[c] = merged[c] * scale

    merged["amount"] = merged["amount"] * 1000.0
    merged["turnover"] = merged["turn_over"]
    return merged[["trade_date", "open", "high", "low", "close", "vol", "amount", "turnover"]].copy()


def fetch_latest_close_map(codes: List[str], as_of: Optional[datetime] = None) -> dict:
    if not codes:
        return {}

    latest_trade_date = get_latest_trade_date(as_of=as_of)
    bak = fetch_bak_daily_snapshot(latest_trade_date).rename(columns={"ts_code": "secucode"})
    if bak.empty:
        return {}

    bak["code"] = bak["secucode"].map(ts_code_to_code)
    bak["close"] = pd.to_numeric(bak["close"], errors="coerce")
    target_codes = {str(code).zfill(6) for code in codes}
    filtered = bak[bak["code"].isin(target_codes)].copy()
    return dict(zip(filtered["code"], filtered["close"]))


def winsorize(s: pd.Series, p: float = 0.025) -> pd.Series:
    v = s.dropna()
    if len(v) < 20:
        return s
    lo, hi = np.nanpercentile(v, [100 * p, 100 * (1 - p)])
    return s.clip(lo, hi)


def industry_zscore(series: pd.Series, industry: pd.Series) -> pd.Series:
    tmp = pd.DataFrame({"x": series, "ind": industry.fillna("未知行业")})
    z = pd.Series(np.nan, index=tmp.index)
    global_x = tmp["x"]
    global_mu = global_x.mean(skipna=True)
    global_std = global_x.std(skipna=True, ddof=0)

    for _, idx in tmp.groupby("ind").groups.items():
        x = tmp.loc[idx, "x"]
        if x.notna().sum() < 3:
            mu = global_mu
            sd = global_std
        else:
            mu = x.mean(skipna=True)
            sd = x.std(skipna=True, ddof=0)
            if pd.isna(sd) or sd == 0:
                sd = global_std
                if pd.isna(sd) or sd == 0:
                    sd = np.nan
        z.loc[list(idx)] = (x - mu) / sd if pd.notna(sd) and sd != 0 else 0.0
    return z