#!/usr/bin/env python3
"""
全A（不含科创板）短线多因子-尾盘版筛选。

14:30-14:45 运行，作为 14:50 买入操作的参考。
使用尾盘版专属权重（偏重近期冲量与当日量能爆发）与交易过滤器。
"""

from short_screen import run_tail_screen


def main() -> None:
    run_tail_screen()


if __name__ == "__main__":
    main()
