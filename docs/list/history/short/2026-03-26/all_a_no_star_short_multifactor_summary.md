# 全A（不含科创板）短线多因子筛选统计

- 生成时间: 2026-03-26 19:18:46
- 全A（沪主板+深主板+创业板）初始样本: 4405
- K线成功样本: 37
- 硬过滤后样本: 2064
- 最终符合样本: 26

## 输出文件

- `docs/list/all_a_no_star_short_multifactor_passed.csv`
- `docs/list/all_a_no_star_short_multifactor_passed.md`
- `docs/list/all_a_no_star_short_multifactor_top20.csv`
- `docs/list/all_a_no_star_short_multifactor_top20.md`
- `docs/list/all_a_no_star_short_multifactor_summary.md`

## 口径说明

- 股票池: 沪A主板 + 深A主板 + 创业板（不含科创板）
- ST过滤: 名称含 `ST` 或 `*ST` 剔除
- 上市天数: 优先用K线交易日，缺失时按上市日自然日近似
- 执行流动性: 20日均成交额优先，缺失时回退到当日成交额
- 短线动量: 5日、10日、20日收益与20日均线突破
- 活跃度: 5日/20日成交额放大比 + 5日换手率
- 稳定性: 10日波动率（取负）+ 20日最大回撤 + 20日上涨胜率
- 百分制得分: 按原始综合分在全样本中的线性排名换算到0-100
