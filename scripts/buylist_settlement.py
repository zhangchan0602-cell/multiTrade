import pandas as pd

from buylist_io import (
    BUYLIST_DIR,
    ensure_buylist_sidecars,
    format_currency,
    format_signed_currency,
    format_signed_percent,
    get_settled_markdown_path,
    load_buylist_record,
    write_settlement_sidecars,
)
from screen_common import fetch_daily_snapshot

SETTLEMENT_SUMMARY_PREFIX = "[tail-settle-summary]"
DEFAULT_TAIL_SETTLE_HOLD_SESSIONS = 3


def _build_intraday_quote_map(quote: pd.DataFrame) -> dict:
    quote_map = {}
    for _, row in quote[["code", "close"]].dropna(subset=["code", "close"]).iterrows():
        quote_map[str(row["code"]).zfill(6)] = float(row["close"])
    return quote_map


def _build_daily_close_map(trade_date: pd.Timestamp) -> dict:
    daily = fetch_daily_snapshot(trade_date.strftime("%Y%m%d"))
    if daily.empty:
        return {}
    daily = daily[["ts_code", "close"]].copy()
    daily["code"] = daily["ts_code"].astype(str).str.split(".").str[0].str.zfill(6)
    daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
    daily = daily.dropna(subset=["code", "close"])
    return dict(zip(daily["code"], daily["close"]))


def _resolve_cutoff_buy_date(current_trade_date: pd.Timestamp, hold_sessions: int) -> tuple[pd.Timestamp | None, list[str]]:
    current = pd.Timestamp(current_trade_date).normalize()
    trade_dates = [current]
    cursor = current - pd.Timedelta(days=1)
    checked_days = 0
    max_checked_days = max(hold_sessions * 10, 30)

    while len(trade_dates) < hold_sessions + 1 and checked_days < max_checked_days:
        if not fetch_daily_snapshot(cursor.strftime("%Y%m%d")).empty:
            trade_dates.append(cursor.normalize())
        cursor -= pd.Timedelta(days=1)
        checked_days += 1

    if len(trade_dates) < hold_sessions + 1:
        return None, []
    return trade_dates[-1], [trade_date.strftime("%Y-%m-%d") for trade_date in trade_dates]


def _build_settlement_record(buylist: dict, settle_date: pd.Timestamp, hold_sessions: int, intraday_quote_map: dict, daily_close_map: dict) -> dict:
    rows = []
    tail_price_rows = 0
    daily_close_rows = 0
    pending_rows = 0
    settled_buy_amount = 0.0
    total_buy_amount = 0.0
    total_current_value = 0.0

    for item in buylist.get("rows") or []:
        code = str(item.get("code") or "").zfill(6)
        buy_amount = float(item.get("buyAmount") or 0.0)
        shares = int(item.get("shares") or 0)
        buy_price = float(item.get("buyPrice") or 0.0)
        total_buy_amount += buy_amount

        settlement_price = intraday_quote_map.get(code)
        price_source = "tail"
        status = "已结算"
        if settlement_price is None:
            settlement_price = daily_close_map.get(code)
            price_source = "daily_close"
        if settlement_price is None:
            status = "待补价"
            price_source = "pending"
            pending_rows += 1
            current_value = None
            pnl_amount = None
            pnl_pct = None
        else:
            settlement_price = float(settlement_price)
            current_value = float(settlement_price * shares)
            pnl_amount = float(current_value - buy_amount)
            pnl_pct = float(pnl_amount / buy_amount * 100.0) if buy_amount > 0 else 0.0
            settled_buy_amount += buy_amount
            total_current_value += current_value
            if price_source == "tail":
                tail_price_rows += 1
            else:
                daily_close_rows += 1

        rows.append(
            {
                "rank": int(item.get("rank") or len(rows) + 1),
                "code": code,
                "name": item.get("name") or code,
                "industry": item.get("industry") or "",
                "buyPrice": buy_price,
                "settlementPrice": settlement_price,
                "shares": shares,
                "buyAmount": buy_amount,
                "currentValue": current_value,
                "pnlAmount": pnl_amount,
                "pnlPct": pnl_pct,
                "priceSource": price_source,
                "status": status,
            }
        )

    total_pnl = float(total_current_value - settled_buy_amount)
    overall_rate = float(total_pnl / settled_buy_amount * 100.0) if settled_buy_amount > 0 else None
    return {
        "version": 1,
        "title": f"买入计划 {buylist['buyDate']} — 结算",
        "buyDate": buylist["buyDate"],
        "settleDate": settle_date.strftime("%Y-%m-%d"),
        "holdSessions": hold_sessions,
        "referenceSource": buylist.get("referenceSource") or "",
        "rows": rows,
        "summary": {
            "totalRows": len(rows),
            "settledRows": len(rows) - pending_rows,
            "pendingRows": pending_rows,
            "tailPriceRows": tail_price_rows,
            "dailyCloseRows": daily_close_rows,
            "totalBuyAmount": total_buy_amount,
            "settledBuyAmount": settled_buy_amount,
            "totalCurrentValue": total_current_value,
            "totalPnl": total_pnl,
            "overallRatePct": overall_rate,
        },
    }


