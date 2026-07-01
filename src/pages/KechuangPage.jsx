import React, { useMemo, useState } from 'react';
import { calculateKechuangMarket } from '../lib/kechuang';
import { refreshKechuangIndex } from '../lib/opsApi';

function formatNumber(value, digits = 2) {
  return Number.isFinite(value) ? value.toFixed(digits) : '-';
}

function formatPct(value, digits = 2) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : '-';
}

function riskClass(probability) {
  if (probability >= 0.62) {
    return 'risk-high';
  }
  if (probability >= 0.42) {
    return 'risk-mid';
  }
  return 'risk-low';
}

function riskLabel(probability) {
  if (probability >= 0.62) {
    return '偏高';
  }
  if (probability >= 0.42) {
    return '中性';
  }
  return '偏低';
}

function MiniTrendChart({ points }) {
  const path = useMemo(() => {
    if (!points?.length) {
      return '';
    }

    const width = 620;
    const height = 180;
    const padding = 12;
    const closes = points.map((item) => item.close).filter(Number.isFinite);
    const min = Math.min(...closes);
    const max = Math.max(...closes);
    const range = max - min || 1;

    return points
      .map((item, index) => {
        const x = padding + (index / Math.max(1, points.length - 1)) * (width - padding * 2);
        const y = height - padding - ((item.close - min) / range) * (height - padding * 2);
        return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(' ');
  }, [points]);

  if (!points?.length) {
    return null;
  }

  const first = points[0];
  const latest = points.at(-1);

  return (
    <div className="kc-chart" aria-label="科创指数走势">
      <svg viewBox="0 0 620 180" role="img">
        <path className="kc-chart-grid" d="M12 45H608M12 90H608M12 135H608" />
        <path className="kc-chart-line" d={path} />
      </svg>
      <div className="kc-chart-meta">
        <span>{first.tradeDate}</span>
        <strong>{formatNumber(latest.close)}</strong>
        <span>{latest.tradeDate}</span>
      </div>
    </div>
  );
}

export default function KechuangPage() {
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [calculating, setCalculating] = useState(false);

  async function handleCalculate() {
    setCalculating(true);
    setError('');

    try {
      const payload = await refreshKechuangIndex();
      const nextResult = calculateKechuangMarket(payload.csvText);
      setResult({
        ...nextResult,
        refreshedAt: payload.updatedAt,
        fetchedTradeDate: payload.latestTradeDate,
      });
    } catch (err) {
      setResult(null);
      setError(err.message || '科创市场模型计算失败');
    } finally {
      setCalculating(false);
    }
  }

  return (
    <section className="content-stack">
      <article className="panel panel-intro kc-intro">
        <div>
          <h2>科创</h2>
          <p>基于本地科创50指数日线，计算当前回撤概率与本轮趋势压力位。</p>
          <p className="panel-meta-line">回撤概率采用历史相似市场状态模型：动量、波动、量能、均线偏离与20日回撤共同参与匹配。</p>
        </div>
        <button type="button" className="action-button action-primary" onClick={handleCalculate} disabled={calculating}>
          {calculating ? '拉取并计算...' : '计算'}
        </button>
      </article>

      {error && <article className="panel error">{error}</article>}

      {!result && !error && (
        <article className="panel kc-empty">
          <h3>模块一</h3>
          <p>点击“计算”后输出科创50指数模型下 3 / 5 / 10 天内回撤发生概率，并同步给出本次趋势的多个压力位。</p>
        </article>
      )}

      {result && (
        <>
          <article className="panel kc-overview">
            <div>
              <h3>当前科创市场状态</h3>
              <p className="panel-meta-line">
                {result.summary.indexName}（{result.summary.indexCode}），行情日期 {result.summary.tradeDate}，
                历史日线 {result.summary.dataPointCount} 条。
                {result.refreshedAt ? ` 数据已刷新至 ${result.fetchedTradeDate || result.summary.tradeDate}。` : ''}
              </p>
            </div>
            <div className="kc-stat-grid">
              <div className="meta-card">
                <div className="meta-key">指数点位</div>
                <div className="meta-value">{formatNumber(result.summary.close)}</div>
              </div>
              <div className="meta-card">
                <div className="meta-key">5日涨跌</div>
                <div className="meta-value">{formatPct(result.summary.ret5)}</div>
              </div>
              <div className="meta-card">
                <div className="meta-key">20日回撤</div>
                <div className="meta-value">{formatPct(result.summary.drawdown20)}</div>
              </div>
              <div className="meta-card">
                <div className="meta-key">5日量能</div>
                <div className="meta-value">{formatPct(result.summary.amountRatio5)}</div>
              </div>
            </div>
            <MiniTrendChart points={result.chart} />
          </article>

          <article className="panel">
            <div className="ops-card-head">
              <div>
                <h3>回撤概率</h3>
                <p className="panel-meta-line">输出为未来 N 个交易日内触发模型回撤阈值的概率。</p>
              </div>
              <span className="status-chip status-success">市场模型</span>
            </div>
            <div className="kc-prob-grid">
              {result.model.probabilities.map((item) => (
                <div key={item.horizon} className={`kc-prob-card ${riskClass(item.probability)}`}>
                  <span>{item.horizon}天内</span>
                  <strong>{formatPct(item.probability, 1)}</strong>
                  <small>
                    {riskLabel(item.probability)} · 阈值 {formatPct(item.threshold)}
                  </small>
                  <small>相似样本 {item.sampleCount}，中位最差回撤 {formatPct(item.medianWorstDrawdown)}</small>
                </div>
              ))}
            </div>
          </article>

          <article className="panel">
            <div>
              <h3>本次趋势压力位</h3>
              <p className="panel-meta-line">
                趋势起点 {result.pressure.trendStart.tradeDate}，起点 {formatNumber(result.pressure.trendStart.level)}，
                已运行 {result.pressure.trendStart.days} 个交易日，累计 {formatPct(result.pressure.trendStart.gain)}。
              </p>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>序号</th>
                    <th>压力位</th>
                    <th>距离当前</th>
                    <th>依据</th>
                    <th>触点</th>
                    <th>最近日期</th>
                  </tr>
                </thead>
                <tbody>
                  {result.pressure.levels.map((level, index) => (
                    <tr key={`${level.level}-${level.reason}`}>
                      <td>{index + 1}</td>
                      <td>{formatNumber(level.level)}</td>
                      <td>{formatPct(level.distance)}</td>
                      <td>{level.reason}</td>
                      <td>{level.touches}</td>
                      <td>{level.latestDate || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>

          <article className="panel kc-analogs">
            <h3>相似市场状态</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>相似距离</th>
                    <th>10日涨跌</th>
                    <th>20日回撤</th>
                    <th>5日量能</th>
                    <th>20日波动</th>
                  </tr>
                </thead>
                <tbody>
                  {result.model.analogs.map((item) => (
                    <tr key={item.tradeDate}>
                      <td>{item.tradeDate}</td>
                      <td>{formatNumber(item.distance, 3)}</td>
                      <td>{formatPct(item.ret10)}</td>
                      <td>{formatPct(item.drawdown20)}</td>
                      <td>{formatPct(item.amountRatio5)}</td>
                      <td>{formatPct(item.vol20)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>
        </>
      )}
    </section>
  );
}
