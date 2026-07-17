#!/usr/bin/env python3
"""Download the CSI semiconductor index daily bars used by the semiconductor page."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from download_kechuang_index import INDEX_CACHE_DIR, download_index
from screen_common import get_latest_trade_date


INDEX_CODE = "H30184.CSI"
START_DATE = "20170101"


def main() -> None:
    end_date = get_latest_trade_date()
    df = download_index(INDEX_CODE, START_DATE, end_date)
    if df.empty:
        raise SystemExit(f"no index data returned for {INDEX_CODE} {START_DATE}..{end_date}")

    INDEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INDEX_CACHE_DIR / f"{INDEX_CODE.split('.')[0]}.csv"
    df.to_csv(out_path, index=False)
    print(f"[semiconductor-index] {INDEX_CODE} {df['trade_date'].iloc[0]} -> {df['trade_date'].iloc[-1]} rows={len(df)}")
    print(f"[semiconductor-index] saved {out_path}")


if __name__ == "__main__":
    main()
