#!/usr/bin/env python3
"""
全A（不含科创板）中线多因子筛选（基于东财公开接口）

股票池：
- 沪A主板
- 深A主板
- 创业板

输出文件：
- docs/list/all_a_no_star_mid_multifactor_passed.csv
- docs/list/all_a_no_star_mid_multifactor_passed.md
- docs/list/all_a_no_star_mid_multifactor_top30.md
- docs/list/all_a_no_star_mid_multifactor_summary.md
"""

import re
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
import requests

DATA_CENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

TOKEN = "894050c76af8597a853f5b408b759f5d"
OUTPUT_DIR = Path("docs/list")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/",
}

A_NO_STAR_FILTER = (
    '(TRADE_MARKET_CODE in ("069001001001","069001002001","069001002002"))'
    '(LISTING_STATE="0")(SECURITY_TYPE_CODE="058001001")'
)
CODE_RE = re.compile(r"^(6|0|3)\d{5}$")


def eastmoney_get(url: str, **kwargs):
    with requests.Session() as session:
        session.trust_env = False
        return session.get(url, **kwargs)


def safe_get_json(params: Dict, retries: int = 6, sleep_s: float = 0.7) -> Dict:
    err = None
    for i in range(retries):
        try:
            r = eastmoney_get(DATA_CENTER_URL, params=params, headers=HEADERS, timeout=25)
            r.raise_for_status()
            js = r.json()
            if js.get("success") is False:
                raise RuntimeError(js.get("message"))
            return js
        except Exception as e:  # pragma: no cover
            err = e
            time.sleep(sleep_s * (1.5 ** i))
    raise RuntimeError(f"datacenter request failed: params={params}, err={err}")


