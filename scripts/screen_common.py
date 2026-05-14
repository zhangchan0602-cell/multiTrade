import os
import re
import time
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import tushare as ts

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "list"
CODE_RE = re.compile(r"^(6|0|3)\d{5}$")
VALID_MARKETS = {"主板", "创业板"}
STOCK_BASIC_CACHE = Path(__file__).resolve().parent / ".cache" / "stock_basic.csv"
KLINE_CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "kline"
_TUSHARE_CALL_LOCK = threading.Lock()
_LAST_TUSHARE_CALL_TS = 0.0


def _load_env_file() -> None:
    """从项目根目录 .env 自动加载环境变量（只补充尚未设置的键，不覆盖已有值）。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            _key = _key.strip()
            _val = _val.strip().strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val


_load_env_file()


def get_tushare_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    raise RuntimeError("missing TUSHARE_TOKEN environment variable")


@lru_cache(maxsize=1)
def get_tushare_pro():
    return ts.pro_api(get_tushare_token())


def call_tushare_api(api_callable, *args, **kwargs):
    """串行限速访问 Tushare，避免在批量历史回放时触发频控。"""
    global _LAST_TUSHARE_CALL_TS
    min_interval = max(0.0, float(os.environ.get("TUSHARE_MIN_INTERVAL", "0.15")))
    with _TUSHARE_CALL_LOCK:
        now = time.monotonic()
        wait_seconds = min_interval - (now - _LAST_TUSHARE_CALL_TS)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        result = api_callable(*args, **kwargs)
        _LAST_TUSHARE_CALL_TS = time.monotonic()
        return result


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
        df = call_tushare_api(
            pro.stock_basic,
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
    df = call_tushare_api(
        pro.daily,
        trade_date=trade_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
    )
    return df if df is not None else pd.DataFrame()


@lru_cache(maxsize=32)
def fetch_bak_daily_snapshot(trade_date: str) -> pd.DataFrame:
    pro = get_tushare_pro()
    df = call_tushare_api(pro.bak_daily, trade_date=trade_date)
    return df if df is not None else pd.DataFrame()


@lru_cache(maxsize=32)
def fetch_adj_factor_snapshot(trade_date: str) -> pd.DataFrame:
    pro = get_tushare_pro()
    df = call_tushare_api(pro.adj_factor, trade_date=trade_date)
    return df if df is not None else pd.DataFrame()


@lru_cache(maxsize=8)
def fetch_trade_cal_dates(start_date: str, end_date: str) -> list:
    """返回 [start_date, end_date] 区间内的上交所交易日列表（升序 YYYYMMDD 字符串）。"""
    pro = get_tushare_pro()
    df = call_tushare_api(pro.trade_cal, exchange="SSE", start_date=start_date, end_date=end_date, is_open=1)
    if df is None or df.empty:
        return []
    return sorted(df["cal_date"].astype(str).tolist())


@lru_cache(maxsize=32)
def fetch_daily_basic_snapshot(trade_date: str) -> pd.DataFrame:
    """获取指定交易日的每日指标快照（流通换手率/量比/PE/PB/市值）。"""
    pro = get_tushare_pro()
    df = call_tushare_api(
        pro.daily_basic,
        trade_date=trade_date,
        fields="ts_code,trade_date,turnover_rate_f,volume_ratio,pe_ttm,pb,total_mv,circ_mv",
    )
    return df if df is not None else pd.DataFrame()


def get_latest_trade_date(as_of: Optional[datetime] = None, max_lookback_days: int = 14) -> str:
    anchor = pd.Timestamp(as_of or datetime.now()).normalize()
    # 优先用 trade_cal 获取候选交易日，再验证 daily 行情已发布
    try:
        start = (anchor - pd.Timedelta(days=max_lookback_days)).strftime("%Y%m%d")
        end = anchor.strftime("%Y%m%d")
        for d in reversed(fetch_trade_cal_dates(start, end)):
            if not fetch_daily_snapshot(d).empty:
                return d
    except Exception:
        pass
    # 回退：逐日探测
    for offset in range(max_lookback_days + 1):
        trade_date = (anchor - pd.Timedelta(days=offset)).strftime("%Y%m%d")
        if not fetch_daily_snapshot(trade_date).empty:
            return trade_date
    raise RuntimeError("unable to resolve latest trade date from Tushare daily")


def get_trade_date_by_calendar_offset(end_trade_date: str, offset_days: int, search_back_days: int = 20) -> Optional[str]:
    anchor = parse_trade_date(end_trade_date)
    target = anchor - pd.Timedelta(days=offset_days)
    # 优先用 trade_cal 精确定位 target 日或其之前最近交易日
    try:
        cal_start = (target - pd.Timedelta(days=10)).strftime("%Y%m%d")
        cal_end = target.strftime("%Y%m%d")
        dates = fetch_trade_cal_dates(cal_start, cal_end)
        if dates:
            return dates[-1]
    except Exception:
        pass
    # 回退：逐日探测
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

    latest_daily = fetch_daily_snapshot(latest_trade_date)[["ts_code", "close"]].rename(
        columns={"ts_code": "secucode", "close": "close_latest"}
    )
    past_daily = fetch_daily_snapshot(past_trade_date)[["ts_code", "close"]].rename(
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


# ── K 线本地增量缓存辅助函数 ────────────────────────────────────────────────

def _kline_cache_path(code: str) -> Path:
    return KLINE_CACHE_DIR / f"{code}.csv"


def _load_kline_cache(code: str) -> pd.DataFrame:
    path = _kline_cache_path(code)
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, parse_dates=["trade_date"])
        if df.empty or "trade_date" not in df.columns:
            return pd.DataFrame()
        return df.dropna(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _save_kline_cache(code: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    KLINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (
        df.sort_values("trade_date")
        .drop_duplicates(subset=["trade_date"], keep="last")
        .reset_index(drop=True)
        .to_csv(_kline_cache_path(code), index=False)
    )


def fetch_akshare_kline_frame(code: str, start_date: str, end_date: str, retries: int = 3) -> pd.DataFrame:
    """使用 akshare 东方财富后复权日线 K 线，含指数退避重试，格式与 fetch_tushare_kline_frame 兼容。"""
    try:
        import akshare as ak
    except ImportError:
        raise RuntimeError("akshare 未安装，请执行 pip install akshare")

    last_err = None
    for i in range(retries):
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="hfq",
            )
            if df is None or df.empty:
                raise RuntimeError(f"akshare kline empty for {code}")

            df = df.rename(columns={
                "日期": "trade_date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "vol",
                "成交额": "amount",
                "换手率": "turnover",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
            df = df.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last").copy()

            for c in ["open", "high", "low", "close", "vol", "amount", "turnover"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")

            # akshare 成交额单位为元，与 fetch_tushare_kline_frame 处理后一致
            return df[["trade_date", "open", "high", "low", "close", "vol", "amount", "turnover"]].copy()
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(0.5 * (2 ** i))  # 0.5s → 1.0s 退避
    raise RuntimeError(f"akshare kline failed for {code} after {retries} retries: {last_err}")


def _fetch_kline_from_source(code: str, start_date: str, end_date: str, source: str = "auto") -> pd.DataFrame:
    """按指定来源获取 K 线，不含缓存逻辑。"""
    if source == "tushare":
        return fetch_tushare_kline_frame(code, start_date, end_date)
    if source == "akshare":
        return fetch_akshare_kline_frame(code, start_date, end_date)

    try:
        return fetch_akshare_kline_frame(code, start_date, end_date)
    except Exception:
        return fetch_tushare_kline_frame(code, start_date, end_date)


def fetch_kline_frame(code: str, start_date: str, end_date: str, source: str = "auto") -> pd.DataFrame:
    """获取后复权 K 线，支持按来源选择并带本地增量缓存。"""
    req_start = pd.to_datetime(start_date, format="%Y%m%d", errors="coerce")
    req_end = pd.to_datetime(end_date, format="%Y%m%d", errors="coerce")

    cached = _load_kline_cache(code)
    if not cached.empty:
        cache_min = cached["trade_date"].min()
        cache_max = cached["trade_date"].max()

        if cache_min <= req_start:
            # 缓存起点足够早；末尾不超过 3 自然日缺口则视为新鲜，直接返回
            if cache_max >= req_end - pd.Timedelta(days=3):
                return cached[
                    (cached["trade_date"] >= req_start) & (cached["trade_date"] <= req_end)
                ].copy()

            # 增量更新：只拉取缓存末日之后的数据
            incr_start = (cache_max + pd.Timedelta(days=1)).strftime("%Y%m%d")
            if incr_start <= end_date:
                try:
                    new_df = _fetch_kline_from_source(code, incr_start, end_date, source=source)
                    if not new_df.empty:
                        merged = (
                            pd.concat([cached, new_df], ignore_index=True)
                            .sort_values("trade_date")
                            .drop_duplicates(subset=["trade_date"], keep="last")
                            .reset_index(drop=True)
                        )
                        _save_kline_cache(code, merged)
                        return merged[
                            (merged["trade_date"] >= req_start) & (merged["trade_date"] <= req_end)
                        ].copy()
                except Exception:
                    pass

            # 增量拉取失败，返回现有缓存（可能缺最新几日）
            return cached[
                (cached["trade_date"] >= req_start) & (cached["trade_date"] <= req_end)
            ].copy()

    # 无缓存或缓存起点太晚：全量拉取并保存
    df = _fetch_kline_from_source(code, start_date, end_date, source=source)
    if not df.empty:
        _save_kline_cache(code, df)
    return df


def fetch_a_no_star_quotes_akshare() -> pd.DataFrame:
    """使用 akshare 东方财富行情获取全 A（不含科创板）快照。"""
    try:
        import akshare as ak
    except ImportError:
        raise RuntimeError("akshare 未安装，请执行 pip install akshare")

    df = ak.stock_zh_a_spot_em()
    if df is None or df.empty:
        raise RuntimeError("akshare stock_zh_a_spot_em 返回空数据")

    df = df.rename(columns={
        "代码": "code",
        "名称": "name",
        "最新价": "close",
        "涨跌幅": "change_rate",
        "成交额": "deal_amount",
        "振幅": "amp",
        "换手率": "turnover",
        "市盈率-动态": "pe",
        "市净率": "pb",
        "总市值": "total_mv",
        "流通市值": "float_mv",
        "60日涨跌幅": "ret_60d",
    })

    df["code"] = df["code"].astype(str).str.zfill(6)
    # 只保留主板/创业板，排除科创板(688xxx)及北交所
    df = df[df["code"].str.match(CODE_RE, na=False)].copy()
    df = df[~df["code"].str.startswith("688")].copy()
    # 过滤 ST/*ST 股票（已退市预警或特别处理，尾盘/盘后均不参与）
    df = df[~df["name"].str.contains("ST", na=False)].copy()

    df["secucode"] = df["code"].apply(lambda c: f"{c}.SH" if c.startswith("6") else f"{c}.SZ")
    df["trade_date"] = pd.Timestamp.now().normalize()

    # 用 stock_basic 补充 industry + listing_date
    basic = fetch_stock_basic()
    if not basic.empty:
        enriched = basic[["secucode", "name", "industry", "listing_date"]].rename(
            columns={"name": "basic_name", "industry": "basic_industry"}
        )
        df = df.merge(enriched, on="secucode", how="left")
        df["name"] = df["basic_name"].fillna(df["name"])
        df["industry"] = df["basic_industry"].fillna("未知行业")
        df = df.drop(columns=["basic_name", "basic_industry"], errors="ignore")
    else:
        df["industry"] = "未知行业"
        df["listing_date"] = pd.NaT

    df["industry"] = df["industry"].fillna("未知行业")
    df["volume_ratio"] = np.nan  # akshare 路径暂无量比，置空由 daily_basic 补充

    df["listing_state"] = "0"
    df["ret_250d"] = np.nan

    for c in ["close", "change_rate", "deal_amount", "amp", "turnover", "pe", "pb", "total_mv", "float_mv", "ret_60d"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # akshare total_mv/float_mv 单位已为元，无需换算
    return df[[
        "code", "secucode", "name", "close", "change_rate", "deal_amount",
        "amp", "turnover", "pe", "pb", "total_mv", "ret_60d", "ret_250d",
        "trade_date", "listing_state", "volume_ratio",
    ]].copy()


def fetch_a_no_star_quotes(
    source: str = "auto",
    trade_date: Optional[str] = None,
    as_of: Optional[datetime] = None,
) -> pd.DataFrame:
    """获取全 A（不含科创板）行情快照，支持按来源选择和指定交易日回放。"""
    fallback_reason = ""
    historical_mode = bool(trade_date)
    target_trade_date = trade_date or get_latest_trade_date(as_of=as_of)

    def _annotate_quote_meta(df: pd.DataFrame, actual_source: str, is_intraday: bool) -> pd.DataFrame:
        df = df.copy()
        df["quote_source_requested"] = source
        df["quote_source_used"] = actual_source
        df["quote_is_intraday"] = bool(is_intraday)
        df["quote_fallback_reason"] = fallback_reason[:200]
        return df

    if source == "akshare" and historical_mode:
        raise RuntimeError("akshare spot quote does not support historical trade_date replay")
    if source == "akshare":
        return _annotate_quote_meta(fetch_a_no_star_quotes_akshare(), actual_source="akshare", is_intraday=True)
    if source == "auto" and not historical_mode:
        try:
            return _annotate_quote_meta(fetch_a_no_star_quotes_akshare(), actual_source="akshare", is_intraday=True)
        except Exception as e:
            fallback_reason = str(e)
            print(f"[fetch_a_no_star_quotes] akshare 失败，回退 tushare: {e}")

    # --- tushare 路径（daily + daily_basic + stock_basic，不依赖 bak_daily）---
    latest_trade_date = target_trade_date
    basic = fetch_stock_basic()

    # 价格数据：daily（amount 单位千元，pct_chg=涨跌幅%）
    daily_snap = fetch_daily_snapshot(latest_trade_date)
    if daily_snap.empty:
        raise RuntimeError(f"empty daily snapshot for trade_date={latest_trade_date}")

    # 每日指标：daily_basic（换手率/量比/PE/PB/市值，total_mv/circ_mv 单位万元）
    db = fetch_daily_basic_snapshot(latest_trade_date)

    # 构建 quote，过滤主板+创业板
    quote = daily_snap[["ts_code", "trade_date", "close", "pct_chg",
                         "high", "low", "pre_close", "amount"]].copy()
    quote = quote.rename(columns={"ts_code": "secucode", "pct_chg": "change_rate"})
    quote["code"] = quote["secucode"].map(ts_code_to_code)
    quote = quote[quote["code"].astype(str).str.match(CODE_RE, na=False)].copy()
    quote = quote[~quote["code"].astype(str).str.startswith("688")].copy()

    # 振幅 = (high - low) / pre_close * 100
    pre_close = pd.to_numeric(quote["pre_close"], errors="coerce")
    quote["amp"] = np.where(
        pre_close > 0,
        (pd.to_numeric(quote["high"], errors="coerce")
         - pd.to_numeric(quote["low"], errors="coerce")) / pre_close * 100.0,
        np.nan,
    )
    # 成交额：daily.amount 单位千元 → 转为元
    quote["deal_amount"] = pd.to_numeric(quote["amount"], errors="coerce") * 1_000.0
    quote = quote.drop(columns=["high", "low", "pre_close", "amount"], errors="ignore")

    # 合并 daily_basic（换手率/量比/PE/PB/市值）
    if not db.empty:
        db_sub = db[["ts_code", "turnover_rate_f", "volume_ratio",
                      "pe_ttm", "pb", "total_mv", "circ_mv"]].rename(
            columns={"ts_code": "secucode", "pe_ttm": "pe",
                     "turnover_rate_f": "turnover"}
        )
        for c in ["turnover", "volume_ratio", "pe", "pb", "total_mv", "circ_mv"]:
            db_sub[c] = pd.to_numeric(db_sub[c], errors="coerce")
        db_sub["total_mv"] = db_sub["total_mv"] * 10_000.0    # 万元 → 元
        db_sub["float_mv"] = db_sub["circ_mv"] * 10_000.0    # 万元 → 元
        db_sub = db_sub.drop(columns=["circ_mv"], errors="ignore")
        quote = quote.merge(db_sub, on="secucode", how="left")
    else:
        for c in ["volume_ratio", "pe", "pb", "total_mv", "float_mv", "turnover"]:
            quote[c] = np.nan

    # 合并 stock_basic（name / industry）
    if not basic.empty:
        enriched = basic[["secucode", "name", "industry", "listing_date"]].rename(
            columns={"name": "basic_name", "industry": "basic_industry"}
        )
        quote = quote.merge(enriched, on="secucode", how="left")
        quote["name"] = quote["basic_name"].fillna(quote["secucode"])
        quote["industry"] = quote["basic_industry"].fillna("未知行业")
        quote = quote.drop(columns=["basic_name", "basic_industry"])
    else:
        quote["name"] = quote["secucode"]
        quote["listing_date"] = pd.NaT
        quote["industry"] = "未知行业"

    # 截面收益率
    ret_60d = compute_cross_section_return(latest_trade_date, 60)
    quote = quote.merge(ret_60d, on="secucode", how="left")

    quote["trade_date"] = pd.to_datetime(quote["trade_date"], format="%Y%m%d", errors="coerce")
    quote["industry"] = quote["industry"].fillna("未知行业")
    quote["listing_state"] = "0"
    quote["ret_250d"] = np.nan

    for c in ["close", "change_rate", "deal_amount", "amp", "turnover",
               "pe", "pb", "total_mv", "ret_60d"]:
        quote[c] = pd.to_numeric(quote[c], errors="coerce")

    quote = quote[
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
            "volume_ratio",
        ]
    ].copy()

    return _annotate_quote_meta(quote, actual_source="tushare", is_intraday=False)


def fetch_org_info(
    secucodes: List[str],
    trade_date: Optional[str] = None,
    as_of: Optional[datetime] = None,
) -> pd.DataFrame:
    basic = fetch_stock_basic()
    if not basic.empty:
        if secucodes:
            basic = basic[basic["secucode"].isin(secucodes)].copy()
        if not basic.empty:
            return basic[["secucode", "code", "listing_date", "industry"]].drop_duplicates(
                subset=["secucode"], keep="first"
            )

    latest_trade_date = trade_date or get_latest_trade_date(as_of=as_of)
    try:
        bak = fetch_bak_daily_snapshot(latest_trade_date).rename(columns={"ts_code": "secucode"})
    except Exception:
        bak = pd.DataFrame()
    if secucodes:
        bak = bak[bak["secucode"].isin(secucodes)].copy() if not bak.empty else bak
    if bak.empty:
        # bak_daily 不可用，用 daily_basic 代替（仅 ts_code，industry 置空）
        db = fetch_daily_basic_snapshot(latest_trade_date)
        if db.empty:
            raise RuntimeError("empty org info")
        bak = db[["ts_code"]].rename(columns={"ts_code": "secucode"}).drop_duplicates()
        if secucodes:
            bak = bak[bak["secucode"].isin(secucodes)].copy()
        bak["industry"] = "未知行业"

    bak["code"] = bak["secucode"].map(ts_code_to_code)
    bak["industry"] = bak["industry"].fillna("未知行业") if "industry" in bak.columns else "未知行业"
    bak["listing_date"] = pd.NaT
    return bak[["secucode", "code", "listing_date", "industry"]].drop_duplicates(
        subset=["secucode"], keep="first"
    )


def fetch_tushare_kline_frame(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    ts_code = code_to_ts_code(code)
    pro = get_tushare_pro()

    daily = call_tushare_api(
        pro.daily,
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,vol,amount",
    )
    if daily.empty:
        raise RuntimeError(f"empty daily history for {ts_code}")

    adj = call_tushare_api(pro.adj_factor, ts_code=ts_code, start_date=start_date, end_date=end_date)
    if adj.empty:
        raise RuntimeError(f"empty adj_factor history for {ts_code}")

    try:
        bak = call_tushare_api(pro.bak_daily, ts_code=ts_code, start_date=start_date, end_date=end_date)
        bak_turnover = bak[["ts_code", "trade_date", "turn_over"]].copy() if not bak.empty else pd.DataFrame()
    except Exception:
        bak_turnover = pd.DataFrame()

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
    daily_snap = fetch_daily_snapshot(latest_trade_date)
    if daily_snap.empty:
        return {}

    daily_snap = daily_snap.rename(columns={"ts_code": "secucode"})
    daily_snap["code"] = daily_snap["secucode"].map(ts_code_to_code)
    daily_snap["close"] = pd.to_numeric(daily_snap["close"], errors="coerce")
    target_codes = {str(code).zfill(6) for code in codes}
    filtered = daily_snap[daily_snap["code"].isin(target_codes)].copy()
    return dict(zip(filtered["code"], filtered["close"]))


def winsorize(s: pd.Series, p: float = 0.025) -> pd.Series:
    v = s.dropna()
    if len(v) < 20:
        return s
    lo, hi = np.nanpercentile(v, [100 * p, 100 * (1 - p)])
    return s.clip(lo, hi)


def industry_zscore(series: pd.Series, industry: pd.Series) -> pd.Series:
    """行业中性化 z-score，向量化实现（groupby.transform），无 Python 循环。"""
    tmp = pd.DataFrame({"x": series, "ind": industry.fillna("未知行业")})
    global_mu = series.mean(skipna=True)
    global_sd = series.std(skipna=True, ddof=0)

    cnt = tmp.groupby("ind")["x"].transform("count")
    mu  = tmp.groupby("ind")["x"].transform("mean")
    sd  = tmp.groupby("ind")["x"].transform(lambda s: s.std(ddof=0))

    # 样本数 < 3 的行业回退全局统计量
    mu = mu.where(cnt >= 3, global_mu)
    sd = sd.where(cnt >= 3, global_sd)
    # std = 0 时回退全局 std（避免除零）
    sd = sd.where(sd != 0, global_sd)

    z = (series - mu) / sd.replace(0, np.nan)
    return z.replace([np.inf, -np.inf], np.nan).fillna(0.0)