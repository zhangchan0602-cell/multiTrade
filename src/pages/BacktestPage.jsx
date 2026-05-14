import React, { useMemo } from 'react';
import { parseCsv } from '../lib/csv';
import historyCsvText from '../../docs/list/short_t3_history.csv?raw';
import historyMarkdownText from '../../docs/list/short_t3_history.md?raw';

function toNumber(value) {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function normalizeRows(rows) {
  return rows.map((row) => ({
    signalDate: row.signal_date || '',
    buyDate: row.buy_date || '',
    settleDate: row.settle_date || '',
    pickCount: toNumber(row.pick_count),
    avgReturnPct: toNumber(row.avg_return_pct),
    winRatePct: toNumber(row.win_rate_pct),
    top5Codes: row.top5_codes || '',
    pickReturns: row.pick_returns || '',
  }));
}

function extractMeta(markdown, label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matched = markdown?.match(new RegExp(`^- ${escaped}:\\s*(.+)$`, 'm'));
  return matched ? matched[1].trim() : '-';
}

function formatPct(value) {
  return Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '-';
}

export default function BacktestPage() {
  const rows = useMemo(() => normalizeRows(parseCsv(historyCsvText)), []);
  const generatedAt = extractMeta(historyMarkdownText, '生成时间');
  const replayRange = extractMeta(historyMarkdownText, '回放区间');

  const stats = useMemo(() => {
    const valid = rows.filter((row) => Number.isFinite(row.avgReturnPct));
    const avgReturn = valid.length
      ? valid.reduce((sum, row) => sum + row.avgReturnPct, 0) / valid.length
      : null;
    const winDays = valid.filter((row) => row.avgReturnPct > 0).length;
    const winRate = valid.length ? (winDays / valid.length) * 100 : null;
    return {
      total: rows.length,
      avgReturn,
      winRate,
    };
  }, [rows]);

  if (!rows.length) {
    return <section className="panel">未读取到历史 T+3 回放列表，请先生成 `docs/list/short_t3_history.csv`。</section>;
  }

  return (
    <section className="content-stack">
      <article className="panel panel-intro">
        <div>
          <h2>盘后版 T+3 回放</h2>
          <p>按当前盘后版打分与过滤逻辑，回放每个信号日的 Top5，并统计次日参考买入后的 T+3 收益。</p>
          <p className="panel-meta-line">{`生成时间：${generatedAt}`}</p>
          <p className="panel-meta-line">{`回放区间：${replayRange}`}</p>
        </div>
      </article>

      <article className="panel">
        <div className="stat-row">
          <div className="stat-card">
            <div className="stat-value">{stats.total}</div>
            <div className="stat-label">信号日数量</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{formatPct(stats.avgReturn)}</div>
            <div className="stat-label">日均 Top5 收益</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{formatPct(stats.winRate)}</div>
            <div className="stat-label">正收益天数占比</div>
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>信号日</th>
                <th>买入日</th>
                <th>结算日</th>
                <th>Top5数</th>
                <th>平均收益率</th>
                <th>胜率</th>
                <th>代码</th>
                <th>单票收益</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.signalDate}-${row.settleDate}`}>
                  <td>{row.signalDate}</td>
                  <td>{row.buyDate}</td>
                  <td>{row.settleDate}</td>
                  <td>{row.pickCount ?? '-'}</td>
                  <td>{formatPct(row.avgReturnPct)}</td>
                  <td>{formatPct(row.winRatePct)}</td>
                  <td>{row.top5Codes || '-'}</td>
                  <td>{row.pickReturns || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
}