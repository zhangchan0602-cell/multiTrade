import json
import re
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from screen_common import OUTPUT_DIR

BUYLIST_DIR = OUTPUT_DIR / "history" / "buylist"


def split_markdown_row(line: str) -> List[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def parse_numeric_cell(value) -> float:
    text = str(value or "").replace(",", "").replace("￥", "").replace("%", "").strip()
    if not text:
        return np.nan
    try:
        return float(text)
    except ValueError:
        return np.nan


def read_row_value(row: dict, keys: List[str], fallback: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return fallback


def format_currency(value: float) -> str:
    return f"{value:,.2f}"


def format_signed_currency(value: float) -> str:
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:,.2f}"


def format_signed_percent(value: float) -> str:
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.2f}%"


def get_buylist_json_path(markdown_path: Path) -> Path:
    return markdown_path.with_suffix(".json")


def get_buylist_csv_path(markdown_path: Path) -> Path:
    return markdown_path.with_suffix(".csv")


def get_settled_markdown_path(markdown_path: Path) -> Path:
    return markdown_path.with_name(f"{markdown_path.stem}.settled.md")


def get_settled_json_path(markdown_path: Path) -> Path:
    return markdown_path.with_name(f"{markdown_path.stem}.settled.json")


def get_settled_csv_path(markdown_path: Path) -> Path:
    return markdown_path.with_name(f"{markdown_path.stem}.settled.csv")


def _to_optional_float(value) -> float | None:
    return float(value) if np.isfinite(value) else None


def _sanitize_for_json(value):
    if isinstance(value, dict):
        return {key: _sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _build_buylist_summary(rows: List[dict]) -> dict:
    total_buy_amount = sum(float(row.get("buyAmount") or 0.0) for row in rows)
    total_shares = sum(int(row.get("shares") or 0) for row in rows)

    budget_values = [float(row["budget"]) for row in rows if row.get("budget") is not None]
    balance_values = [float(row["balance"]) for row in rows if row.get("balance") is not None]

    return {
        "rowCount": len(rows),
        "totalShares": total_shares,
        "totalBuyAmount": total_buy_amount,
        "totalBudget": sum(budget_values) if budget_values else None,
        "totalBalance": sum(balance_values) if balance_values else None,
    }


def parse_buylist_markdown(file_path: Path) -> dict:
    lines = file_path.read_text(encoding="utf-8").splitlines()
    title = next((line.strip() for line in lines if line.startswith("# ")), file_path.stem)
    title_date_match = re.search(r"(\d{4}-\d{2}-\d{2})", title)
    if not title_date_match:
        raise RuntimeError(f"未识别买入日期: {file_path.name}")

    buy_date = pd.to_datetime(title_date_match.group(1), errors="coerce")
    if pd.isna(buy_date):
        raise RuntimeError(f"买入日期无效: {file_path.name}")

    reference_source = ""
    for line in lines:
        if line.startswith("> 参考价格来源："):
            reference_source = line.replace("> 参考价格来源：", "", 1).strip()
            break

    table_start = next((idx for idx, line in enumerate(lines) if line.strip().startswith("|")), -1)
    if table_start < 0 or table_start + 2 >= len(lines):
        raise RuntimeError(f"未找到买入表格: {file_path.name}")

    headers = split_markdown_row(lines[table_start])
    rows = []
    for line in lines[table_start + 2 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        values = split_markdown_row(stripped)
        row = {header: (values[idx] if idx < len(values) else "") for idx, header in enumerate(headers)}

        code = read_row_value(row, ["代码", "code"]).zfill(6)
        shares_value = parse_numeric_cell(read_row_value(row, ["数量（股）", "股数"], "0"))
        shares = int(shares_value) if np.isfinite(shares_value) else 0
        buy_price = parse_numeric_cell(read_row_value(row, ["买入价（元）", "当前价（元）", "参考价（元）"]))
        buy_amount = parse_numeric_cell(read_row_value(row, ["买入金额（元）", "金额（元）", "买入金额"], ""))
        if not np.isfinite(buy_amount) and np.isfinite(buy_price) and shares > 0:
            buy_amount = float(buy_price * shares)
        if len(code) != 6 or shares <= 0 or not np.isfinite(buy_price) or not np.isfinite(buy_amount):
            continue

        rank_value = parse_numeric_cell(read_row_value(row, ["排名"], str(len(rows) + 1)))
        rows.append(
            {
                "rank": int(rank_value) if np.isfinite(rank_value) else (len(rows) + 1),
                "code": code,
                "name": read_row_value(row, ["名称"], code),
                "industry": read_row_value(row, ["行业"]),
                "buyPrice": float(buy_price),
                "shares": shares,
                "buyAmount": float(buy_amount),
                "budget": _to_optional_float(parse_numeric_cell(read_row_value(row, ["预算（元）"]))),
                "balance": _to_optional_float(parse_numeric_cell(read_row_value(row, ["余额（元）"]))),
                "currentPrice": _to_optional_float(parse_numeric_cell(read_row_value(row, ["今日价（元）", "现价（元）"]))),
                "pnlAmount": _to_optional_float(parse_numeric_cell(read_row_value(row, ["盈亏（元）"]))),
                "pnlPct": _to_optional_float(parse_numeric_cell(read_row_value(row, ["盈亏率", "收益率"]))),
            }
        )

    if not rows:
        raise RuntimeError(f"未解析出可结算持仓: {file_path.name}")

    record = {
        "version": 1,
        "title": title,
        "buyDate": buy_date.strftime("%Y-%m-%d"),
        "referenceSource": reference_source,
        "sourceMarkdown": file_path.name,
        "rows": rows,
        "summary": _build_buylist_summary(rows),
    }
    return _sanitize_for_json(record)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(_sanitize_for_json(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: List[dict]) -> None:
    if rows:
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")


def write_buylist_sidecars(record: dict, markdown_path: Path) -> None:
    _write_json(get_buylist_json_path(markdown_path), record)
    _write_csv(get_buylist_csv_path(markdown_path), record.get("rows") or [])


def write_settlement_sidecars(markdown_path: Path, settlement_record: dict, markdown_text: str) -> None:
    get_settled_markdown_path(markdown_path).write_text(markdown_text, encoding="utf-8")
    _write_json(get_settled_json_path(markdown_path), settlement_record)
    _write_csv(get_settled_csv_path(markdown_path), settlement_record.get("rows") or [])


def load_buylist_record(markdown_path: Path) -> dict:
    json_path = get_buylist_json_path(markdown_path)
    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))

    record = parse_buylist_markdown(markdown_path)
    write_buylist_sidecars(record, markdown_path)
    return record


def ensure_buylist_sidecars(directory: Path = BUYLIST_DIR) -> dict:
    summary = {
        "scannedCount": 0,
        "migratedCount": 0,
        "errors": [],
    }
    if not directory.exists():
        return summary

    for markdown_path in sorted(directory.glob("*buy.md")):
        summary["scannedCount"] += 1
        json_path = get_buylist_json_path(markdown_path)
        csv_path = get_buylist_csv_path(markdown_path)
        if json_path.exists() and csv_path.exists():
            continue
        try:
            record = parse_buylist_markdown(markdown_path)
            write_buylist_sidecars(record, markdown_path)
            summary["migratedCount"] += 1
        except Exception as exc:
            summary["errors"].append({"fileName": markdown_path.name, "reason": str(exc)})
    return summary