def render_settlement_markdown(settlement: dict) -> str:
    summary = settlement["summary"]
    reference_source = settlement["referenceSource"] or f"{settlement['buyDate']} 买入参考价"
    lines = [
        f"# {settlement['title']}",
        "",
        f"> 买入日期：{settlement['buyDate']}  ",
        f"> 结算日期：{settlement['settleDate']}  ",
        f"> 持有交易日：{settlement['holdSessions']}（达到 T+3 后按尾盘价格优先结算）  ",
        f"> 参考价格来源：{reference_source}  ",
        f"> 现价来源：{settlement['settleDate']} 尾盘价格优先，缺失回退当日日线收盘",
    ]
    if summary["pendingRows"] > 0:
        lines.append(
            f"> 待补价：{summary['pendingRows']} 支，整体收益率按已结算 {summary['settledRows']} 支计算"
        )

    lines.extend(
        [
            "",
            "| 排名 | 代码 | 名称 | 买入价（元） | 结算价（元） | 股数 | 买入金额（元） | 现值（元） | 盈亏（元） | 收益率 | 价格来源 | 状态 |",
            "|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )

    for row in settlement["rows"]:
        lines.append(
            "| {rank} | {code} | {name} | {buy_price} | {settle_price} | {shares} | {buy_amount} | {current_value} | {pnl} | {rate} | {source} | {status} |".format(
                rank=row["rank"],
                code=row["code"],
                name=row["name"],
                buy_price=format_currency(float(row["buyPrice"] or 0.0)),
                settle_price=format_currency(float(row["settlementPrice"])) if row["settlementPrice"] is not None else "-",
                shares=row["shares"],
                buy_amount=format_currency(float(row["buyAmount"] or 0.0)),
                current_value=format_currency(float(row["currentValue"])) if row["currentValue"] is not None else "-",
                pnl=format_signed_currency(float(row["pnlAmount"])) if row["pnlAmount"] is not None else "-",
                rate=format_signed_percent(float(row["pnlPct"])) if row["pnlPct"] is not None else "-",
                source={"tail": "尾盘", "daily_close": "收盘", "pending": "待补"}[row["priceSource"]],
                status=row["status"],
            )
        )

    lines.extend(
        [
            "",
            f"**合计买入金额：￥{format_currency(float(summary['totalBuyAmount'] or 0.0))} 元**  ",
            f"**已结算买入金额：￥{format_currency(float(summary['settledBuyAmount'] or 0.0))} 元**  ",
            f"**已结算现值：￥{format_currency(float(summary['totalCurrentValue'] or 0.0))} 元**  ",
            f"**已结算盈亏：{format_signed_currency(float(summary['totalPnl'] or 0.0))} 元**  ",
            f"**已结算收益率：{format_signed_percent(float(summary['overallRatePct'] or 0.0)) if summary['overallRatePct'] is not None else '-'}**",
        ]
    )
    return "\n".join(lines) + "\n"


def auto_settle_due_buylists(quote: pd.DataFrame, quote_is_intraday: bool, hold_sessions: int = DEFAULT_TAIL_SETTLE_HOLD_SESSIONS) -> dict:
    summary = {
        "status": "ok",
        "message": "",
        "currentTradeDate": None,
        "cutoffBuyDate": None,
        "holdSessions": hold_sessions,
        "migratedCount": 0,
        "buylistScanCount": 0,
        "dueCount": 0,
        "settledCount": 0,
        "alreadySettledCount": 0,
        "pendingFileCount": 0,
        "pendingSymbolCount": 0,
        "settledFiles": [],
        "skippedFiles": [],
    }
    if quote.empty:
        summary["status"] = "skipped"
        summary["message"] = "empty quote, skip auto settlement"
        return summary
    if not quote_is_intraday:
        summary["status"] = "skipped"
        summary["message"] = "intraday quote unavailable, skip auto settlement"
        return summary
    if not BUYLIST_DIR.exists():
        summary["status"] = "skipped"
        summary["message"] = "buylist dir not found"
        return summary

    sidecar_summary = ensure_buylist_sidecars(BUYLIST_DIR)
    summary["migratedCount"] = int(sidecar_summary["migratedCount"])
    summary["buylistScanCount"] = int(sidecar_summary["scannedCount"])
    for error in sidecar_summary["errors"]:
        summary["skippedFiles"].append(error)

    current_trade_date = pd.to_datetime(quote["trade_date"].dropna().iloc[0], errors="coerce")
    if pd.isna(current_trade_date):
        summary["status"] = "error"
        summary["message"] = "invalid current trade date"
        return summary
    current_trade_date = current_trade_date.normalize()
    summary["currentTradeDate"] = current_trade_date.strftime("%Y-%m-%d")

    cutoff_buy_date, recent_trade_dates = _resolve_cutoff_buy_date(current_trade_date, hold_sessions)
    if cutoff_buy_date is None:
        summary["status"] = "error"
        summary["message"] = "unable to resolve T+3 cutoff trade date"
        return summary
    summary["cutoffBuyDate"] = cutoff_buy_date.strftime("%Y-%m-%d")
    summary["recentTradeDates"] = recent_trade_dates

    intraday_quote_map = _build_intraday_quote_map(quote)
    daily_close_map = _build_daily_close_map(current_trade_date)

    for markdown_path in sorted(BUYLIST_DIR.glob("*buy.md")):
        settled_markdown_path = get_settled_markdown_path(markdown_path)
        if settled_markdown_path.exists():
            summary["alreadySettledCount"] += 1
            continue

        try:
            buylist = load_buylist_record(markdown_path)
        except Exception as exc:
            summary["skippedFiles"].append({"fileName": markdown_path.name, "reason": str(exc)})
            continue

        buy_date = pd.to_datetime(buylist.get("buyDate"), errors="coerce")
        if pd.isna(buy_date) or buy_date.normalize() > cutoff_buy_date:
            continue

        summary["dueCount"] += 1
        settlement = _build_settlement_record(
            buylist,
            settle_date=current_trade_date,
            hold_sessions=hold_sessions,
            intraday_quote_map=intraday_quote_map,
            daily_close_map=daily_close_map,
        )
        write_settlement_sidecars(markdown_path, settlement, render_settlement_markdown(settlement))

        pending_rows = int(settlement["summary"]["pendingRows"])
        summary["settledCount"] += 1
        summary["pendingSymbolCount"] += pending_rows
        if pending_rows > 0:
            summary["pendingFileCount"] += 1
        summary["settledFiles"].append(
            {
                "fileName": markdown_path.name,
                "buyDate": buylist["buyDate"],
                "settledRows": int(settlement["summary"]["settledRows"]),
                "pendingRows": pending_rows,
                "tailPriceRows": int(settlement["summary"]["tailPriceRows"]),
                "dailyCloseRows": int(settlement["summary"]["dailyCloseRows"]),
            }
        )

    if summary["dueCount"] == 0:
        summary["message"] = "no T+3 buylist due for settlement"
    elif summary["pendingFileCount"] > 0:
        summary["message"] = "settlement completed with pending prices"
    else:
        summary["message"] = "settlement completed"
    return summary