def fetch_a_no_star_quotes() -> pd.DataFrame:
    rows = []
    page = 1
    while True:
        params = {
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "pageSize": "500",
            "pageNumber": str(page),
            "reportName": "RPT_DMSK_TS_STOCKNEW",
            "quoteColumns": (
                "f2~01~SECURITY_CODE~CLOSE_PRICE,"
                "f3~01~SECURITY_CODE~CHANGE_RATE,"
                "f6~01~SECURITY_CODE~DEAL_AMOUNT,"
                "f7~01~SECURITY_CODE~AMPLITUDE,"
                "f8~01~SECURITY_CODE~TURNOVERRATE,"
                "f9~01~SECURITY_CODE~PE_DYNAMIC,"
                "f20~01~SECURITY_CODE~TOTAL_MARKET_CAP,"
                "f23~01~SECURITY_CODE~PB_NEWEST,"
                "f24~01~SECURITY_CODE~CHANGE_RATE_60D,"
                "f25~01~SECURITY_CODE~CHANGE_RATE_250D"
            ),
            "quoteType": "0",
            "columns": "ALL",
            "filter": A_NO_STAR_FILTER,
            "token": TOKEN,
        }
        js = safe_get_json(params)
        result = (js or {}).get("result") or {}
        data = result.get("data") or []
        if not data:
            break
        rows.extend(data)
        pages = int(result.get("pages") or 0)
        if page >= pages:
            break
        page += 1

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("empty A-share quote dataset")

    df = df.rename(
        columns={
            "SECURITY_CODE": "code",
            "SECUCODE": "secucode",
            "SECURITY_NAME_ABBR": "name",
            "CLOSE_PRICE": "close",
            "CHANGE_RATE": "change_rate",
            "DEAL_AMOUNT": "deal_amount",
            "AMPLITUDE": "amp",
            "TURNOVERRATE": "turnover",
            "PE_DYNAMIC": "pe",
            "PB_NEWEST": "pb",
            "TOTAL_MARKET_CAP": "total_mv",
            "CHANGE_RATE_60D": "ret_60d",
            "CHANGE_RATE_250D": "ret_250d",
            "TRADE_DATE": "trade_date",
            "LISTING_STATE": "listing_state",
        }
    )

    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["code"].str.match(CODE_RE, na=False)].copy()
    df = df[df["listing_state"].astype(str) == "0"].copy()

    for c in ["close", "deal_amount", "amp", "turnover", "pe", "pb", "total_mv", "ret_60d", "ret_250d"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    return df


def chunks(xs: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


def fetch_org_info(secucodes: List[str]) -> pd.DataFrame:
    out = []
    for i, batch in enumerate(chunks(secucodes, 200), start=1):
        in_expr = ",".join([f'"{x}"' for x in batch])
        params = {
            "reportName": "RPT_F10_BASIC_ORGINFO",
            "columns": "SECUCODE,SECURITY_CODE,LISTING_DATE,INDUSTRYCSRC1,TRADE_MARKET_CODE,SECURITY_TYPE_CODE",
            "filter": f"(SECUCODE in ({in_expr}))",
            "pageNumber": 1,
            "pageSize": 500,
            "sortTypes": "1",
            "sortColumns": "SECURITY_CODE",
        }
        js = safe_get_json(params)
        data = ((js or {}).get("result") or {}).get("data") or []
        if data:
            out.extend(data)
        if i % 5 == 0:
            print(f"[org] batch {i}, rows={len(out)}")

    df = pd.DataFrame(out)
    if df.empty:
        raise RuntimeError("empty org info")

    df = df.rename(
        columns={
            "SECUCODE": "secucode",
            "SECURITY_CODE": "code",
            "LISTING_DATE": "listing_date",
            "INDUSTRYCSRC1": "industry",
        }
    )
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["listing_date"] = pd.to_datetime(df["listing_date"], errors="coerce")
    df["industry"] = df["industry"].fillna("未知行业")
    df = df.drop_duplicates(subset=["secucode"], keep="first").copy()
    return df[["secucode", "code", "listing_date", "industry"]]


def candidate_report_dates(as_of: date, years_back: int = 3) -> List[str]:
    out = []
    for y in range(as_of.year, as_of.year - years_back - 1, -1):
        for m, d in [(12, 31), (9, 30), (6, 30), (3, 31)]:
            rd = date(y, m, d)
            if rd <= as_of:
                out.append(f"{rd.isoformat()} 00:00:00")
    return sorted(set(out), reverse=True)


def fetch_finance_by_report_date(report_date: str) -> pd.DataFrame:
    rows = []
    page = 1
    while True:
        params = {
            "reportName": "RPT_F10_FINANCE_MAINFINADATA",
            "columns": (
                "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,NOTICE_DATE,"
                "ROEJQ,XSMLL,TOTALOPERATEREVETZ,PARENTNETPROFITTZ,ZCFZL,NCO_NETPROFIT,FCFF_BACK"
            ),
            "filter": f"(REPORT_DATE='{report_date}')",
            "pageNumber": page,
            "pageSize": 500,
            "sortTypes": "-1",
            "sortColumns": "SECURITY_CODE",
            "source": "HSF10",
            "client": "PC",
        }
        js = safe_get_json(params)
        result = (js or {}).get("result") or {}
        data = result.get("data") or []
        if not data:
            break
        rows.extend(data)
        pages = int(result.get("pages") or 0)
        if page >= pages:
            break
        page += 1
    return pd.DataFrame(rows)


def fetch_latest_finance(secucodes: List[str], as_of: date) -> pd.DataFrame:
    secu_set = set(secucodes)
    frames = []
    covered = set()

    for rd in candidate_report_dates(as_of, years_back=3):
        df = fetch_finance_by_report_date(rd)
        if df.empty:
            continue
        df = df[df["SECUCODE"].isin(secu_set)].copy()
        if df.empty:
            continue
        frames.append(df)
        covered.update(df["SECUCODE"].unique().tolist())
        cov = len(covered) / max(1, len(secu_set))
        print(f"[finance] report_date={rd}, matched={len(df)}, coverage={cov:.2%}")
        if cov >= 0.995:
            break

    if not frames:
        raise RuntimeError("empty finance dataset for A-share universe")

    fin = pd.concat(frames, ignore_index=True)
    fin["REPORT_DATE"] = pd.to_datetime(fin["REPORT_DATE"], errors="coerce")
    fin["NOTICE_DATE"] = pd.to_datetime(fin["NOTICE_DATE"], errors="coerce")
    fin = fin.sort_values(["SECUCODE", "REPORT_DATE", "NOTICE_DATE"], ascending=[True, False, False])
    fin = fin.drop_duplicates(subset=["SECUCODE"], keep="first").copy()

    fin = fin.rename(
        columns={
            "SECUCODE": "secucode",
            "SECURITY_CODE": "code",
            "REPORT_DATE": "report_date",
            "NOTICE_DATE": "notice_date",
            "ROEJQ": "roe",
            "XSMLL": "gross_margin",
            "TOTALOPERATEREVETZ": "rev_yoy",
            "PARENTNETPROFITTZ": "profit_yoy",
            "ZCFZL": "debt_ratio",
            "NCO_NETPROFIT": "cfo_to_np",
            "FCFF_BACK": "fcff_back",
        }
    )
    fin["code"] = fin["code"].astype(str).str.zfill(6)
    for c in ["roe", "gross_margin", "rev_yoy", "profit_yoy", "debt_ratio", "cfo_to_np", "fcff_back"]:
        fin[c] = pd.to_numeric(fin[c], errors="coerce")

    keep = [
        "secucode",
        "code",
        "report_date",
        "notice_date",
        "roe",
        "gross_margin",
        "rev_yoy",
        "profit_yoy",
        "debt_ratio",
        "cfo_to_np",
        "fcff_back",
    ]
    return fin[keep]


def get_kline_feature(code: str, retries: int = 4) -> Dict:
    params = {
        "secid": f"1.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "lmt": "320",
        "end": "20500000",
    }
    last_err = None
    for i in range(retries):
        try:
            r = eastmoney_get(KLINE_URL, params=params, headers=HEADERS, timeout=25)
            r.raise_for_status()
            js = r.json()
            klines = ((js or {}).get("data") or {}).get("klines") or []
            if not klines:
                raise RuntimeError("empty kline")

            closes = []
            amounts = []
            for line in klines:
                p = line.split(",")
                if len(p) < 7:
                    continue
                try:
                    closes.append(float(p[2]))
                    amounts.append(float(p[6]))
                except Exception:
                    continue

            if len(closes) < 80:
                raise RuntimeError("insufficient history")

            c = np.array(closes, dtype=float)
            a = np.array(amounts, dtype=float)
            rets = c[1:] / c[:-1] - 1.0

            avg_amount_60 = float(np.nanmean(a[-60:])) if len(a) >= 60 else np.nan
            vol_60 = float(np.std(rets[-60:], ddof=0)) if len(rets) >= 60 else np.nan
            mom_12_1 = np.nan
            if len(c) >= 252:
                mom_12_1 = float(c[-22] / c[-252] - 1.0)

            return {
                "code": code,
                "kline_ok": 1,
                "listed_days_kline": int(len(c)),
                "avg_amount_60": avg_amount_60,
                "vol_60": vol_60,
                "mom_12_1": mom_12_1,
            }
        except Exception as e:  # pragma: no cover
            last_err = e
            time.sleep(0.4 * (i + 1))

    return {
        "code": code,
        "kline_ok": 0,
        "listed_days_kline": np.nan,
        "avg_amount_60": np.nan,
        "vol_60": np.nan,
        "mom_12_1": np.nan,
        "kline_err": str(last_err)[:120],
    }


def fetch_kline_features(codes: List[str], max_workers: int = 8) -> pd.DataFrame:
    out = []
    total = len(codes)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(get_kline_feature, c): c for c in codes}
        for fut in as_completed(futs):
            done += 1
            out.append(fut.result())
            if done % 200 == 0 or done == total:
                ok = sum(1 for r in out if r.get("kline_ok") == 1)
                print(f"[kline] {done}/{total}, success={ok}")
    return pd.DataFrame(out)


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
            if pd.isna(global_std) or global_std == 0:
                z.loc[idx] = 0.0
            else:
                z.loc[idx] = (x - global_mu) / global_std
            continue

        mu = x.mean(skipna=True)
        std = x.std(skipna=True, ddof=0)
        if pd.isna(std) or std == 0:
            if pd.isna(global_std) or global_std == 0:
                z.loc[idx] = 0.0
            else:
                z.loc[idx] = (x - global_mu) / global_std
        else:
            z.loc[idx] = (x - mu) / std
    return z


def score_factors(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Value
    df["ep"] = np.where(df["pe"] > 0, 1.0 / df["pe"], np.nan)
    df["bp"] = np.where(df["pb"] > 0, 1.0 / df["pb"], np.nan)
    df["fcf_yield"] = np.where(df["total_mv"] > 0, df["fcff_back"] / df["total_mv"], np.nan)

    # Quality
    df["debt_neg"] = -df["debt_ratio"]

    # Momentum / LowVol fallback
    # ret_60d/ret_250d are percent values from quote fields
    ret60 = pd.to_numeric(df["ret_60d"], errors="coerce") / 100.0
    ret250 = pd.to_numeric(df["ret_250d"], errors="coerce") / 100.0
    df["mom_12_1_proxy"] = (1.0 + ret250) / (1.0 + ret60) - 1.0
    df["mom_raw"] = df["mom_12_1"].where(df["mom_12_1"].notna(), df["mom_12_1_proxy"])

    # amp is percent; use as volatility proxy when 60d vol missing
    df["vol_proxy"] = pd.to_numeric(df["amp"], errors="coerce") / 100.0
    df["vol_raw"] = df["vol_60"].where(df["vol_60"].notna(), df["vol_proxy"])
    df["lowvol_raw"] = -df["vol_raw"]

    raw_factor_cols = [
        "ep",
        "bp",
        "fcf_yield",
        "roe",
        "gross_margin",
        "cfo_to_np",
        "debt_neg",
        "rev_yoy",
        "profit_yoy",
        "mom_raw",
        "lowvol_raw",
    ]

    for c in raw_factor_cols:
        df[c] = winsorize(pd.to_numeric(df[c], errors="coerce"), p=0.025)
        zc = c + "_z"
        df[zc] = industry_zscore(df[c], df["industry"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    df["value_score"] = 0.4 * df["ep_z"] + 0.3 * df["bp_z"] + 0.3 * df["fcf_yield_z"]
    df["quality_score"] = (
        0.35 * df["roe_z"]
        + 0.25 * df["gross_margin_z"]
        + 0.2 * df["cfo_to_np_z"]
        + 0.2 * df["debt_neg_z"]
    )
    df["growth_score"] = 0.5 * df["rev_yoy_z"] + 0.5 * df["profit_yoy_z"]
    df["momentum_score"] = df["mom_raw_z"]
    df["lowvol_score"] = df["lowvol_raw_z"]

    df["score"] = (
        0.25 * df["value_score"]
        + 0.30 * df["quality_score"]
        + 0.15 * df["growth_score"]
        + 0.20 * df["momentum_score"]
        + 0.10 * df["lowvol_score"]
    )

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    df["score_raw"] = df["score"]
    if len(df) > 1:
        # 百分制: 最高分=100, 最低分=0
        df["score_100"] = (len(df) - df["rank"]) / (len(df) - 1) * 100.0
    elif len(df) == 1:
        df["score_100"] = 100.0
    else:
        df["score_100"] = np.nan
    return df


def write_outputs(scored: pd.DataFrame, merged: pd.DataFrame, run_ts: datetime) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    export_cols = [
        "rank",
        "code",
        "name",
        "industry",
        "score_100",
        "score_raw",
        "value_score",
        "quality_score",
        "growth_score",
        "momentum_score",
        "lowvol_score",
        "pe",
        "pb",
        "fcf_yield",
        "roe",
        "gross_margin",
        "cfo_to_np",
        "debt_ratio",
        "rev_yoy",
        "profit_yoy",
        "mom_raw",
        "vol_raw",
        "avg_amount_60_used",
        "report_date",
        "notice_date",
    ]
    scored[export_cols].to_csv(
        OUTPUT_DIR / "all_a_no_star_mid_multifactor_passed.csv", index=False, encoding="utf-8-sig"
    )

    # full markdown list
    with (OUTPUT_DIR / "all_a_no_star_mid_multifactor_passed.md").open("w", encoding="utf-8") as f:
        f.write("# 全A（不含科创板）中线多因子模型 符合清单\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 共 {len(scored)} 只\n\n")
        f.write("| 排名 | 代码 | 名称 | 行业 | 百分制得分 |\n")
        f.write("|---:|---:|---|---|---:|\n")
        for _, r in scored.iterrows():
            f.write(
                f"| {int(r['rank'])} | {r['code']} | {r['name']} | {r['industry']} | {float(r['score_100']):.2f} |\n"
            )

    # top30
    top30 = scored.head(30)
    with (OUTPUT_DIR / "all_a_no_star_mid_multifactor_top30.md").open("w", encoding="utf-8") as f:
        f.write("# 全A（不含科创板）中线多因子模型 Top 30\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("| 排名 | 代码 | 名称 | 行业 | 百分制得分 | 原始分 | 价值 | 质量 | 成长 | 动量 | 低波 |\n")
        f.write("|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|\n")
        for _, r in top30.iterrows():
            f.write(
                (
                    "| {rank} | {code} | {name} | {industry} | {score100:.2f} | {score_raw:.4f} | "
                    "{value:.4f} | {quality:.4f} | {growth:.4f} | {mom:.4f} | {lowvol:.4f} |\n"
                ).format(
                    rank=int(r["rank"]),
                    code=r["code"],
                    name=r["name"],
                    industry=r["industry"],
                    score100=float(r["score_100"]),
                    score_raw=float(r["score_raw"]),
                    value=float(r["value_score"]),
                    quality=float(r["quality_score"]),
                    growth=float(r["growth_score"]),
                    mom=float(r["momentum_score"]),
                    lowvol=float(r["lowvol_score"]),
                )
            )

    # summary
    kline_ok = int((merged["kline_ok"] == 1).sum())
    with (OUTPUT_DIR / "all_a_no_star_mid_multifactor_summary.md").open("w", encoding="utf-8") as f:
        f.write("# 全A（不含科创板）中线多因子筛选统计\n\n")
        f.write(f"- 生成时间: {run_ts.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 全A（沪主板+深主板+创业板）初始样本: {len(merged)}\n")
        f.write(f"- K线成功样本: {kline_ok}\n")
        f.write(f"- 硬过滤后样本: {int(merged['hard_pass'].sum())}\n")
        f.write(f"- 最终符合样本: {len(scored)}\n\n")
        f.write("## 输出文件\n\n")
        f.write("- `docs/list/all_a_no_star_mid_multifactor_passed.csv`\n")
        f.write("- `docs/list/all_a_no_star_mid_multifactor_passed.md`\n")
        f.write("- `docs/list/all_a_no_star_mid_multifactor_top30.md`\n")
        f.write("- `docs/list/all_a_no_star_mid_multifactor_summary.md`\n\n")
        f.write("## 口径说明\n\n")
        f.write("- 股票池: 沪A主板 + 深A主板 + 创业板（不含科创板）\n")
        f.write("- ST过滤: 名称含 `ST` 或 `*ST` 剔除\n")
        f.write("- 上市天数: 优先用K线交易日，缺失时按上市日自然日近似\n")
        f.write("- 成交额过滤: 优先用60日均成交额，缺失时用当日成交额替代\n")
        f.write("- 动量: 优先 `12-1` 月K线收益，缺失时用 `(1+250日涨跌)/(1+60日涨跌)-1` 近似\n")
        f.write("- 低波: 优先60日收益波动率，缺失时用当日振幅近似\n")
        f.write("- 百分制得分: 按原始综合分在全样本中的线性排名换算到0-100\n")


def main() -> None:
    run_ts = datetime.now()
    as_of = run_ts.date()

    print("[1/4] fetch A-share quote universe (no STAR)...")
    quote = fetch_a_no_star_quotes()
    print(f"[1/4] quote rows={len(quote)}")

    print("[2/4] fetch org info + finance...")
    org = fetch_org_info(quote["secucode"].unique().tolist())
    fin = fetch_latest_finance(quote["secucode"].unique().tolist(), as_of)
    print(f"[2/4] org rows={len(org)}, finance rows={len(fin)}")

    skip_kline = os.environ.get("SKIP_KLINE", "0") == "1"
    if skip_kline:
        print("[3/4] skip kline (fast mode)...")
        kf = pd.DataFrame(
            {
                "code": quote["code"].tolist(),
                "kline_ok": 0,
                "listed_days_kline": np.nan,
                "avg_amount_60": np.nan,
                "vol_60": np.nan,
                "mom_12_1": np.nan,
            }
        )
        print(f"[3/4] kline rows={len(kf)}, success=0")
    else:
        print("[3/4] fetch kline features...")
        kf = fetch_kline_features(quote["code"].tolist(), max_workers=8)
        print(f"[3/4] kline rows={len(kf)}, success={(kf['kline_ok'] == 1).sum()}")

    print("[4/4] merge, filter, score...")
    df = quote.merge(org[["secucode", "listing_date", "industry"]], on="secucode", how="left")
    df = df.merge(fin, on=["secucode", "code"], how="left")
    df = df.merge(kf, on="code", how="left")

    df["industry"] = df["industry"].fillna("未知行业")
    df["is_st"] = df["name"].astype(str).str.upper().str.contains("ST", na=False)

    calendar_days = (pd.Timestamp(as_of) - pd.to_datetime(df["listing_date"], errors="coerce")).dt.days
    df["pass_listing"] = np.where(df["listed_days_kline"].notna(), df["listed_days_kline"] >= 250, calendar_days >= 365)

    df["avg_amount_60_used"] = df["avg_amount_60"].where(df["avg_amount_60"].notna(), df["deal_amount"])
    df["pass_liquidity"] = df["avg_amount_60_used"].fillna(0) >= 50_000_000

    df["pass_notice_lag"] = (
        pd.to_datetime(df["notice_date"], errors="coerce").notna()
        & ((pd.Timestamp(as_of) - pd.to_datetime(df["notice_date"], errors="coerce")).dt.days >= 20)
    )

    df["hard_pass"] = (~df["is_st"]) & df["pass_listing"] & df["pass_liquidity"] & df["pass_notice_lag"]

    base = df[df["hard_pass"]].copy()

    raw_cols = [
        "pe",
        "pb",
        "total_mv",
        "fcff_back",
        "roe",
        "gross_margin",
        "cfo_to_np",
        "debt_ratio",
        "rev_yoy",
        "profit_yoy",
        "ret_60d",
        "ret_250d",
        "amp",
    ]
    base["raw_missing_count"] = base[raw_cols].isna().sum(axis=1)
    base = base[base["raw_missing_count"] <= 4].copy()

    scored = score_factors(base)
    write_outputs(scored, df, run_ts)

    print("done")
    print(f"universe_total={len(df)}")
    print(f"hard_pass={int(df['hard_pass'].sum())}")
    print(f"final_passed={len(scored)}")


if __name__ == "__main__":
    main()
