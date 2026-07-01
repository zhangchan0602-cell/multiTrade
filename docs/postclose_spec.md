# 短线多因子-盘后版打分规则技术规格

> 版本：v2.2
> 更新日期：2026-06-29
> 适用场景：全A（不含科创板）短线轮动候选池筛选，可用于 Spark 批处理打分

---

## 一、股票池与硬过滤

| 条件 | 参数 |
|------|------|
| 交易所 | 沪A主板 + 深A主板 + 创业板（不含科创板） |
| 剔除 ST | 名称包含 `ST` / `*ST` 的全部剔除 |
| 上市天数 | 真实 K 线记录 ≥ 60 个交易日 |
| 20日均成交额 | ≥ 1 亿元 |
| 收盘价 | ≥ 3 元 |
| 20日平均换手率 | 1% ~ 20% |

> **预筛说明**：拉取 K 线前先做宽松预筛（上市约 45 自然日、当日成交额 ≥ 3000万、价格 ≥ 2元、换手率 0.2%~35%），仅用于减少接口请求，最终入选以上表硬过滤为准。

---

## 二、原始因子计算

数据来源：日线 OHLCV（后复权），至少需要最近 25 根 K 线，主要使用近 20 根。

### 2.1 启动类因子

| 因子名 | 计算公式 |
|--------|---------|
| `ret_3` | `close[-1] / close[-4] - 1` |
| `ret_5` | `close[-1] / close[-6] - 1` |
| `accel` | `ret_5 - (close[-6] / close[-21] - 1)`，正值表示近期刚启动 |
| `high_breakout_20` | `close[-1] / max(high[-21:-1]) - 1`，突破前20日最高价幅度 |
| `close_position_20` | `(close[-1] - min(low[-20:])) / (max(high[-20:]) - min(low[-20:]))` |
| `price_vs_ma20` | `close[-1] / MA20 - 1` |
| `close_strength_5` | `mean((close - low) / (high - low), 近5日)` |

### 2.2 趋势类因子

| 因子名 | 计算公式 |
|--------|---------|
| `ret_10` | `close[-1] / close[-11] - 1` |
| `ret_20` | `close[-1] / close[-21] - 1` |
| `breakout_20` | `close[-1] / MA20 - 1`（与 `price_vs_ma20` 相同） |
| `ma_alignment_20` | `0.6 × (MA5/MA20 - 1) + 0.4 × (MA10/MA20 - 1)` |
| `trend_slope_20` | 近20日收盘价线性回归斜率 / 近20日均值 |
| `trend_efficiency_20` | `ret_20 / sum(abs(daily_ret[-20:]))`，趋势效率 |

### 2.3 活跃类因子

| 因子名 | 计算公式 |
|--------|---------|
| `amount_ratio_1_20` | `amount[-1] / mean(amount[-20:])` |
| `amount_ratio_3_20` | `mean(amount[-3:]) / mean(amount[-20:])` |
| `amount_ratio_5_20` | `mean(amount[-5:]) / mean(amount[-20:])` |
| `turnover_5` | `mean(turnover[-5:])` |
| `turnover_accel_5_20` | `mean(turnover[-5:]) / mean(turnover[-20:]) - 1` |

### 2.4 稳定类因子（越低越好，打分时取负）

| 因子名 | 计算公式 | 方向 |
|--------|---------|------|
| `vol_10` | `std(daily_ret[-10:])` | 取负 |
| `vol_20` | `std(daily_ret[-20:])` | 取负 |
| `downside_vol_20` | `std(daily_ret[-20:] 中负收益部分)` | 取负 |
| `max_drawdown_20` | `min(close[-20:] / running_max - 1)`，最大回撤 | 原始（已为负，越大越好） |
| `win_rate_20` | `mean(daily_ret[-20:] > 0)` | 正向 |
| `vol_base` | `std(daily_ret[-20:-5])`，底部横盘期日收益波动 | 取负 |
| `range_base` | `mean(high/low - 1, [-20:-5])`，底部日内振幅均值 | 取负 |
| `upper_shadow_5` | `mean((high - max(open, close)) / (high - low), 近5日)` | 取负 |

其中 `daily_ret[i] = close[i] / close[i-1] - 1`。

### 2.5 流动类因子

| 因子名 | 计算公式 |
|--------|---------|
| `avg_amount_20` | `mean(amount[-20:])`，20日均成交额 |
| `turnover_20` | `mean(turnover[-20:])`，20日平均换手率 |

---

## 三、标准化处理

每个原始因子依次执行：

1. **去极值**：双侧 2.5% Winsorize（替换为第 2.5 / 97.5 百分位值）
2. **行业内 z-score**：按一级行业分组，`z = (x - mean) / std`，不足 3 只的行业退化为全市场 z-score，inf / NaN 填 0

> 实盘脚本口径：先对通过硬过滤且具备真实 K 线的样本做横截面打分，再在排序结果上叠加交易执行过滤；若最终真实 K 线样本为空，才允许退化到纯行情候选。

---

## 四、分组得分合成

### 4.1 启动得分 `launch_score`

```
launch_score =
  0.18 × ret_3_z
+ 0.20 × ret_5_z
+ 0.18 × accel_z
+ 0.16 × high_breakout_20_z
+ 0.13 × close_position_20_z
+ 0.10 × price_vs_ma20_z
+ 0.05 × close_strength_5_z
```

