# A股中线多因子选股模型（v1）

> 适用风格：偏稳健的中线量化选股
>  
> 更新日期：2026-03-23

## 1. 模型目标

在控制回撤和换手的前提下，构建一个可回测、可执行、可迭代的A股多因子选股框架。

## 2. 股票池（Universe）

基础股票池：全A（不含北交所）

剔除规则：
- `ST/*ST` 股票
- 上市不足 `250` 个交易日
- 近 `60` 个交易日日均成交额低于 `5000万`
- 长期停牌或无法正常交易标的

数据处理约束：
- 财务数据统一按 `20` 个交易日滞后入模，避免未来函数。

## 3. 因子配置与权重

总分由5大类因子构成：

- 价值（Value）`25%`
  - `E/P(TTM)`：40%
  - `B/P`：30%
  - `FCF Yield`：30%

- 质量（Quality）`30%`
  - `ROE`：35%
  - 毛利率：25%
  - `经营现金流/净利润`：20%
  - `资产负债率（取负）`：20%

- 成长（Growth）`15%`
  - 营收同比：50%
  - 净利同比：50%

- 动量（Momentum）`20%`
  - 过去 `12-1` 个月收益率

- 低波（LowVol）`10%`
  - 60日波动率（取负）

## 4. 因子预处理与打分

每个原始因子按以下步骤处理：

1. 去极值：双侧 `2.5%` winsorize
2. 标准化：行业内 `z-score`
3. 合成总分：

```text
Score = 0.25*Value + 0.30*Quality + 0.15*Growth + 0.20*Momentum + 0.10*LowVol
```

## 5. 组合构建与调仓

- 调仓频率：每月第1个交易日
- 选股数量：总分前 `30` 只
- 权重方案：等权
- 单票权重上限：`5%`
- 行业暴露约束：相对基准行业权重偏离不超过 `±10%`
- 单次换手上限：`25%`（超出部分顺延下月）

## 6. 卖出与风控规则

满足任一条件触发卖出：
- 个股综合分数跌出前 `40%`
- 出现明显财务风险信号（如业绩大幅下修）
- 个股止损触发：`-15%`（可按风险偏好调整为 `-10% ~ -20%`）

组合级建议风控：
- 组合最大回撤阈值（如 `12% ~ 15%`）触发降仓
- 市场极端波动阶段提高现金比例

## 7. 回测建议参数

- 回测区间：至少覆盖 `5-8` 年（包含上涨、震荡、下跌阶段）
- 交易成本：
  - 手续费：按实际券商费率
  - 滑点：建议按中低流动性分层设置
- 评估指标：
  - 年化收益、年化波动、夏普比率
  - 最大回撤
  - 超额收益与信息比率
  - 胜率、盈亏比、换手率

## 8. 可迭代方向（v2）

- 引入风格中性（市值/行业/贝塔中性）
- 增加事件因子（财报超预期、回购、机构调研）
- 增加择时层（指数趋势过滤）
- 使用机器学习进行非线性因子合成（如 `XGBoost`）

## 9. 参考文献

- Fama, E. F., & French, K. R. (1993). Common risk factors in the returns on stocks and bonds.  
  https://www.sciencedirect.com/science/article/pii/0304405X93900235

- Fama, E. F., & French, K. R. (2015). A five-factor asset pricing model.  
  https://www.sciencedirect.com/science/article/abs/pii/S0304405X14002323

- Novy-Marx, R. (2013). The other side of value: The gross profitability premium.  
  https://www.nber.org/papers/w15940

- Carhart, M. M. (1997). On persistence in mutual fund performance.  
  https://econpapers.repec.org/RePEc:bla:jfinan:v:52:y:1997:i:1:p:57-82

- Ken French Data Library  
  https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html

## 10. 免责声明

本文档仅用于量化研究与策略开发讨论，不构成任何投资建议。
