# 全A（不含科创板）短线多因子筛选统计

- 生成时间: 2026-04-23 11:15:53
- 全A（沪主板+深主板+创业板）初始样本: 4406
- K线成功样本: 2286
- 硬过滤后样本: 2099
- 最终符合样本: 1150

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
- 短线动量: 5日ャ10日、20日收益 + 20日均线突破 + 启动加速度
- 活跃度: 5日/20日成交额放大比 + 5日换手率
- 稳定性: 10日波动率（取负）+ 20日最大回撤 + 20日上涨胜率 + 底部收敛度
- 启动检测: 底部收敛度（前15日低波动）+ 启动加速度（近5日涨幅＞前15日涨幅）
- 百分制得分: 按原始综合分在全样本中的线性排名换算到0-100