### 4.2 趋势得分 `trend_score`

```
trend_score =
  0.18 × ret_10_z
+ 0.14 × ret_20_z
+ 0.18 × breakout_20_z
+ 0.20 × ma_alignment_20_z
+ 0.18 × trend_slope_20_z
+ 0.12 × trend_efficiency_20_z
```

### 4.3 活跃得分 `activity_score`

```
activity_score =
  0.24 × amount_ratio_1_20_z
+ 0.31 × amount_ratio_3_20_z
+ 0.20 × amount_ratio_5_20_z
+ 0.15 × turnover_5_z
+ 0.10 × turnover_accel_5_20_z
```

### 4.4 稳定得分 `stability_score`

```
stability_score =
  0.16 × (-vol_10_z)
+ 0.12 × (-vol_20_z)
+ 0.12 × (-downside_vol_20_z)
+ 0.18 × max_drawdown_20_z
+ 0.14 × win_rate_20_z
+ 0.16 × (-vol_base_z)
+ 0.08 × (-range_base_z)
+ 0.04 × (-upper_shadow_5_z)
```

### 4.5 流动得分 `liquidity_score`

```
liquidity_score =
  0.55 × avg_amount_20_z
+ 0.45 × turnover_20_z
```

### 4.6 总分 `score`

```
score =
  0.16 × launch_score
+ 0.22 × trend_score
+ 0.16 × activity_score
+ 0.40 × stability_score
+ 0.06 × liquidity_score
```

> 总分为行业 z-score 空间下的加权合成值，不做归一化。排序后按百分位映射为 `score_100`（0~100）方便展示。

---

## 五、交易执行过滤条件

所有条件均需同时满足，输出字段 `pass_next_2_3d_setup = true`。

另外增加两个最终执行地板：

- `momentum_score > 0` 且 `launch_score > -0.10`，用于剔除纯流动性驱动但启动不足的标的
- 盘后市场环境闸门通过；若未通过，不输出可交易 Top5，且不启用纯行情降级候选

盘后市场环境闸门：

| 指标 | 条件 |
|------|------|
| 全A上涨家数占比 | ≥ 28% |
| 全A中位涨跌幅 | ≥ -2% |
| 全A跌超5%占比 | ≤ 8% |

| 过滤维度 | 条件 |
|----------|------|
| **当日行情** | `change_rate` ∈ [-4%, 6.5%] 且 `amp` ≤ 16% |
| **启动窗口** | `ret_3` ∈ [-2%, 8%]<br>`ret_5` ∈ [-1%, 13%]<br>`ret_20` ∈ [-8%, 25%]<br>`accel` ≥ -5% |
| **突破形态** | `close_position_20` ∈ [60%, 105%]<br>`high_breakout_20` ∈ [-4%, 6%]<br>`price_vs_ma20` ∈ [-3%, 6.5%] |
| **活跃形态** | `amount_ratio_3_20` ∈ [1.05, 3.5]<br>`amount_ratio_5_20` ∈ [0.95, 3.0]<br>`turnover_5` ∈ [1.5%, 15%] |
| **风险控制** | `vol_20` ≤ 6%<br>`max_drawdown_20` ≥ -13%<br>`upper_shadow_5` ≤ 35% |

---

## 六、输出字段说明

| 字段 | 说明 |
|------|------|
| `score` | 加权合成总分（z-score 空间） |
| `score_100` | 百分位映射后的 0~100 分 |
| `rank` | 在通过硬过滤样本中的排名 |
| `launch_score` | 启动分组得分 |
| `trend_score` | 趋势分组得分 |
| `activity_score` | 活跃分组得分 |
| `stability_score` | 稳定分组得分 |
| `liquidity_score` | 流动分组得分 |
| `pass_next_2_3d_setup` | 是否通过交易执行过滤（布尔） |
| `pass_market_env` | 是否通过盘后市场环境闸门（布尔） |
| `market_up_ratio` | 当日全A上涨家数占比 |
| `market_median_change` | 当日全A涨跌幅中位数 |
| `market_down5_ratio` | 当日全A跌超5%占比 |
| `kline_fallback_used` | 是否使用了代理因子兜底（布尔） |

---

## 七、Spark 实现要点

1. **数据分区**：按 `trade_date` 分区，每日独立打分，避免跨日数据泄露
2. **K 线窗口**：每只股票需携带至少 25 根历史 K 线，使用 `Window.partitionBy("code").orderBy("trade_date").rowsBetween(-24, 0)` 收集数组
3. **行业 z-score**：`Window.partitionBy("trade_date", "industry")` 计算均值和标准差，行业内样本 < 3 只时退化为全市场分区
4. **去极值**：Winsorize 需先计算全量分位数（`percentile_approx`），再 clip
5. **打分列**：所有中间 z-score 列可在一次 `select` 中批量生成，避免多次 shuffle
6. **硬过滤**：先 filter 再打分，减少参与 z-score 计算的样本量
7. **兜底逻辑**：`kline_fallback` 代理因子精度较低，Spark 中建议仅用于监控，生产打分以真实 K 线为准
