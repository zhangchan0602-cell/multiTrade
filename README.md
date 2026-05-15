# React 看板项目

全A短线多因子选股系统，含盘后版与尾盘版筛选、T+3 历史回测、买入清单结算，以及 React 看板展示。

## 启动

```bash
export TUSHARE_TOKEN=你的token
npm install
npm run api   # 启动本地 API，默认 http://127.0.0.1:8787
npm run dev
```

Python 脚本和本地 API 都会读取 `TUSHARE_TOKEN`，未设置时无法抓取最新股票数据。

**定时任务（crontab）**

```cron
# 14:30 跑尾盘版
30 14 * * 1-5  cd /path/to/multitrade/scripts && python3 tail_screen.py

# 15:30 跑盘后版
30 15 * * 1-5  cd /path/to/multitrade/scripts && python3 short_screen.py
```

## 构建

```bash
npm run build
npm run preview
```

构建后的 `dist/index.html` 也可以直接打开；资源路径已改成相对路径，页面数据会随构建一起打包进去。

## 页面说明

- `#/ops`：操作界面，可执行盘后版/尾盘版脚本并查看当天 Top5
- `#/top20`：展示 `docs/list/short_top5*.csv` 与 `docs/list/tail_top5*.csv`
  - 支持评分历史（按日期）
  - 自动标记新增项
  - 自动显示本期移出前20项（按最近两期对比）
- `#/myplan`：展示 `myplan.md` 内容

## 输出文档说明

### 盘后版（`short_screen.py`，15:30 运行）

| 文件 | 说明 |
|------|------|
| `docs/list/short_passed.csv / .md` | 通过全部过滤条件的股票完整列表及因子分 |
| `docs/list/short_top20.csv / .md` | 综合评分前 20 名 |
| `docs/list/short_top5.csv / .md` | 综合评分前 5 名，作为次日择机买入参考 |
| `docs/list/short_summary.md` | 本次筛选摘要（数据口径、因子权重、运行时间等） |

### 尾盘版（`tail_screen.py`，14:30 运行）

| 文件 | 说明 |
|------|------|
| `docs/list/tail_passed.csv / .md` | 通过全部过滤条件的股票完整列表 |
| `docs/list/tail_top20.csv / .md` | 综合评分前 20 名 |
| `docs/list/tail_top5.csv / .md` | 综合评分前 5 名，作为当日 14:50 操作参考 |
| `docs/list/tail_summary.md` | 本次筛选摘要（含盘中快照口径与是否降级说明） |

### 历史快照

每次运行后，上述文件会自动备份到带时间戳的历史目录：

| 路径 | 说明 |
|------|------|
| `docs/list/history/short_YYYYMMDD-HHMM_*.{csv,md}` | 盘后版各次运行历史快照 |
| `docs/list/history/short/YYYY-MM-DD/short_*.{csv,md}` | 盘后版按交易日归档（供 T+3 回测扫描） |
| `docs/list/history/tail_YYYYMMDD-HHMM_*.{csv,md}` | 尾盘版各次运行历史快照 |

### T+3 回测（`postclose_t3_history.py`）

| 文件 | 说明 |
|------|------|
| `docs/list/short_t3_history.csv` | 历史各交易日 Top5 及 T+3 结算收益（CSV） |
| `docs/list/short_t3_history.md` | 同上，Markdown 格式，含汇总统计 |

### 买入清单与结算（`buylist_io.py` / `buylist_settlement.py`）

| 文件 | 说明 |
|------|------|
| `docs/list/history/buylist/YYYYMMDD buy.md` | 手动维护的买入清单，记录买入价、手数等 |
| `docs/list/history/buylist/YYYYMMDD buy.settled.md` | 自动结算后的买入清单，含持仓盈亏与结算价 |
| `docs/list/history/buylist/YYYYMMDD buy.settled.json` | 结算数据的结构化存档（JSON） |
| `docs/list/history/buylist/YYYYMMDD buy.settled.csv` | 结算明细（CSV） |

> 盘后版运行时若检测到到期的买入清单，会自动触发结算并在 `short_summary.md` 中追加结算摘要。

## 历史数据规则

`Top20` 页默认加载：

- `docs/list/short_top5.csv`（当前盘后版）
- `docs/list/tail_top5.csv`（当前尾盘版）
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
