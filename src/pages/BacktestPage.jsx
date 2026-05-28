import React, { useMemo } from 'react';
import { parseCsv } from '../lib/csv';
import historyTradesCsvText from '../../docs/list/short_t5_history_trades.csv?raw';
import historyMarkdownText from '../../docs/list/short_t5_history.md?raw';

function toNumber(value) {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function normalizeRows(rows) {
  return rows.map((row) => ({
    entryDate: row.entry_date || '',
    exitDate: row.exit_date || '',
    code: row.code || '',
    name: row.name || '',
    rank: toNumber(row.rank),
    shares: toNumber(row.shares),
    holdDays: toNumber(row.hold_days),
    buyAmount: toNumber(row.buy_amount),
    sellAmount: toNumber(row.sell_amount),
    retPct: toNumber(row.ret_pct),
    exitReason: row.exit_reason || '',
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

function formatMoney(value) {
  return Number.isFinite(value)
    ? `${value >= 0 ? '' : '-'}${Math.abs(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : '-';
}

export default function BacktestPage() {
  const rows = useMemo(() => normalizeRows(parseCsv(historyTradesCsvText)), []);
  const generatedAt = extractMeta(historyMarkdownText, '生成时间');
  const signalRange = extractMeta(historyMarkdownText, '信号区间');
  const marketEnd = extractMeta(historyMarkdownText, '行情截止');
  const t5Rule = extractMeta(historyMarkdownText, 'T+5口径');
  const sellRule = extractMeta(historyMarkdownText, '卖出规则');
  const budget = extractMeta(historyMarkdownText, '单票预算');
  const maxPositions = extractMeta(historyMarkdownText, '最大持仓数');
  const finalEquity = extractMeta(historyMarkdownText, '最终权益');
  const openCount = extractMeta(historyMarkdownText, '未平仓笔数');

  const stats = useMemo(() => {
    const valid = rows.filter((row) => Number.isFinite(row.retPct));
    const avgReturn = valid.length
      ? valid.reduce((sum, row) => sum + row.retPct, 0) / valid.length
      : null;
    const wins = valid.filter((row) => row.retPct > 0).length;
    const winRate = valid.length ? (wins / valid.length) * 100 : null;
    const realizedProfit = valid.reduce((sum, row) => {
      if (Number.isFinite(row.buyAmount) && Number.isFinite(row.sellAmount)) {
        return sum + (row.sellAmount - row.buyAmount);
      }
      return sum;
    }, 0);
    return {
      total: rows.length,
      avgReturn,
      winRate,
      realizedProfit,
    };
  }, [rows]);

  if (!rows.length) {
    return <section className="panel">未读取到历史 T+5 回测列表，请先生成 `docs/list/short_t5_history_trades.csv`。</section>;
  }

  return (
    <section className="content-stack">
      <article className="panel panel-intro">
        <div>
          <h2>盘后版 T+5 历史回测</h2>
          <p>按当前盘后版 Top5 顺序尝试买入，使用 10 万单票预算、最多 3 仓、整百股，以及涨停 / 单日回撤 5% / 跌破 5 日线的卖出规则回放历史交易。</p>
          <p className="panel-meta-line">{`生成时间：${generatedAt}`}</p>
          <p className="panel-meta-line">{`信号区间：${signalRange}`}</p>
          <p className="panel-meta-line">{`行情截止：${marketEnd}`}</p>
          <p className="panel-meta-line">{`T+5口径：${t5Rule}`}</p>
          <p className="panel-meta-line">{`单票预算：${budget}，最大持仓：${maxPositions}`}</p>
          <p className="panel-meta-line">{`卖出规则：${sellRule}`}</p>
          <p className="panel-meta-line">{`最终权益：${finalEquity}，未平仓：${openCount}`}</p>
        </div>
      </article>

      <article className="panel">
        <div className="stat-row">
          <div className="stat-card">
            <div className="stat-value">{stats.total}</div>
            <div className="stat-label">已平仓笔数</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{formatPct(stats.avgReturn)}</div>
            <div className="stat-label">平均收益率</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{formatPct(stats.winRate)}</div>
            <div className="stat-label">胜率</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{formatMoney(stats.realizedProfit)}</div>
            <div className="stat-label">已实现盈亏</div>
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>买入日</th>
                <th>卖出日</th>
                <th>代码</th>
                <th>名称</th>
                <th>排名</th>
                <th>股数</th>
                <th>买入金额</th>
                <th>收益率</th>
                <th>持有天数</th>
                <th>卖出原因</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.entryDate}-${row.code}-${row.exitDate}`}> 
                  <td>{row.entryDate || '-'}</td>
                  <td>{row.exitDate || '-'}</td>
                  <td>{row.code || '-'}</td>
                  <td>{row.name || '-'}</td>
                  <td>{row.rank ?? '-'}</td>
                  <td>{row.shares ?? '-'}</td>
                  <td>{formatMoney(row.buyAmount)}</td>
                  <td>{formatPct(row.retPct)}</td>
                  <td>{row.holdDays ?? '-'}</td>
                  <td>{row.exitReason || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
}