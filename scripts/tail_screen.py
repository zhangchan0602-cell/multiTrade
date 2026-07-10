#!/usr/bin/env python3
"""
全A（不含科创板）短线多因子-收盘资金版筛选。

每日收盘后运行，基于收盘价、成交量和资金流入流出筛选。
沿用 tail_* 输出文件名，避免看板和历史路径迁移。
"""

from short_screen import run_tail_screen


def main() -> None:
    run_tail_screen()


if __name__ == "__main__":
    main()
