# React 看板项目

## 启动

```bash
export TUSHARE_TOKEN=你的token
npm install
npm run api
npm run dev
```

Python 脚本和本地 API 都会读取 `TUSHARE_TOKEN`，未设置时无法抓取最新股票数据。

## 构建

```bash
npm run build
npm run preview
```

构建后的 `dist/index.html` 也可以直接打开；资源路径已改成相对路径，页面数据会随构建一起打包进去。

## 页面说明

- `#/ops`：操作界面，可执行盘后版/尾盘版脚本并查看当天 Top5
- `#/top20`：展示 `docs/list/postclose_top5*.csv` 与 `docs/list/tail_top5*.csv`
  - 支持评分历史（按日期）
  - 自动标记新增项
  - 自动显示本期移出前20项（按最近两期对比）
- `#/myplan`：展示 `myplan.md` 内容

## 历史数据规则

`Top20` 页默认加载：

- `docs/list/postclose_top5.csv`（当前盘后版）
- `docs/list/tail_top5.csv`（当前尾盘版）
- `src/data/top20_history.json`（历史快照）

`操作界面` 依赖本地 API 服务：

- 启动命令：`npm run api`
- 默认地址：`http://127.0.0.1:8787`

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
