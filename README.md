# React 看板项目

全A短线多因子选股系统，含盘后版与收盘资金版筛选、T+5 历史回测、买入清单结算，以及 React 看板展示。

## 启动

```bash
export TUSHARE_TOKEN=66e342740a8e93dbd06c91d972dd4313a4e3d166e7cff1dbacb6d2bb
npm install
npm run api   # 启动本地 API，默认 http://127.0.0.1:8787
npm run dev
```

Python 脚本和本地 API 都会读取 `TUSHARE_TOKEN`，未设置时无法抓取最新股票数据。ok

**定时任务（crontab）**

```cron
# 15:30 跑盘后版
30 15 * * 1-5  cd /path/to/multitrade/scripts && python3 short_screen.py

# 15:40 跑收盘资金版
40 15 * * 1-5  cd /path/to/multitrade/scripts && python3 tail_screen.py
```

## 构建

```bash
npm run build
npm run preview
```

构建后的 `dist/index.html` 也可以直接打开；资源路径已改成相对路径，页面数据会随构建一起打包进去。

## 页面说明

- `#/ops`：操作界面，可从统一入口选择盘后版、收盘资金版、RPS双90并查看当天 Top5
- `#/top20`：展示最新 `docs/list/history/short/YYYY-MM-DD/short_top5*.csv` 与 `docs/list/tail_top5*.csv`
  - 支持评分历史（按日期）
  - 自动标记新增项
  - 自动显示本期移出前20项（按最近两期对比）
- `#/myplan`：展示 `myplan.md` 内容

## 输出文档说明

### 盘后版（`short_screen.py`，15:30 运行）

| 文件 | 说明 |
|------|------|
| `docs/list/history/short/YYYY-MM-DD/short_passed.csv / .md` | 当天盘后版通过全部过滤条件的股票完整列表及因子分 |
| `docs/list/history/short/YYYY-MM-DD/short_top20.csv / .md` | 当天盘后版综合评分前 20 名 |
| `docs/list/history/short/YYYY-MM-DD/short_top5.csv / .md` | 当天盘后版综合评分前 5 名，作为次日择机买入参考 |
| `docs/list/history/short/YYYY-MM-DD/short_summary.md` | 当天盘后版筛选摘要（数据口径、因子权重、运行时间等） |

### 收盘资金版（`tail_screen.py`，15:40 运行）

| 文件 | 说明 |
|------|------|
| `docs/list/tail_passed.csv / .md` | 通过全部过滤条件的股票完整列表 |
| `docs/list/tail_top20.csv / .md` | 综合评分前 20 名 |
| `docs/list/tail_top5.csv / .md` | 综合评分前 5 名，结合收盘价与资金流入流出 |
| `docs/list/tail_summary.md` | 本次筛选摘要（含收盘行情与资金流口径说明） |

### 历史快照

每次运行后，上述文件会自动备份到带时间戳的历史目录：

| 路径 | 说明 |
|------|------|
| `docs/list/history/short_YYYYMMDD-HHMM_*.{csv,md}` | 盘后版各次运行历史快照 |
| `docs/list/history/short/YYYY-MM-DD/short_*.{csv,md}` | 盘后版按交易日归档（供 T+5 历史回测扫描） |
| `docs/list/history/tail_YYYYMMDD-HHMM_*.{csv,md}` | 收盘资金版各次运行历史快照 |

### T+5 历史回测（`postclose_t3_history.py`，兼容旧脚本名）

| 文件 | 说明 |
|------|------|
| `docs/list/short_t5_history_trades.csv` | 历史已平仓交易明细（CSV） |
| `docs/list/short_t5_history_equity.csv` | 历史每日权益曲线（CSV） |
| `docs/list/short_t5_history_open_positions.csv` | 回测截止日仍未平仓持仓（CSV） |
| `docs/list/short_t5_history.md` | T+5 口径摘要与规则说明（Markdown） |

### 统一组合历史回测（`strategy_backtest.py --portfolio`）

```bash
npm run backtest:portfolio
```

组合回测会按历史交易日使用 Tushare 数据重跑策略信号，当前支持 `rps90`、`short`、`tail`、`leader`，并按以下规则模拟：单票 10 万预算、整百股交易、最多同时持有 3 只、不限制持有期限、涨停即出、未涨停时单日回撤 5% 即出、跌破 5 日线止损。

| 文件 | 说明 |
|------|------|
| `docs/list/strategy_portfolio_backtest_trades.csv` | 已平仓交易明细 |
| `docs/list/strategy_portfolio_backtest_equity.csv` | 每日现金、持仓市值与权益曲线 |
| `docs/list/strategy_portfolio_backtest_open_positions.csv` | 回测结束时仍未平仓持仓 |
| `docs/list/strategy_portfolio_backtest_summary.md` | 策略汇总统计 |

### 买入清单与结算（`buylist_io.py` / `buylist_settlement.py`）

| 文件 | 说明 |
|------|------|
| `docs/list/history/buylist/YYYYMMDD buy.md` | 手动维护的买入清单，记录买入价、手数等 |
| `docs/list/history/buylist/YYYYMMDD buy.settled.md` | 自动结算后的买入清单，含持仓盈亏与结算价 |
| `docs/list/history/buylist/YYYYMMDD buy.settled.json` | 结算数据的结构化存档（JSON） |
| `docs/list/history/buylist/YYYYMMDD buy.settled.csv` | 结算明细（CSV） |

> 盘后版运行时若检测到到期的买入清单，会自动触发结算并在当天目录下的 `short_summary.md` 中追加结算摘要。

## 历史数据规则

`Top20` 页默认加载：

- `docs/list/history/short/YYYY-MM-DD/short_top5.csv`（当前盘后版，自动取最新日期目录）
- `docs/list/tail_top5.csv`（当前收盘资金版）
- `src/data/top20_history.json`（历史快照）

`top20_history.json` 结构示例：

```json
[
  {
    "date": "2026-03-20",
    "items": [
      { "rank": 1, "code": "603444", "name": "吉比特", "score_100": 100, "score_raw": 1.4524 }
    ]
  }
]
```

当存在至少两期时，会自动计算新增/移出与排名变化。
