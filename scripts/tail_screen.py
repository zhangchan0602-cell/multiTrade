#!/usr/bin/env python3
"""
全A（不含科创板）短线多因子-尾盘版筛选。

当前复用盘后版打分逻辑与阈值，仅独立输出尾盘版文件，便于后续单独演进。
"""

from postclose_screen import run_screen


def main() -> None:
    run_screen(
        model_name="短线多因子-尾盘版",
        output_stem="tail",
        trade_target_text="当前先复用盘后版逻辑，尾盘版输出用于独立跟踪与后续单独调参",
    )


if __name__ == "__main__":
    main()
